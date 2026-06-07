# Stops any running Ping and starts a fresh hidden instance.
& "$PSScriptRoot\stop.ps1"
Start-Sleep -Seconds 1
& "$PSScriptRoot\start.ps1"
