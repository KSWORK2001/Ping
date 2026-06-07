# Removes Ping from Windows logon auto-start.
$startup = [Environment]::GetFolderPath('Startup')
$vbs = Join-Path $startup 'PingAgent.vbs'
if (Test-Path $vbs) {
    Remove-Item $vbs -Force
    Write-Host "Removed auto-start ($vbs). (Already-running instances keep running; use stop.ps1.)"
} else {
    Write-Host "Auto-start was not installed."
}
