# ═══════════════════════════════════════════════════════════════
# 后端打包脚本
# 名称: T2605294-Agent跟我起飞-【A13】基于AI的大学生职业规划智能体-源码（后端）
# ═══════════════════════════════════════════════════════════════

$ErrorActionPreference = "Stop"

# 配置
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $ProjectRoot "backend"
$OutputDir = Join-Path $ProjectRoot "output"
$ArchiveName = "T2605294-Agent跟我起飞-【A13】基于AI的大学生职业规划智能体-源码（后端）"
$ArchivePath = Join-Path $OutputDir "$ArchiveName.zip"

# 创建输出目录
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

Write-Host "╔══════════════════════════════════════════════════════════════╗"
Write-Host "║          后端项目打包                                     ║"
Write-Host "╚══════════════════════════════════════════════════════════════╝"
Write-Host ""
Write-Host "源目录: $BackendDir"
Write-Host "输出:   $ArchivePath"
Write-Host ""

# 验证源目录
if (-not (Test-Path $BackendDir)) {
    Write-Error "后端目录不存在: $BackendDir"
    exit 1
}

# 创建临时打包目录
$TempDir = Join-Path $env:TEMP "cpa-backend-package"
if (Test-Path $TempDir) {
    Remove-Item -Path $TempDir -Recurse -Force
}
New-Item -ItemType Directory -Path $TempDir | Out-Null

# 复制需要打包的文件
Write-Host "正在复制文件..."
Copy-Item -Path (Join-Path $BackendDir "app") -Destination (Join-Path $TempDir "app") -Recurse
Copy-Item -Path (Join-Path $BackendDir "scripts") -Destination (Join-Path $TempDir "scripts") -Recurse
Copy-Item -Path (Join-Path $BackendDir "run.py") -Destination $TempDir
Copy-Item -Path (Join-Path $BackendDir "requirements.txt") -Destination $TempDir
Copy-Item -Path (Join-Path $BackendDir "environment.yml") -Destination $TempDir
Copy-Item -Path (Join-Path $BackendDir ".env.example") -Destination (Join-Path $TempDir ".env")
Copy-Item -Path (Join-Path $BackendDir "Dockerfile") -Destination $TempDir -ErrorAction SilentlyContinue
Copy-Item -Path (Join-Path $BackendDir ".dockerignore") -Destination $TempDir -ErrorAction SilentlyContinue

# 删除任何可能残留的缓存
Get-ChildItem -Path $TempDir -Filter "__pycache__" -Recurse -Directory | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path $TempDir -Filter "*.pyc" -Recurse -File | Remove-Item -Force -ErrorAction SilentlyContinue

Write-Host "正在打包..."
Compress-Archive -Path (Join-Path $TempDir "*") -DestinationPath $ArchivePath -Force

# 清理临时目录
Remove-Item -Path $TempDir -Recurse -Force

# 输出结果
$Size = (Get-Item $ArchivePath).Length / 1MB
Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════════════╗"
Write-Host "║                     打包完成                               ║"
Write-Host "╚══════════════════════════════════════════════════════════════╝"
Write-Host ""
Write-Host "文件: $ArchiveName.zip"
Write-Host "大小: $([math]::Round($Size, 2)) MB"
Write-Host "路径: $ArchivePath"
Write-Host ""
