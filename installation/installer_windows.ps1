# Installe l'agent comme tâche planifiée Windows (démarrage à l'ouverture de session, relance auto).
# À lancer depuis le dossier agent-logement :  powershell -ExecutionPolicy Bypass -File installation\installer_windows.ps1
$ErrorActionPreference = "Stop"
$racine = Split-Path -Parent $PSScriptRoot
Set-Location $racine

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "→ .env créé : remplis-le." }
if (-not (Test-Path "profil_candidat.md")) { Copy-Item "profil_candidat.exemple.md" "profil_candidat.md" }

# Équivalent de chmod 600 : seul ton compte peut lire .env et profil_candidat.md
foreach ($f in ".env", "profil_candidat.md") {
    icacls $f /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$racine\installation\lancer_agent.ps1`"" `
    -WorkingDirectory $racine
$declencheur = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$reglages = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "AgentLogement" -Action $action -Trigger $declencheur -Settings $reglages `
    -Description "Agent logement (ALJT, LOC'Annonces, Action Logement)" -Force | Out-Null
Start-ScheduledTask -TaskName "AgentLogement"

# Réveil automatique : sort le PC de veille toutes les 10 min pour une lecture des sites (sur secteur)
$actionR = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$racine\installationeveil.ps1`"" `
    -WorkingDirectory $racine
$declR = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$reglR = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 6) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "AgentLogementReveil" -Action $actionR -Trigger $declR -Settings $reglR `
    -Description "Réveille le PC toutes les 10 min pour l'agent logement" -Force | Out-Null
Write-Host "Tâche « AgentLogementReveil » installée (réveil toutes les 10 min)."
Write-Host "Tâche « AgentLogement » installée et démarrée. Journal : data\agent.log"
