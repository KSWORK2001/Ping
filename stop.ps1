# Stops any running Ping bot instances.
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*bot.py*' }
if (-not $procs) { Write-Host "Ping is not running."; return }
foreach ($p in $procs) {
    Write-Host "Stopping Ping (PID $($p.ProcessId))"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
