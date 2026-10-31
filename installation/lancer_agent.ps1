# Superviseur : relance l'agent 30 s après toute sortie (erreur, plantage de Chrome…).
# Lancé par la tâche planifiée « AgentLogement » à l'ouverture de session.
$ErrorActionPreference = "Continue"
$racine = Split-Path -Parent $PSScriptRoot
Set-Location $racine
$py = Join-Path $racine ".venv\Scripts\python.exe"
New-Item -ItemType Directory -Force (Join-Path $racine "data") | Out-Null
$journal = Join-Path $racine "data\superviseur.log"
while ($true) {
    Add-Content -Path $journal -Value "$(Get-Date -Format s) démarrage de l'agent" -Encoding utf8
    & $py main.py *>> (Join-Path $racine "data\sortie.log")
    Add-Content -Path $journal -Value "$(Get-Date -Format s) agent arrêté (code $LASTEXITCODE), relance dans 10 s" -Encoding utf8
    Start-Sleep -Seconds 10
}
