# Stage the ISO payload and build dbnorm.iso. Run from the project root.
# Requires oscdimg (Windows ADK). Re-run after any code or wheels change.
$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ROOT

$STAGE = Join-Path $ROOT "dbnorm_package"
Remove-Item -Recurse -Force $STAGE -ErrorAction SilentlyContinue
New-Item -ItemType Directory $STAGE | Out-Null
Copy-Item backend (Join-Path $STAGE "backend") -Recurse
Copy-Item frontend (Join-Path $STAGE "frontend") -Recurse
Copy-Item wheels (Join-Path $STAGE "wheels") -Recurse
Copy-Item requirements.txt -Destination $STAGE
Copy-Item docs\README.md (Join-Path $STAGE "README.md")
Copy-Item docs (Join-Path $STAGE "docs") -Recurse
Copy-Item packaging\INSTALL.txt (Join-Path $STAGE "INSTALL.txt")
Get-ChildItem $STAGE -Recurse -Include __pycache__ -Directory | Remove-Item -Recurse -Force
Get-ChildItem $STAGE -Recurse -Include *.pyc, *.sqlite, *.db, *.log -File | Remove-Item -Force
Remove-Item (Join-Path $STAGE "backend\logs") -Recurse -Force -ErrorAction SilentlyContinue

$ISO = Join-Path $ROOT "dbnorm.iso"
Remove-Item $ISO -Force -ErrorAction SilentlyContinue
oscdimg -m -j1 -lDBNORM $STAGE $ISO
Get-FileHash $ISO -Algorithm SHA256
