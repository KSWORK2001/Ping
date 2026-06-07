# Watchdog: runs Ping in the foreground and auto-restarts it if it crashes.
# Use this when you want self-healing while you're away. Ctrl+C to stop.
Set-Location $PSScriptRoot
while ($true) {
    Write-Host "[watchdog] starting Ping at $(Get-Date -Format 'HH:mm:ss')"
    python bot.py
    Write-Host "[watchdog] Ping exited (code $LASTEXITCODE). Restarting in 5s..."
    Start-Sleep -Seconds 5
}
