param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$Python = "C:\Users\26320\.dsh\dsh-runtimes\dsh-next-primary-runtime\dependencies\python\python.exe"
)

# 打包版 GUI 冒烟：启动 → 截图 → 关闭
$env:TEMP = Join-Path $Root ".tmp"
$env:TMP = $env:TEMP

$exe = Join-Path $Root "dist\zip2epub.exe"
if (-not (Test-Path $exe)) {
    "missing exe: $exe"
    exit 1
}

$process = Start-Process -FilePath $exe -WorkingDirectory $Root -PassThru
Start-Sleep -Seconds 10
$process.Refresh()

"pid=$($process.Id) exited=$($process.HasExited) title=[$($process.MainWindowTitle)]"

if (-not $process.HasExited) {
    & $Python (Join-Path $Root "tools\capture_exe.py")
    Start-Sleep -Milliseconds 400
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 600
    Get-Process zip2epub -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    "closed"
}
