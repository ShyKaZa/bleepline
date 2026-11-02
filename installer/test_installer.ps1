# Installer tests: fresh install, upgrade with the app running, upgrade from 1.1, uninstall.
# It really installs and uninstalls (registry, Start menu), so the release workflow runs it on GitHub's
# throwaway build machine; on your own PC, use a virtual machine. Everything it creates is removed at the end.
#     powershell -File installer\test_installer.ps1 [path\to\Bleepline-Setup.exe]
param([string]$Setup = "release\Bleepline-Setup.exe")
$Setup = (Resolve-Path $Setup).Path
$ErrorActionPreference = "Stop"
$root = Join-Path $env:TEMP "bleepline-installer-test"
$uninstRoot = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall"
$innoKey = "$uninstRoot\Bleepline_is1"
$oldKey = "$uninstRoot\Bleepline"
$dataDir = "$env:LOCALAPPDATA\Bleepline"
$dataExisted = Test-Path $dataDir
$startMenu = [Environment]::GetFolderPath("Programs") + "\Bleepline.lnk"
$failures = 0
$version = [regex]::Match((Get-Content "$PSScriptRoot\..\app\version.py" -Raw), 'VERSION = "([^"]+)"').Groups[1].Value
function Check($ok, $what) { if ($ok) { Write-Host "  ok   $what" } else { Write-Host "  FAIL $what"; $script:failures++ } }
function Install($argsList) { (Start-Process $Setup -ArgumentList $argsList -Wait -PassThru).ExitCode }
function Uninstall($dir) {
    Start-Process "$dir\unins000.exe" -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES" -Wait
    for ($i = 0; $i -lt 60 -and (Test-Path $innoKey); $i++) { Start-Sleep -Seconds 1 }
    Start-Sleep -Seconds 1
}
function FakeOldInstall($dir) {
    New-Item -ItemType Directory -Force "$dir\_internal", "$dir\obs-plugin" | Out-Null
    "old" | Set-Content "$dir\Bleepline.exe"; "old" | Set-Content "$dir\_internal\old.dll"
    "old" | Set-Content "$dir\obs-plugin\bleepline-filter.dll"; "x" | Set-Content "$dir\LICENSE"
    "x" | Set-Content "$dir\THIRD_PARTY_NOTICES.txt"; "mine" | Set-Content "$dir\keep-me.txt"
    New-Item $oldKey -Force | Out-Null
    Set-ItemProperty $oldKey -Name InstallLocation -Value $dir
    Set-ItemProperty $oldKey -Name DisplayName -Value "Bleepline"
}
Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "1. Fresh install into a folder with a space in its name"
$a = "$root\Fresh install"
Check ((Install "/VERYSILENT", "/SUPPRESSMSGBOXES", "/DIR=`"$a`"", "/TASKS=checkupdates") -eq 0) "setup exit code 0"
Check (Test-Path "$a\Bleepline.exe") "Bleepline.exe installed"
Check (Test-Path "$a\_internal\assets\wordlists\strong.txt") "built-in word lists bundled"
Check (-not (Test-Path "$a\_internal\ctranslate2\cudnn64_9.dll")) "cuDNN loader left out"
$e = Get-ItemProperty $innoKey
Check ($e.DisplayVersion -eq $version -and $e.Publisher -eq "Gorgho") "Apps & features entry: version $($e.DisplayVersion), publisher $($e.Publisher)"
Check ((Get-ItemProperty "HKCU:\Software\Bleepline").CheckForUpdates -eq 1) "update-check choice saved (on)"
Check (Test-Path $startMenu) "Start menu shortcut"

Write-Host "2. Upgrade while Bleepline is running: it is closed gracefully (saves settings) and replaced"
Remove-Item "$dataDir\settings.json" -ErrorAction SilentlyContinue
$app = Start-Process "$a\Bleepline.exe" -PassThru
Start-Sleep -Seconds 8
Check (-not $app.HasExited) "app started"
"stale" | Set-Content "$a\_internal\stale-from-old-version.dll"
Check ((Install "/VERYSILENT", "/SUPPRESSMSGBOXES", "/TASKS=") -eq 0) "upgrade exit code 0"
Check ($app.HasExited) "running app was closed"
# (Informational: a machine without a desktop session may not give the app a window to ask nicely.)
Write-Host ("  note app " + $(if (Test-Path "$dataDir\settings.json") { "saved its settings on the way out (graceful close)" } else { "was closed forcefully" }))
Check (-not (Test-Path "$a\_internal\stale-from-old-version.dll")) "old libraries removed"
Check (Test-Path "$a\Bleepline.exe") "upgraded in the same folder (no /DIR given)"
Check ((Get-ItemProperty "HKCU:\Software\Bleepline").CheckForUpdates -eq 0) "update-check choice updated (off)"
Uninstall $a
Check (-not (Test-Path "$a\Bleepline.exe")) "uninstall removed the app"
Check (-not (Test-Path $innoKey)) "uninstall removed the Apps entry"
Check (-not (Test-Path "HKCU:\Software\Bleepline")) "uninstall removed the update-check setting"
Check (-not (Test-Path $startMenu)) "uninstall removed the Start menu shortcut"

Write-Host "3. Upgrade from 1.1 (its own installer) into a different folder"
$old = "$root\Old 1.1"; $new = "$root\New"
FakeOldInstall $old
Check ((Install "/VERYSILENT", "/SUPPRESSMSGBOXES", "/DIR=`"$new`"") -eq 0) "setup exit code 0"
Check (-not (Test-Path "$old\Bleepline.exe") -and -not (Test-Path "$old\_internal") -and -not (Test-Path "$old\obs-plugin")) "1.1's files removed"
Check (Test-Path "$old\keep-me.txt") "unrelated file in the old folder kept"
Check (-not (Test-Path $oldKey)) "1.1's Apps entry removed"
Check (Test-Path "$new\Bleepline.exe") "installed in the new folder"
Uninstall $new

Write-Host "4. Upgrade from 1.1 without choosing a folder: installs where 1.1 was"
$old2 = "$root\Old 1.1 here"
FakeOldInstall $old2
Check ((Install "/VERYSILENT", "/SUPPRESSMSGBOXES") -eq 0) "setup exit code 0"
Check ((Get-Content "$old2\Bleepline.exe" -TotalCount 1 -ErrorAction SilentlyContinue) -ne "old" -and (Test-Path "$old2\Bleepline.exe")) "replaced 1.1 in place"
Check (-not (Test-Path "$old2\_internal\old.dll")) "1.1's libraries replaced"
Check ((Get-ItemProperty $innoKey).InstallLocation.TrimEnd("\") -eq $old2) "Apps entry points at 1.1's folder"
Uninstall $old2
Check (Test-Path "$old2\keep-me.txt") "uninstall kept the unrelated file"
Check (-not (Test-Path "$old2\Bleepline.exe")) "uninstall removed only Bleepline's files"

Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
if (-not $dataExisted) { Remove-Item $dataDir -Recurse -Force -ErrorAction SilentlyContinue }
Remove-Item $oldKey -ErrorAction SilentlyContinue
Write-Host ("RESULT: " + $(if ($failures -eq 0) { "all checks passed" } else { "$failures check(s) failed" }))
exit $failures
