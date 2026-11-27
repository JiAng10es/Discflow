# -*- coding: utf-8 -*-
"""
updater_service.py
Discflow GitHub Releases 应用内版本探测、流式下载校验与自更新替换服务：
1. 优先调用 GitHub Releases REST API，遭遇 403/429 限流时自动回退网页端 releases/latest 重定向探测
2. 流式下载安装包（实时回调进度）、校验长度与 PE 文件头（MZ）
3. 打包运行态生成静默批处理脚本等待退出并覆盖重启
"""

import errno
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Callable, Optional

import requests

from library_service import log_runtime_event


def parse_version_tuple(ver_str: str) -> tuple:
    raw = str(ver_str or "").strip().lstrip("vV")
    raw = raw.split("+", 1)[0].strip()
    if "-" in raw:
        core_part, pre_part = raw.split("-", 1)
    else:
        core_part, pre_part = raw, ""
    nums = [int(x) for x in re.findall(r"\d+", core_part)]
    while len(nums) < 4:
        nums.append(0)
    core_tuple = tuple(nums[:4])
    is_stable = 1 if not pre_part.strip() else 0
    pre_tokens = []
    if pre_part.strip():
        for tok in pre_part.strip().split("."):
            if not tok:
                continue
            if tok.isdigit():
                pre_tokens.append((0, int(tok)))
            else:
                pre_tokens.append((1, tok.lower()))
    return core_tuple + (is_stable, tuple(pre_tokens))


def resolve_update_target_exe(anchor_file: str) -> str:
    """定位当前运行实例对应的目标 Discflow.exe 路径"""
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(anchor_file)), "..", "Discflow.exe"))


def build_self_update_script(pid: int, staged_exe: str, target_exe: str) -> str:
    """生成静默自更新批处理脚本：等待旧进程退出 -> 覆盖目标 EXE -> 重启新版 -> 自删"""
    src = staged_exe.replace('"', "").replace("%", "%%")
    dst = target_exe.replace('"', "").replace("%", "%%")
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
        "    ping -n 2 127.0.0.1 >NUL\r\n"
        "    goto waitloop\r\n"
        ")\r\n"
        "set /a tries=0\r\n"
        ":moveloop\r\n"
        'move /Y "%SRC%" "%DST%" >NUL 2>NUL\r\n'
        "if not errorlevel 1 goto launch\r\n"
        "set /a tries+=1\r\n"
        "if %tries% GEQ 15 goto finish\r\n"
        "ping -n 2 127.0.0.1 >NUL\r\n"
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
    base_releases_page = str(releases_page or "").split("?", 1)[0].split("#", 1)[0].rstrip("/")
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
    api_err_msg = ""
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
            if not download_url and has_upd and raw_tag and base_releases_page:
                download_url = f"{base_releases_page}/download/{raw_tag}/Discflow.exe"

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

        api_err_msg = f"HTTP {r.status_code}"
        log_runtime_event("UPDATE", f"GitHub API 返回 HTTP {r.status_code}，尝试网页重定向兜底", level="WARNING")
    except Exception as api_exc:
        api_err_msg = str(api_exc)
        log_runtime_event("UPDATE", f"GitHub API 请求异常（{api_exc}），尝试网页重定向兜底", level="WARNING")

    try:
        web_r = req.get(
            f"{base_releases_page}/latest",
            headers={"User-Agent": f"{app_name}/{app_version}"},
            allow_redirects=True,
            timeout=6,
        )
        raw_final_url = str(getattr(web_r, "url", "") or "")
        final_url = raw_final_url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        if web_r.status_code == 200 and "/releases/tag/" in final_url:
            raw_tag = final_url.rsplit("/", 1)[-1].strip()
            tag = raw_tag.lstrip("vV").strip()
            has_upd = bool(tag and parse_version_tuple(tag) > parse_version_tuple(app_version))
            log_runtime_event("UPDATE", f"网页兜底检查成功 latest={tag or app_version} hasUpdate={has_upd}")
            cand_url = f"{base_releases_page}/download/{raw_tag}/Discflow.exe" if base_releases_page else ""
            download_url = ""
            asset_size = 0
            if has_upd and cand_url:
                try:
                    hr = req.head(
                        cand_url,
                        headers={"User-Agent": f"{app_name}/{app_version}"},
                        allow_redirects=True,
                        timeout=4,
                    )
                    if hr.status_code == 200:
                        download_url = cand_url
                        asset_size = int(hr.headers.get("Content-Length") or 0)
                except Exception:
                    pass
            res.update({
                "ok": True,
                "latestVersion": tag or app_version,
                "hasUpdate": has_upd,
                "ignored": bool(has_upd and tag == ignored_version),
                "releaseUrl": final_url,
                "downloadUrl": download_url,
                "assetSize": asset_size,
            })
            return res

        res["error"] = api_err_msg or f"HTTP {web_r.status_code}"
        return res
    except Exception as e:
        res["error"] = api_err_msg or str(e)
    return res


def _ensure_writable_dir(preferred_dir: str) -> str:
    candidate = preferred_dir or tempfile.gettempdir()
    try:
        os.makedirs(candidate, exist_ok=True)
        probe = os.path.join(candidate, f".discflow_write_probe_{os.getpid()}_{threading_ident()}")
        with open(probe, "wb") as fp:
            fp.write(b"1")
        os.remove(probe)
        return candidate
    except Exception:
        return tempfile.gettempdir()


def threading_ident() -> int:
    import threading
    return threading.get_ident()


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
    target_dir = _ensure_writable_dir(os.path.dirname(target_exe) or tempfile.gettempdir())
    tmp_path = os.path.join(target_dir, "Discflow.exe.new.tmp")
    staged_path = os.path.join(target_dir, "Discflow.exe.update.exe")

    try:
        log_runtime_event("UPDATE", f"开始下载更新 v{expected_version} url={download_url}")
        with req.get(
            download_url,
            stream=True,
            timeout=(8, 30),
            headers={"User-Agent": f"{app_name}/{app_version}", "Accept-Encoding": "identity"},
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

        if is_cancelled():
            raise InterruptedError("下载已取消")

        if downloaded <= 0 or (total_bytes > 0 and downloaded != total_bytes):
            raise RuntimeError("更新包下载不完整，请重试")

        with open(tmp_path, "rb") as f_chk:
            header_bytes = f_chk.read(4096)
        if len(header_bytes) < 2 or header_bytes[:2] != b"MZ":
            raise RuntimeError("下载的更新包格式校验失败（非有效 Windows 可执行文件）")
        if downloaded >= 4096:
            if len(header_bytes) < 64:
                raise RuntimeError("下载的更新包格式校验失败（PE 头不完整）")
            e_lfanew = int.from_bytes(header_bytes[60:64], "little")
            if e_lfanew < 64 or e_lfanew + 4 > downloaded:
                raise RuntimeError("下载的更新包格式校验失败（无效的 PE 偏移）")
            with open(tmp_path, "rb") as f_pe:
                f_pe.seek(e_lfanew)
                pe_sig = f_pe.read(4)
            if pe_sig != b"PE\x00\x00":
                raise RuntimeError("下载的更新包格式校验失败（缺少 PE 签名）")

        os.replace(tmp_path, staged_path)
        log_runtime_event("UPDATE", f"更新包校验通过并已暂存 size={downloaded} target={target_exe}")
        on_apply_staged(staged_path, target_exe)
    except InterruptedError:
        log_runtime_event("UPDATE", f"更新下载被取消 v{expected_version}", level="WARNING")
        for p in (tmp_path, staged_path):
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except Exception:
                pass
        on_state(status="cancelled", statusText="下载已取消", error="")
    except Exception as e:
        log_runtime_event("UPDATE", f"更新下载失败 v{expected_version} err={e}", level="ERROR")
        for p in (tmp_path, staged_path):
            try:
                if os.path.isfile(p):
                    os.remove(p)
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
        try:
            os.replace(staged_exe, target_exe)
        except OSError as ex:
            if ex.errno == errno.EXDEV or os.path.abspath(staged_exe).lower() != os.path.abspath(target_exe).lower():
                shutil.move(staged_exe, target_exe)
            else:
                raise
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
    if "&" in bat_path or "^" in bat_path:
        sp.Popen(f'cmd.exe /c ""{bat_path}""', creationflags=creation_flags, close_fds=True)
    else:
        sp.Popen(["cmd.exe", "/c", bat_path], creationflags=creation_flags, close_fds=True)
    tm.sleep(0.35)
    on_exit_app()
