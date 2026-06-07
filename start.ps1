# Starts Ping hidden (survives closing this terminal). Logs to ping.log / ping.err.log.
Set-Location $PSScriptRoot
$env:PYTHONUNBUFFERED = "1"   # so ping.log isn't block-buffered
Start-Process -FilePath "python" -ArgumentList "-u", "bot.py" -WindowStyle Hidden `
    -RedirectStandardOutput "ping.log" -RedirectStandardError "ping.err.log"
Write-Host "Ping started (hidden). Tail logs with:  Get-Content .\ping.log -Wait"
