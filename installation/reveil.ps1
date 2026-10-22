# Lancé toutes les 10 min par la tâche « AgentLogementReveil » (option « réveiller l'ordinateur »).
# Garde le PC éveillé le temps que l'agent fasse une lecture des sites, puis le laisse se rendormir.
$racine = Split-Path -Parent $PSScriptRoot
Add-Type -Namespace Agent -Name Eveil -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
[Agent.Eveil]::SetThreadExecutionState([uint32]"0x80000001") | Out-Null      # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
$debut = Get-Date
Set-Content -Path (Join-Path $racine "data\reveil.flag") -Value $debut.ToString("s")
$etats = Get-ChildItem (Join-Path $racine "data\*\etat.json") -ErrorAction SilentlyContinue
# attendre que chaque plateforme ait relu son site (etat.json réécrit), 4 min au plus
while (((Get-Date) - $debut).TotalSeconds -lt 240) {
    Start-Sleep -Seconds 5
    $enRetard = $etats | Where-Object { (Get-Item $_.FullName).LastWriteTime -lt $debut }
    if (-not $enRetard) { break }
}
Start-Sleep -Seconds 20                                                       # laisser partir les notifications
[Agent.Eveil]::SetThreadExecutionState([uint32]"0x80000000") | Out-Null
