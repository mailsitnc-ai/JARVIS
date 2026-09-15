# JARVIS installer check. Run from PowerShell:  .\install.ps1 [-AddToPath] [-Startup]
#   -AddToPath  lets you type `jarvis` from any terminal (adds this folder to your user PATH)
#   -Startup    starts JARVIS in the background at sign-in so Ctrl+Alt+J always works
param([switch]$AddToPath, [switch]$Startup)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

$candidates = @(
    "$root\.venv\Scripts\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:ProgramFiles\Python313\python.exe",
    "$env:ProgramFiles\Python312\python.exe"
)
$py = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) {
    Write-Host "Python 3.10+ was not found. Install it, then run this script again:" -ForegroundColor Yellow
    Write-Host "    winget install -e --id Python.Python.3.13"
    exit 1
}
Write-Host "Python: $py"

& $py -c "import sys, tkinter; assert sys.version_info >= (3, 10)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "This Python is older than 3.10 or was installed without Tcl/Tk (tkinter)." -ForegroundColor Yellow
    exit 1
}

Write-Host "Running tests..."
& $py -m unittest discover -s "$root\tests" -t "$root"
if ($LASTEXITCODE -ne 0) { Write-Host "Tests failed." -ForegroundColor Red; exit 1 }

if ($AddToPath) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (($userPath -split ";") -notcontains $root) {
        [Environment]::SetEnvironmentVariable("Path", (@($userPath, $root) | Where-Object { $_ }) -join ";", "User")
        Write-Host "Added $root to your PATH. Open a new terminal to use 'jarvis'."
    }
}
if ($Startup) { & "$root\jarvis.cmd" startup enable }

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  winget install -e --id Ollama.Ollama --scope user     local AI runtime (free, offline)"
Write-Host "  ollama pull qwen2.5-coder:0.5b-instruct-q8_0         the local model, 531 MB"
Write-Host "  jarvis doctor --live    check everything end to end"
Write-Host "  jarvis on               open JARVIS in the 60/40 split, then Ctrl+Alt+J toggles it"
