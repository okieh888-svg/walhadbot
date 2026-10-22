# Redémarre proprement l'agent (tâche + processus Python + Chrome de l'agent), par ex. après modification de .env
Stop-ScheduledTask -TaskName "AgentLogement" -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*lancer_agent.ps1*" -or ($_.Name -eq "python.exe" -and $_.CommandLine -like "*main.py*") -or ($_.Name -eq "chrome.exe" -and $_.CommandLine -like "*agent-logement*navigateur*") } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
Enable-ScheduledTask -TaskName "AgentLogement" | Out-Null
Start-ScheduledTask -TaskName "AgentLogement"
Write-Host "Agent redémarré. Journal : data\agent.log"
