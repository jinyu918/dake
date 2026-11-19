param([switch]$Initialize)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONUTF8 = "1"
$python = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (!(Test-Path $python)) { throw "Install the Windows Python environment first." }
if (!(Get-Command docker -ErrorAction SilentlyContinue)) {
    foreach ($folder in @("$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin", "$env:ProgramFiles\Docker\Docker\resources\bin")) {
        if (Test-Path (Join-Path $folder "docker.exe")) { $env:PATH = "$folder;$env:PATH"; break }
    }
}
if (!(Get-Command docker -ErrorAction SilentlyContinue)) { throw "Install and open Docker Desktop first." }
& docker compose up -d --wait mysql mongo
if ($LASTEXITCODE -ne 0) { throw "MySQL or MongoDB did not start. Check Docker Desktop and ports 3307 / 27017." }
if ($Initialize) {
    & $python scripts/init_empty_database.py
    if ($LASTEXITCODE -ne 0) { throw "Database initialization stopped. Services were not started." }
}
# 两个独立窗口保留服务输出；关闭对应窗口即可停止服务。
Start-Process -FilePath $python -WorkingDirectory (Get-Location).Path -ArgumentList @("-m", "uvicorn", "dake.main:app", "--host", "127.0.0.1", "--port", "8001")
Start-Process powershell.exe -WorkingDirectory (Join-Path (Get-Location) "web") -ArgumentList @("-NoExit", "-Command", 'npm.cmd run dev')
