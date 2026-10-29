param(
    [switch]$Reinstall,
    [switch]$SkipBuild
)

# Build a single windowed exe (no console) with PyInstaller.
# ASCII-only on purpose: Windows PowerShell reads .ps1 as ANSI without a BOM.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 1. Pick a Python that has tkinter + Pillow + lxml
$python = $null
$candidates = @(
    "C:\Users\$env:USERNAME\.dsh\dsh-runtimes\dsh-next-primary-runtime\dependencies\python\python.exe",
    "python"
)
foreach ($candidate in $candidates) {
    if ($candidate -eq "python") {
        try {
            & python -c "import PIL, lxml, tkinter" 2>$null
            if ($LASTEXITCODE -eq 0) { $python = "python"; break }
        } catch { }
    }
    elseif (Test-Path $candidate) {
        & $candidate -c "import PIL, lxml, tkinter" 2>$null
        if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
    }
}

if (-not $python) {
    Write-Host "No Python with Pillow + lxml + tkinter found." -ForegroundColor Red
    Write-Host "Install them first:  pip install -r requirements.txt"
    exit 1
}

Write-Host "==> using python: $python" -ForegroundColor Cyan

# 2. PyInstaller into a local target dir (does not touch the interpreter)
if ($Reinstall -or -not (Test-Path ".pyi\PyInstaller")) {
    Write-Host "==> installing PyInstaller into .pyi" -ForegroundColor Cyan
    $env:TEMP = Join-Path $root ".tmp"
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
    & $python -m pip install --disable-pip-version-check --no-cache-dir --target .pyi pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

# 3. Icon
Write-Host "==> generating icon" -ForegroundColor Cyan
& $python tools\make_icon.py

if ($SkipBuild) { exit 0 }

# 4. Single windowed build
$icon = Join-Path $root "docs\app.ico"
$version = Join-Path $root "build\version_info.txt"
$env:PYTHONPATH = ".pyi"
$env:TEMP = Join-Path $root ".tmp"
$env:TMP = $env:TEMP

Write-Host "==> building zip2epub.exe (single file, windowed)" -ForegroundColor Cyan
& $python -m PyInstaller --noconfirm --onefile --windowed --name zip2epub `
    --icon $icon --version-file $version --paths . `
    --hidden-import gui.app --hidden-import z2e.cli `
    --distpath dist --workpath .build --specpath .build `
    launcher.py
if ($LASTEXITCODE -ne 0) { throw "build failed" }

Write-Host "==> done" -ForegroundColor Green
Get-ChildItem dist\*.exe | ForEach-Object {
    "{0}  {1:N1} MB" -f $_.Name, ($_.Length / 1MB)
}
