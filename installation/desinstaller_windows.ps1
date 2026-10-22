Stop-ScheduledTask -TaskName "AgentLogement" -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "AgentLogement" -Confirm:$false
Unregister-ScheduledTask -TaskName "AgentLogementReveil" -Confirm:$false -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*agent-logement*main.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -like "*lancer_agent.ps1*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Write-Host "Tâche AgentLogement supprimée."
