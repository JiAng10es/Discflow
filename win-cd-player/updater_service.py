# -*- coding: utf-8 -*-
"""
updater_service.py
Discflow GitHub Releases 应用内版本探测、流式下载校验与自更新替换服务：
1. 优先调用 GitHub Releases REST API，遭遇 403/429 限流时自动回退网页端 releases/latest 重定向探测
2. 流式下载安装包（实时回调进度）、校验长度与 PE 文件头（MZ）
3. 打包运行态生成静默批处理脚本等待退出并覆盖重启
"""

import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Callable, Optional

import requests

from library_service import log_runtime_event


def parse_version_tuple(ver_str: str) -> tuple:
    nums = [int(x) for x in re.findall(r"\d+", str(ver_str or ""))]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:4])


def resolve_update_target_exe(anchor_file: str) -> str:
    """定位当前运行实例对应的目标 Discflow.exe 路径"""
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(anchor_file)), "..", "Discflow.exe"))


def build_self_update_script(pid: int, staged_exe: str, target_exe: str) -> str:
    """生成静默自更新批处理脚本：等待旧进程退出 -> 覆盖目标 EXE -> 重启新版 -> 自删"""
    src = staged_exe.replace('"', "")
    dst = target_exe.replace('"', "")
    return (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        f'set "PID={int(pid)}"\r\n'
        f'set "SRC={src}"\r\n'
        f'set "DST={dst}"\r\n'
        "set /a wait_cnt=0\r\n"
        ":waitloop\r\n"
        'tasklist /FI "PID eq %PID%" 2>NUL | find /I "%PID%" >NUL\r\n'
        "if not errorlevel 1 (\r\n"
        "    set /a wait_cnt+=1\r\n"
        "    if %wait_cnt% GEQ 25 goto moveloop\r\n"
        "    timeout /t 1 /nobreak >NUL\r\n"
        "    goto waitloop\r\n"
        ")\r\n"
        "set /a tries=0\r\n"
        ":moveloop\r\n"
        'move /Y "%SRC%" "%DST%" >NUL 2>NUL\r\n'
        "if not errorlevel 1 goto launch\r\n"
        "set /a tries+=1\r\n"
        "if %tries% GEQ 15 goto finish\r\n"
        "timeout /t 1 /nobreak >NUL\r\n"
        "goto moveloop\r\n"
        ":launch\r\n"
        'start "" "%DST%"\r\n'
        ":finish\r\n"
        'del "%~f0" >NUL 2>&1\r\n'
    )


def check_for_updates_info(
    app_name: str,
    app_version: str,
    releases_api: str,
    releases_page: str,
    ignored_version: str,
    requests_mod=None,
) -> dict:
    req = requests_mod or requests
    res = {
        "ok": False,
        "currentVersion": app_version,
        "latestVersion": app_version,
        "hasUpdate": False,
        "ignored": False,
        "releaseUrl": releases_page,
        "downloadUrl": "",
        "assetSize": 0,
        "notes": "",
        "publishedAt": "",
        "error": "",
    }
    try:
        r = req.get(
            releases_api,
            headers={"Accept": "application/vnd.github+json", "User-Agent": f"{app_name}/{app_version}"},
            timeout=6,
        )
        if r.status_code == 200:
            data = r.json()
            raw_tag = str(data.get("tag_name") or "").strip()
            tag = raw_tag.lstrip("vV").strip()
            has_upd = bool(tag and parse_version_tuple(tag) > parse_version_tuple(app_version))

            download_url = ""
            asset_size = 0
            fallback_url = ""
            fallback_size = 0
            for ast in (data.get("assets") or []):
                if not isinstance(ast, dict):
                    continue
                aname = str(ast.get("name") or "").lower()
                aurl = str(ast.get("browser_download_url") or "")
                if aname.endswith(".exe") and aurl:
                    if "discflow" in aname:
                        download_url = aurl
                        asset_size = int(ast.get("size") or 0)
                        break
                    if not fallback_url:
                        fallback_url = aurl
                        fallback_size = int(ast.get("size") or 0)
            if not download_url:
                download_url = fallback_url
                asset_size = fallback_size
            if not download_url and has_upd and raw_tag:
                download_url = f"https://github.com/JiAng10es/Discflow/releases/download/{raw_tag}/Discflow.exe"

            res.update({
                "ok": True,
                "latestVersion": tag or app_version,
                "hasUpdate": has_upd,
                "ignored": bool(has_upd and tag == ignored_version),
                "releaseUrl": str(data.get("html_url") or releases_page),
                "downloadUrl": download_url,
                "assetSize": asset_size,
                "notes": str(data.get("body") or ""),
                "publishedAt": str(data.get("published_at") or ""),
            })
            log_runtime_event("UPDATE", f"API 检查成功 latest={tag or app_version} hasUpdate={has_upd}")
            return res

        log_runtime_event("UPDATE", f"GitHub API 返回 HTTP {r.status_code}，尝试网页重定向兜底", level="WARNING")
        web_r = req.get(
            f"{releases_page}/latest",
            headers={"User-Agent": f"{app_name}/{app_version}"},
            allow_redirects=True,
            timeout=6,
        )
        final_url = str(getattr(web_r, "url", "") or "")
        if web_r.status_code == 200 and "/releases/tag/" in final_url:
            raw_tag = final_url.rstrip("/").rsplit("/", 1)[-1].strip()
            tag = raw_tag.lstrip("vV").strip()
            has_upd = bool(tag and parse_version_tuple(tag) > parse_version_tuple(app_version))
            log_runtime_event("UPDATE", f"网页兜底检查成功 latest={tag or app_version} hasUpdate={has_upd}")
            download_url = f"https://github.com/JiAng10es/Discflow/releases/download/{raw_tag}/Discflow.exe"
            asset_size = 0
            if has_upd:
                try:
                    hr = req.head(
                        download_url,
                        headers={"User-Agent": f"{app_name}/{app_version}"},
                        allow_redirects=True,
                        timeout=4,
                    )
                    if hr.status_code == 200:
                        asset_size = int(hr.headers.get("Content-Length") or 0)
                except Exception:
                    pass
            res.update({
                "ok": True,
                "latestVersion": tag or app_version,
                "hasUpdate": has_upd,
                "ignored": bool(has_upd and tag == ignored_version),
                "releaseUrl": final_url,
                "downloadUrl": download_url if has_upd else "",
                "assetSize": asset_size,
            })
            return res

        res["error"] = f"HTTP {r.status_code}"
        return res
    except Exception as e:
        res["error"] = str(e)
    return res


def download_and_stage_update(
    app_name: str,
    app_version: str,
    download_url: str,
    expected_version: str,
    expected_size: int,
    target_exe: str,
    is_cancelled: Callable[[], bool],
    on_state: Callable[..., None],
    on_apply_staged: Callable[[str, str], None],
    requests_mod=None,
):
    req = requests_mod or requests
    target_dir = os.path.dirname(target_exe) or tempfile.gettempdir()
    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception:
        target_dir = tempfile.gettempdir()
    tmp_path = os.path.join(target_dir, "Discflow.exe.new.tmp")
    staged_path = os.path.join(target_dir, "Discflow.exe.update.exe")

    try:
        log_runtime_event("UPDATE", f"开始下载更新 v{expected_version} url={download_url}")
        with req.get(
            download_url,
            stream=True,
            timeout=(8, 30),
            headers={"User-Agent": f"{app_name}/{app_version}"},
        ) as resp:
            resp.raise_for_status()
            total_bytes = int(resp.headers.get("Content-Length") or expected_size or 0)
            downloaded = 0
            with open(tmp_path, "wb") as f_out:
                for chunk in resp.iter_content(chunk_size=65536):
                    if is_cancelled():
                        raise InterruptedError("下载已取消")
                    if not chunk:
                        continue
                    f_out.write(chunk)
                    downloaded += len(chunk)
                    pct = round((downloaded / total_bytes) * 100.0, 1) if total_bytes > 0 else 0.0
                    dl_mb = downloaded / (1024 * 1024)
                    if total_bytes > 0:
                        tot_mb = total_bytes / (1024 * 1024)
                        txt = f"正在下载 v{expected_version}: {pct:.0f}% ({dl_mb:.1f} MB / {tot_mb:.1f} MB)"
                    else:
                        txt = f"正在下载 v{expected_version}: {dl_mb:.1f} MB"
                    on_state(
                        status="downloading",
                        progressPct=min(99.0, pct),
                        downloadedBytes=downloaded,
                        totalBytes=total_bytes,
                        statusText=txt,
                    )

        if downloaded <= 0 or (total_bytes > 0 and downloaded != total_bytes):
            raise RuntimeError("更新包下载不完整，请重试")

        with open(tmp_path, "rb") as f_chk:
            magic = f_chk.read(2)
        if magic != b"MZ":
            raise RuntimeError("下载的更新包格式校验失败（非有效 Windows 可执行文件）")

        os.replace(tmp_path, staged_path)
        log_runtime_event("UPDATE", f"更新包校验通过并已暂存 size={downloaded} target={target_exe}")
        on_apply_staged(staged_path, target_exe)
    except InterruptedError:
        log_runtime_event("UPDATE", f"更新下载被取消 v{expected_version}", level="WARNING")
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        on_state(status="cancelled", statusText="下载已取消", error="")
    except Exception as e:
        log_runtime_event("UPDATE", f"更新下载失败 v{expected_version} err={e}", level="ERROR")
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        on_state(status="error", statusText="下载失败", error=str(e))


def apply_staged_update_and_restart(
    staged_exe: str,
    target_exe: str,
    on_state: Callable[..., None],
    on_exit_app: Callable[[], None],
    subprocess_mod=None,
    time_mod=None,
):
    """将已校验通过的新版本覆盖到当前目标路径；打包运行时启动静默替换脚本并自动重启"""
    sp = subprocess_mod or subprocess
    tm = time_mod or time
    is_frozen = bool(getattr(sys, "frozen", False))
    if not is_frozen:
        os.makedirs(os.path.dirname(target_exe), exist_ok=True)
        os.replace(staged_exe, target_exe)
        on_state(
            status="completed",
            progressPct=100.0,
            statusText=f"更新包已下载并覆盖：{target_exe}",
            filePath=target_exe,
            error="",
        )
        return

    on_state(
        status="installing",
        progressPct=100.0,
        statusText="下载完成，正在覆盖安装并重启...",
        filePath=target_exe,
        error="",
    )
    bat_path = os.path.join(tempfile.gettempdir(), f"discflow_updater_{os.getpid()}.bat")
    script_content = build_self_update_script(os.getpid(), staged_exe, target_exe)
    with open(bat_path, "w", encoding="utf-8") as f_bat:
        f_bat.write(script_content)
    creation_flags = getattr(sp, "CREATE_NO_WINDOW", 0x08000000)
    sp.Popen(["cmd.exe", "/c", bat_path], creationflags=creation_flags, close_fds=True)
    tm.sleep(0.35)
    on_exit_app()
