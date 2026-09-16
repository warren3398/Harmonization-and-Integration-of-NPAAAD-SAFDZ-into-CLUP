$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$Desktop = [Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $Desktop 'NPAAAD-SAFDZ CLUP Harmonizer.lnk'
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "$env:WINDIR\System32\wscript.exe"
$Shortcut.Arguments = '"' + (Join-Path $Base 'START_HARMONIZER.vbs') + '"'
$Shortcut.WorkingDirectory = $Base
$Shortcut.IconLocation = (Join-Path $Base 'harmonizer.ico') + ',0'
$Shortcut.Description = 'Harmonization and Integration of NPAAAD/SAFDZ into CLUP'
$Shortcut.Save()
Write-Host "Desktop shortcut created: $ShortcutPath"
