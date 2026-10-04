# Offline Packaging
 
How the tool gets onto vm1, which has **no internet**. Everything is downloaded
on an internet-connected Windows machine, packaged, transferred, and installed
once.
 
Target: Windows 64-bit, Python **3.12.10**, wheels tagged `win_amd64` / `cp312`.
 
---
 
## 1. What ships
 
| Item | Shipped | Notes |
|---|---|---|
| Source code | Yes | `backend/`, `frontend/`, `README.md`, `docs/` |
| Runtime wheels | Yes | Everything in `requirements.txt` plus their dependencies |
| Dev-only packages | **No** | pytest, httpx, ruff/black stay on the build machine |
| Test databases and scripts | Optional | Not needed on vm1 |
| Browser binaries | Not needed | No headless browser in this tool |
| Oracle Instant Client | Not needed | python-oracledb runs in thin mode |
 
---
 
## 2. Runtime dependencies
 
`requirements.txt` (top-level only; the full wheel set is resolved by pip):
 
```
fastapi
uvicorn
sqlalchemy
pymysql
cryptography
pg8000
oracledb
reportlab
```
 
Why these:
 
| Package | Purpose | Offline note |
|---|---|---|
| fastapi, uvicorn | API and static file server | Use plain `uvicorn` (no `[standard]` extra): avoids optional C extensions |
| sqlalchemy | Metadata through `inspect()` | May pull `greenlet` and `typing_extensions`; both have `cp312` `win_amd64` wheels |
| pymysql | MySQL driver | Pure Python |
| cryptography | MySQL 8 default auth (`caching_sha2_password`) and Oracle | Compiled wheel; must match `cp312` / `win_amd64` (abi3 wheel is fine) |
| pg8000 | PostgreSQL driver | Pure Python (pulls `scramp`, `asn1crypto`, `python-dateutil`) |
| oracledb | Oracle driver, thin mode | Needs `cryptography` |
| reportlab | PDF generation | Pulls `pillow` (compiled wheel) and `charset-normalizer` |
 
Pin exact versions in `requirements.txt` once the wheel set is verified, so the
build is reproducible. (FastAPI also brings `pydantic`, `pydantic-core`,
`starlette`, `anyio` and others; `pip download` fetches them.)
 
Dev-only list goes in `requirements-dev.txt` and is never downloaded for shipping.
 
---
 
## 3. Build the wheel folder (internet-connected Windows machine)
 
Use a Python 3.12 environment, and match the target platform explicitly:
 
```powershell
cd db-norm-tool
Remove-Item -Recurse -Force wheels -ErrorAction SilentlyContinue
pip download -r requirements.txt `
  --dest wheels `
  --only-binary=:all: `
  --platform win_amd64 `
  --python-version 3.12 `
  --implementation cp `
  --abi cp312
```
 
Notes:
- `--only-binary=:all:` refuses source distributions. If a package has no wheel for
  `cp312` / `win_amd64`, the command fails here, on a machine that still has
  internet, which is exactly where you want to find out.
- If a pure-Python dependency is published only as `py3-none-any`, that is fine; it is a wheel.
- Run the command again after any change to `requirements.txt`.
- Keep the resulting file list (`dir wheels > wheels.txt`) in the repo for review.
Python 3.12 has been the safe choice on this network because newer versions
sometimes lack prebuilt wheels for C-extension packages, and there is no
internet to compile from source.
 
---
 
## 4. Verify before shipping (clean venv, no index)
 
On the build machine, prove the wheels folder is complete:
 
```powershell
python -m venv venv_verify
venv_verify\Scripts\activate
pip install --no-index --find-links wheels -r requirements.txt
python -c "import fastapi, sqlalchemy, pymysql, pg8000, oracledb, reportlab, cryptography; print('imports ok')"
```
 
`--no-index` makes pip refuse to contact any package index, so a missing wheel fails
immediately. For a stricter test, disconnect the network and run a scan against a
local test database from this venv. Delete `venv_verify` afterwards.
 
---
 
## 5. Build the ISO
 
Same shipping path as the other tools. Stage a clean folder first:
 
```
dbnorm_package/
  backend/
  frontend/
  wheels/
  requirements.txt
  README.md
  docs/
  INSTALL.txt
```
 
Do **not** include `venv`, `__pycache__`, `*.pyc`, test databases, logs, the
SQLite file, or `requirements-dev.txt`.
 
Build the ISO with Joliet enabled (`-j1`); plain ISO9660 uppercases filenames and
silently breaks package-manager file detection:
 
```powershell
oscdimg -m -j1 -lDBNORM dbnorm_package dbnorm.iso
```
 
Record a hash of the ISO before transfer:
 
```powershell
Get-FileHash dbnorm.iso -Algorithm SHA256
```
 
---
 
## 6. Install on vm1
 
1. Mount or copy the ISO contents to a working folder, for example `C:\tools\dbnorm`.
2. Open PowerShell in that folder:
```powershell
python --version                     # expect 3.12.10
python -m venv venv
venv\Scripts\activate
pip install --no-index --find-links wheels -r requirements.txt
```
 
3. Smoke test:
```powershell
python -c "import fastapi, sqlalchemy, pymysql, pg8000, oracledb, reportlab; print('ok')"
cd backend
uvicorn app.main:app --port 8770
```
 
4. Open `http://127.0.0.1:8770/` in the browser inside the remote desktop session.
Using a venv keeps other tools' packages on the VM untouched.
 
Check network reach to a target DB before the first scan:
 
```powershell
Test-NetConnection <target_ip> -Port <db_port>
```
 
---
 
## 7. Updating the tool
 
Updates re-ship through the same flow:
 
1. Change code and, if dependencies changed, rebuild `wheels/` (section 3) and re-verify (section 4).
2. Rebuild the ISO and transfer it.
3. On vm1, replace `backend/` and `frontend/`; if dependencies changed, re-run
   `pip install --no-index --find-links wheels -r requirements.txt` in the same venv.
4. The last-scan SQLite file lives next to the backend; keep or delete it as needed.
For the phase 2 EOL table, only `backend/app/data/eol_versions.json` changes; the
"as of" date inside it shows how fresh it is.
 
---
 
## 8. Troubleshooting
 
| Symptom | Cause / fix |
|---|---|
| `pip download` fails with "no matching distribution" | A package has no `cp312` `win_amd64` wheel; pick another version or driver while still online |
| Install on vm1 says "no matching distribution" | A wheel is missing from `wheels/`; rebuild and re-verify with `--no-index` |
| `ImportError` for `cryptography` or `pillow` | Wheel built for the wrong Python/ABI; re-download with the exact `--python-version 3.12 --abi cp312` flags |
| File names look wrong or are missing on the mounted ISO | ISO built without `-j1`; rebuild with `oscdimg -m -j1` |
| MySQL auth error | `cryptography` not installed in the venv |
| `uvicorn` not found | venv not activated, or install failed silently; re-run the install and check the output |
 