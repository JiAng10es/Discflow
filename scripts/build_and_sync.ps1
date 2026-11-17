<#
.SYNOPSIS
    Discflow 一键测试、打包、双盘同步与版本一致性脚本
.DESCRIPTION
    1. Python 语法编译检查 + 前端 JS 语法检查
    2. 一键全量单元测试回归（含无头 Edge 真机冒烟测试与本地诊断日志测试）
    3. 校验/同步主程序 APP_VERSION 与官网 website/index.html（可选 -SyncWebsite）
    4. PyInstaller 打包、安全停止旧进程、同步至 F 盘与 E 盘并校验 SHA256
    5. 可选本地 Git commit（不主动 push/发布，除非显式传入 -PublishRelease）
#>
param(
    [switch]$SkipBuild,
    [switch]$SyncWebsite,
    [string]$CommitMessage = "",
    [switch]$PublishRelease,
    [string]$ReleaseNotes = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$RepoRoot = Split-Path -Parent $PSScriptRoot
$AppDir = Join-Path $RepoRoot "win-cd-player"
$WebsiteHtml = Join-Path $RepoRoot "website\index.html"
$TargetF = Join-Path $RepoRoot "Discflow.exe"
$TargetE = "E:\Discflow\Discflow.exe"

function Write-Step([string]$Msg) {
    Write-Host "`n==> $Msg" -ForegroundColor Cyan
}

# 读取主程序当前版本号
$MainPyPath = Join-Path $AppDir "main.py"
$MainPyContent = Get-Content -Path $MainPyPath -Raw -Encoding UTF8
if ($MainPyContent -match 'APP_VERSION\s*=\s*"([^"]+)"') {
    $AppVersion = $Matches[1]
} else {
    throw "无法从 main.py 解析 APP_VERSION"
}
Write-Host "当前主程序版本: v$AppVersion" -ForegroundColor Green

# 1. 静态语法检查
Write-Step "1/5 执行前后端静态语法检查 (py_compile + node --check)"
Push-Location $AppDir
try {
    $CorePyFiles = Get-ChildItem -Filter "*.py" | Where-Object { $_.Name -notlike "test_*" } | Select-Object -ExpandProperty Name
    python -m py_compile @CorePyFiles
    if ($LASTEXITCODE -ne 0) { throw "Python py_compile 检查失败" }

    node --check ui\app.js
    if ($LASTEXITCODE -ne 0) { throw "前端 ui/app.js 语法检查失败" }

    # 2. 一键全量回归测试（含无头 Edge 真机冒烟 + 日志沙盒测试）
    Write-Step "2/5 执行全量回归测试 (unittest discover)"
    python -m unittest discover -s . -p "test_*.py"
    if ($LASTEXITCODE -ne 0) { throw "全量回归测试未通过，已中止后续打包与同步！" }
} finally {
    Pop-Location
}

# 3. 官网版本号同步 / 一致性提示
Write-Step "3/5 检查主程序与官网版本号一致性"
if (Test-Path $WebsiteHtml) {
    $HtmlText = Get-Content -Path $WebsiteHtml -Raw -Encoding UTF8
    if ($SyncWebsite) {
        $UpdatedHtml = $HtmlText -replace 'v\d+\.\d+\.\d+', "v$AppVersion"
        if ($UpdatedHtml -ne $HtmlText) {
            Set-Content -Path $WebsiteHtml -Value $UpdatedHtml -Encoding UTF8 -NoNewline
            Write-Host "已将 website/index.html 中的版本号同步为 v$AppVersion" -ForegroundColor Green
        } else {
            Write-Host "website/index.html 版本号已是 v$AppVersion" -ForegroundColor DarkGray
        }
    } else {
        if ($HtmlText -notmatch [regex]::Escape("v$AppVersion")) {
            Write-Warning "检测到 website/index.html 尚未更新至 v$AppVersion（可加 -SyncWebsite 自动同步）"
        } else {
            Write-Host "website/index.html 版本号与主程序一致 (v$AppVersion)" -ForegroundColor Green
        }
    }
}

# 4. PyInstaller 打包与双盘同步
if (-not $SkipBuild) {
    Write-Step "4/5 执行 PyInstaller 打包并同步双盘可执行文件"
    Push-Location $AppDir
    try {
        pyinstaller --noconfirm Discflow.spec
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }
    } finally {
        Pop-Location
    }

    $BuiltExe = Join-Path $AppDir "dist\Discflow.exe"
    if (-not (Test-Path $BuiltExe)) {
        throw "未找到打包产物: $BuiltExe"
    }

    # 安全停止正在运行的 Discflow 进程避免文件占用
    Get-Process -Name "Discflow" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Milliseconds 400

    Copy-Item -Path $BuiltExe -Destination $TargetF -Force
    $BuiltHash = (Get-FileHash -Path $BuiltExe -Algorithm SHA256).Hash
    $HashF = (Get-FileHash -Path $TargetF -Algorithm SHA256).Hash
    if ($BuiltHash -ne $HashF) {
        throw "F 盘同步后 SHA256 校验不一致！"
    }
    Write-Host "已同步至 $TargetF (SHA256: $HashF)" -ForegroundColor Green

    $DirE = Split-Path -Parent $TargetE
    if (Test-Path $DirE) {
        Copy-Item -Path $BuiltExe -Destination $TargetE -Force
        $HashE = (Get-FileHash -Path $TargetE -Algorithm SHA256).Hash
        if ($BuiltHash -ne $HashE) {
            throw "E 盘同步后 SHA256 校验不一致！"
        }
        Write-Host "已同步至 $TargetE (SHA256: $HashE)" -ForegroundColor Green
    } else {
        Write-Warning "未检测到目标目录 $DirE，已跳过 E 盘同步。"
    }
} else {
    Write-Step "4/5 已跳过 PyInstaller 打包 (-SkipBuild)"
}

# 5. 本地 Git 留痕 & 可控发布
Write-Step "5/5 PRD 记录检查、Git 本地留痕与发布状态"
Push-Location $RepoRoot
try {
    $ChangedFiles = git status --porcelain
    $TouchedAppCode = $ChangedFiles | Where-Object { $_ -match 'win-cd-player/' }
    $TouchedPrd = $ChangedFiles | Where-Object { $_ -match 'PRD\.md$' }
    if ($TouchedAppCode -and -not $TouchedPrd) {
        Write-Warning "检测到 win-cd-player/ 有代码改动，但 PRD.md 尚未同步记录本轮重要变更！"
    }

    if ($CommitMessage) {
        git add -A
        $StatusOut = git status --porcelain
        if ($StatusOut) {
            git commit -m $CommitMessage
            if ($LASTEXITCODE -ne 0) { throw "git commit 失败" }
            Write-Host "已完成本地 Git 提交: $CommitMessage" -ForegroundColor Green
        } else {
            Write-Host "工作区无新增改动，无需提交。" -ForegroundColor DarkGray
        }
    } else {
        Write-Host "未指定 -CommitMessage，跳过自动提交（仅保留本地工作区状态）。" -ForegroundColor DarkGray
    }

    if ($PublishRelease) {
        $Tag = "v$AppVersion"
        Write-Host "正在推送主分支并发布 GitHub Release $Tag ..." -ForegroundColor Yellow
        git push origin main
        if ($LASTEXITCODE -ne 0) { throw "git push 失败" }

        $NoteArg = if ($ReleaseNotes) { $ReleaseNotes } else { "Discflow $Tag release" }
        gh release create $Tag $TargetF --title "Discflow $Tag" --notes $NoteArg
        if ($LASTEXITCODE -ne 0) { throw "gh release create 失败" }
        Write-Host "GitHub Release $Tag 发布完成！" -ForegroundColor Green
    } else {
        Write-Host "发布节点由用户手动确认，本次未执行 git push / GitHub Release。" -ForegroundColor DarkGray
    }
} finally {
    Pop-Location
}

Write-Host "`n全部流程执行完毕！" -ForegroundColor Green
