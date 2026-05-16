$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$launcherPath = Join-Path $repoRoot "scripts\run_cockpit.bat"
$shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "Portfolio Risk Cockpit.lnk"

if (-not (Test-Path $launcherPath)) {
    throw "Launcher not found: $launcherPath"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcherPath
$shortcut.WorkingDirectory = $repoRoot
$shortcut.WindowStyle = 7
$shortcut.Description = "Launch Portfolio Risk Cockpit"
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,174"
$shortcut.Save()

Write-Host "Created desktop shortcut: $shortcutPath"
