# Build the offline wheels folder (internet-connected Windows machine).
# Run from the project root:  powershell -ExecutionPolicy Bypass -File packaging/build_wheels.ps1
$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ROOT

& python --version
Remove-Item -Recurse -Force wheels -ErrorAction SilentlyContinue
New-Item -ItemType Directory wheels | Out-Null
python -m pip download -r requirements.txt `
  --dest wheels `
  --only-binary=:all: `
  --platform win_amd64 `
  --python-version 3.12 `
  --implementation cp `
  --abi cp312
Get-ChildItem wheels | Select-Object Name, Length | Out-File wheels.txt -Encoding utf8
Write-Output "wheels: $((Get-ChildItem wheels).Count) files, list in wheels.txt"
