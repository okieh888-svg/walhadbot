# Agent logement — ALJT · LOC'Annonces · Action Logement

Agent local qui surveille trois plateformes, **candidate automatiquement** quand les règles le
permettent (ALJT, LOC'Annonces) et **alerte au maximum** sur Telegram (Action Logement : candidature auto en gardant 1 place, sinon alerte).

| Plateforme | Site | Action | Lecture |
|---|---|---|---|
| Pass Logement (ALJT) | offres.passlogement.com | candidature auto si 0–4 candidats et aucune candidature active | API JSON du site, toutes les 10 min |
| LOC'Annonces (Paris) | teleservices.paris.fr/locannonces | candidature auto + motif si 0–2 candidatures, aucune active, loyer ≤ 850 € (Paris) / ≤ 800 € (93) | pages du site, 10 min |
| Action Logement | logement-actionlogement.fr | candidature auto tant qu'il reste ≥ 2 places libres dans la catégorie (1 gardée pour toi, non annulables) ; sinon 🚨 alerte relancée toutes les 2 min jusqu'au « ✅ Vu » | API REST du site |

Critères communs (`coeur/criteres.py`) : T2 minimum, 40 m² minimum ; Paris ≤ 880 € ; Saint-Ouen-sur-Seine,
Saint-Denis, Aubervilliers, Pantin, Le Pré-Saint-Gervais, Les Lilas, Bagnolet, Montreuil ≤ 800 €
(Pantin et Montreuil traités en premier). Loyer **charges comprises** sur les trois sites (ALJT : « Loyer (charges
comprises) » ; LOC'Annonces : loyer + charges collectives ; AL : `totalRentAmountBaseBound` « /mois-CC »).

**Règle absolue** : au premier passage réussi de chaque plateforme, toutes les offres en ligne (toutes pages) sont
marquées vues et ne déclenchent jamais rien (« ▶️ Démarrage : X offres déjà en ligne ignorées »). Garde-fou : plus de
`SEUIL_ANOMALIE` nouvelles offres d'un coup → marquées vues sans action + alerte.

## Plusieurs personnes et interface web (depuis le 31/10/2026)

L'agent surveille **plusieurs personnes**, chacune avec ses plateformes, ses critères et son Telegram.
Tout se règle dans l'**interface web locale** : **http://127.0.0.1:8080** (accessible uniquement depuis ce PC,
démarrée avec l'agent).

- **Ajouter une personne** : nom, zones (codes postaux + loyer max charges comprises ; « 75 » = tout Paris),
  pièces minimum, surface minimum, plateformes à surveiller (ALJT, LOC'Annonces, Action Logement, **AL'in**),
  identifiant et mot de passe de chaque site, « Candidatures réelles » (sinon mode test), lettre de motivation.
- **Mots de passe** : saisis par la personne elle-même, chiffrés avec la protection Windows (DPAPI) dans
  `data/personnes/<id>/secrets/`, jamais affichés ni écrits en clair.
- **Telegram** : la fiche donne un code ; la personne envoie `/lier CODE` au bot et reçoit ensuite SES
  notifications ; `/statut`, `/pause`, `/reprise` et les questions libres ne concernent que son agent.
  Tant qu'elle n'est pas reliée, ses notifications arrivent chez l'administrateur (TELEGRAM_CHAT_ID).
- Toute modification enregistrée relance l'agent (≈ 10 s). Au plus 3 navigateurs ouverts en même temps.
- **AL'in** : une seule candidature à la fois ; candidature auto si aucune n'est en cours (sinon 🏠).
- Connexion manuelle (captcha, code) : `python main.py --connexion <personne>:<plateforme>`.
- Migration de l'ancienne configuration : `python outils/migrer_vers_multi.py "Nom"` (déjà faite pour Ismail).

## Architecture

```
main.py                     boucle par plateforme (threads), Telegram, relances AL, détection de veille
coeur/criteres.py           classer(offre) → Verdict
coeur/politiques.py         politique par plateforme (candidater / notifier / alerter / ignorer) — fonctions pures
coeur/memoire.py            data/<plateforme>/etat.json : offres vues, ligne de base, candidatures
coeur/moteur.py             lire → nouvelles → enrichir → décider → re-vérifier → agir → notifier
coeur/motif.py              motif LOC'Annonces : Ollama + contrôle anti-invention + modèle de secours
coeur/alertes.py            relances AL jusqu'au « ✅ Vu » (persistantes)
coeur/telegram.py           envoi, /statut /pause /reprise, boutons
plateformes/base.py         interface : connexion(), lister_offres(), enrichir(), nombre_candidats(),
                            candidature_active(), candidater(offre, motif, dry_run)  + heuristique JSON de secours
plateformes/aljt.py · locannonces.py · action_logement.py
faux_sites/serveur.py       faux sites reproduisant la structure réelle (tests)
exploration/<plateforme>/   observations anonymisées des vrais sites (OBSERVATIONS.md, JSON, captures)
data/<plateforme>/          état, profil navigateur (session), captures, motifs, journal (non commité)
```

## Installation (Windows — mode recommandé : venv + Planificateur de tâches)

```powershell
cd agent-logement
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env                       # puis remplis .env toi-même
copy profil_candidat.exemple.md profil_candidat.md
```
L'agent pilote **ton Google Chrome installé** (`NAVIGATEUR_CANAL=chrome`) avec des profils séparés dans
`data/<plateforme>/navigateur` (le Chromium de Playwright ne démarre pas sur ce PC).
Action Logement est protégé par Cloudflare, qui bloque Chrome en mode invisible : pour ce site l'agent ouvre une
**vraie fenêtre Chrome réduite** dans la barre des tâches (`AL_HEADLESS=0`), sans aucun contournement.

### Bot Telegram
1. Dans Telegram, ouvre **@BotFather** → `/newbot` → choisis un nom → il te donne le **token** → `TELEGRAM_BOT_TOKEN`.
2. Ouvre la conversation avec ton bot et envoie-lui `/start`.
3. Dans un navigateur : `https://api.telegram.org/bot<TOKEN>/getUpdates` → repère `"chat":{"id":123456789` → `TELEGRAM_CHAT_ID`.
4. Dans Telegram, laisse les notifications de cette conversation **activées avec son** (l'agent n'envoie jamais en silencieux).

Commandes du bot : `/statut` (plateformes, dernière lecture, candidature active, erreurs), `/pause`, `/reprise`.
Seul ton `TELEGRAM_CHAT_ID` est écouté.

### Motif LOC'Annonces (Ollama local)
```powershell
winget install Ollama.Ollama
ollama pull qwen2.5:7b-instruct      # ~4,7 Go, tient dans les 8 Go de la RTX 4060
```
Le motif est généré **uniquement** à partir de `profil_candidat.md` et de l'annonce. Un texte contenant un chiffre
absent de ces sources est rejeté et remplacé par le **motif de secours** (modèle rempli avec le profil) ; idem si
Ollama ne répond pas en `LLM_TIMEOUT` secondes. Chaque motif est enregistré dans `data/locannonces/motifs/` et envoyé
dans la notification.

### Lancer
```powershell
.\.venv\Scripts\python.exe main.py --dry-run --debug          # test : aucune candidature réelle
.\.venv\Scripts\python.exe main.py --once --plateformes aljt   # un passage
.\.venv\Scripts\python.exe main.py --connexion locannonces     # connexion manuelle (captcha/code éventuel)
.\.venv\Scripts\python.exe outils\demo_dry_run.py aljt <id>    # démonstration dry-run sur une offre existante
.\.venv\Scripts\python.exe -m pytest                           # tests (dont bout en bout sur faux sites)
```

### Service (démarrage automatique + relance)
```powershell
powershell -ExecutionPolicy Bypass -File installation\installer_windows.ps1
```
Crée la tâche planifiée **AgentLogement** (à l'ouverture de ta session, sur batterie aussi, sans limite de durée),
qui lance `installation\lancer_agent.ps1` : ce superviseur relance l'agent 30 s après tout arrêt. Le script
restreint aussi `.env` et `profil_candidat.md` à ton compte (équivalent de `chmod 600`).
Désinstaller : `installation\desinstaller_windows.ps1`. Journaux : `data\agent.log`, `data\superviseur.log`.
L'état (`data/`) est conservé entre les redémarrages : aucune offre déjà vue n'est retraitée.

⚠️ **Veille / arrêt** : tant qu'il tourne et que le PC est **sur secteur**, l'agent empêche la mise en veille
automatique (`SetThreadExecutionState`, comme un lecteur vidéo, sans modifier les réglages Windows). Sur batterie,
la veille reste possible : message « 🔋 PC sur BATTERIE ». Fermer le capot met toujours en veille (réglable dans
Paramètres → Alimentation). Au réveil : « 😴 … en veille de HH:MM à HH:MM » ; après un arrêt du PC :
« ⏻ L'agent était ARRÊTÉ de … à … » (battement `data/battement.txt`). L'agent ne démarre qu'à l'ouverture de session.
Ne lance pas `main.py` à la main pendant que le service tourne (les profils navigateur seraient verrouillés).

### Option Docker
`docker compose up -d --build` (redémarrage `unless-stopped`, `data/` monté en volume). Limites : Chromium
headless de l'image (Action Logement/Cloudflare probablement bloqué), profils navigateur distincts de ceux
de Windows, Ollama joint via `host.docker.internal`.

## Passer en production (après validation du dry-run)
Dans `.env` :
```
ALJT_PRODUCTION=1        # candidature ALJT réelle
PARIS_PRODUCTION=1       # candidature LOC'Annonces réelle
AL_PRODUCTION=1          # candidature Action Logement réelle (garde 1 place libre)
```
puis redémarre : `powershell -ExecutionPolicy Bypass -File installation
edemarrer.ps1`.
`--dry-run` en ligne de commande force toujours le mode test. Notifications importantes préfixées 🔴🔴, captures avec bandeau rouge. Intervalles : `*_INTERVALLE` (600 s par défaut).

## Sécurité
Identifiants **uniquement** dans `.env` (jamais dans le code, les journaux — masqués automatiquement —, les
captures — champs mot de passe masqués — ni un commit). `.env`, `profil_candidat.md` et `data/` sont exclus par
`.gitignore` et `.dockerignore`. Pas de rafales (intervalles ±20 %, ralentissement après erreurs), aucun
contournement de captcha ni de protection : en cas de captcha, de code ou de défi Cloudflare persistant,
l'agent s'arrête sur cette plateforme et envoie « 🛑 Agent KO … intervention requise » (une fois par heure au plus).
