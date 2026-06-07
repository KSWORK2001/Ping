# Installs Ping to auto-start at Windows logon: hidden window + self-healing
# watchdog (run.ps1). No administrator rights required.
$dir = $PSScriptRoot
$runner = Join-Path $dir 'run.ps1'
$startup = [Environment]::GetFolderPath('Startup')
$vbs = Join-Path $startup 'PingAgent.vbs'

$content = @"
Set s = CreateObject("WScript.Shell")
s.CurrentDirectory = "$dir"
s.Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""$runner""", 0, False
"@
Set-Content -Path $vbs -Value $content -Encoding ASCII
Write-Host "Installed auto-start -> $vbs"
Write-Host "Ping will launch hidden and self-healing at every logon."
Write-Host "Start it right now without rebooting:  wscript `"$vbs`""
