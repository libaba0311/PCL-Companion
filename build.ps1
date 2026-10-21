$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectDir

python -m pip install --upgrade "pyinstaller>=6.10,<7"
python -m PyInstaller --noconfirm --clean PCL-Companion.spec

if (-not (Test-Path -LiteralPath "dist\PCL-Companion.exe")) {
    throw "Build finished without dist\PCL-Companion.exe"
}

Get-FileHash -Algorithm SHA256 "dist\PCL-Companion.exe"
