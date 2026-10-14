# 起平台后端. 前端由它同源托管 (包里的 frontend/dist), 故部署形态只有这一个进程.
#
# 虚拟环境**不在这里自动建**: 建环境要联网装依赖, 那是部署步骤 (见 docs/deployment.md),
# 不该藏在"起服务"里. 这里只保证路径与解释器是对的.

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    Write-Host "还没有虚拟环境. 先在这个目录下跑:" -ForegroundColor Yellow
    Write-Host '    python -m venv .venv'
    Write-Host '    .\.venv\Scripts\python.exe -m pip install -r requirements.deploy.txt'
    exit 1
}

Set-Location -Path (Join-Path $PSScriptRoot 'backend')

& $venvPython -m app.main
exit $LASTEXITCODE
