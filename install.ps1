# 安装 modelscope-magicube-checkin Skill 到用户级 skills 目录
# 用法: ./install.ps1 [-DestRoot <skills 目录>]
param(
    [string]$DestRoot = (Join-Path $HOME ".workbuddy\skills")
)

$ErrorActionPreference = "Stop"

$SrcDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillName = "modelscope-magicube-checkin"
$SrcSkill = Join-Path $SrcDir $SkillName
$Dest = Join-Path $DestRoot $SkillName

if (-not (Test-Path (Join-Path $SrcSkill "SKILL.md"))) {
    Write-Error "在 $SrcSkill 下找不到 SKILL.md，请在仓库根目录运行。"
    exit 1
}

if (-not (Test-Path $DestRoot)) {
    New-Item -ItemType Directory -Path $DestRoot -Force | Out-Null
}

if (Test-Path $Dest) {
    # 备份到 skills 目录之外，避免残留目录被当成损坏的 skill 加载
    $backupRoot = Join-Path (Split-Path -Parent $DestRoot) "skill-backups"
    if (-not (Test-Path $backupRoot)) {
        New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    }
    $stamp = Get-Date -Format "yyyyMMddHHmmss"
    $backup = Join-Path $backupRoot "$SkillName.$stamp"
    Write-Host "已存在旧版本，备份到：$backup"
    Move-Item -Path $Dest -Destination $backup
}

Copy-Item -Path $SrcSkill -Destination $DestRoot -Recurse
Write-Host "已安装到：$Dest"

# 找一个可用的 python 做自检
$py = $null
foreach ($c in @("python", "python3", "py")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source; break }
}

if ($py) {
    $script = Join-Path $Dest "scripts\ms_checkin.py"
    Write-Host ""
    Write-Host "--- 自检（只查登录态与余额，不领取）---"
    & $py $script --status
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "自检未通过，常见原因："
        Write-Host "  1) 还没登录过 → 跑：& $py `"$script`" --login"
        Write-Host "  2) 找不到 Chrome → 装 Chrome for Testing"
        Write-Host "  3) 页面没渲染完 → 跑 --probe 看实际返回"
    }
    else {
        Write-Host ""
        Write-Host "自检通过。日常领取："
        Write-Host "  & $py `"$script`""
    }
}
else {
    Write-Host ""
    Write-Host "未找到 python/python3/py，请先安装 Python 3.8+。"
}

Write-Host ""
Write-Host "提示：首次使用需人工登录一次 → & $py `"$script`" --login"
