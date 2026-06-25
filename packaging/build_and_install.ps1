# Build Earshot into a standalone app and "install" it for the current user
# (no admin rights): copies to %LOCALAPPDATA%\Programs\Earshot and creates
# Desktop + Start Menu shortcuts. Also cleans up the pre-rename "MeetingNotes"
# install + shortcuts if present.
#
# Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File "packaging\build_and_install.ps1"

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root ".venv\Scripts\python.exe"

# Close any running instance (old name or new) so files aren't locked.
Get-Process Earshot, MeetingNotes -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1

# Ensure build deps + icon are present.
& $py -m pip show pyinstaller *> $null
if ($LASTEXITCODE -ne 0) { Write-Host "Installing PyInstaller..."; & $py -m pip install pyinstaller -q }
$icon = Join-Path $root "packaging\earshot.ico"
if (-not (Test-Path $icon)) { throw "Missing $icon - generate it with: python tools/make_icon.py (needs Pillow)" }

Write-Host "Building with PyInstaller (a few minutes)..."
& $py -m PyInstaller (Join-Path $root "packaging\meeting_notes.spec") `
    --noconfirm --clean `
    --distpath (Join-Path $root "dist") --workpath (Join-Path $root "build")

$src = Join-Path $root "dist\Earshot"
$dst = Join-Path $env:LOCALAPPDATA "Programs\Earshot"
Write-Host "Installing to $dst ..."
if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
Copy-Item $src $dst -Recurse -Force
$exe = Join-Path $dst "Earshot.exe"

$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$startmenu = Join-Path ([Environment]::GetFolderPath('StartMenu')) "Programs"
foreach ($loc in @((Join-Path $desktop 'Earshot.lnk'), (Join-Path $startmenu 'Earshot.lnk'))) {
    $lnk = $ws.CreateShortcut($loc)
    $lnk.TargetPath = $exe
    $lnk.WorkingDirectory = $dst
    $lnk.IconLocation = "$exe,0"
    $lnk.Description = "Earshot - record, transcribe and summarise meetings"
    $lnk.Save()
}

# --- clean up the old pre-rename install + shortcuts ---
$oldDir = Join-Path $env:LOCALAPPDATA "Programs\MeetingNotes"
if (Test-Path $oldDir) { Remove-Item $oldDir -Recurse -Force }
foreach ($old in @((Join-Path $desktop 'Meeting Notes.lnk'), (Join-Path $startmenu 'Meeting Notes.lnk'))) {
    if (Test-Path $old) { Remove-Item $old -Force }
}

Write-Host "Done. Launch from the 'Earshot' desktop icon."
