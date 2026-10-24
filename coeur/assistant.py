"""Répondre en langage naturel aux questions posées au bot Telegram (« qu'est-ce que tu fais ? »).

Les réponses s'appuient UNIQUEMENT sur l'état réel de l'agent : statut, règles, dernières offres vues
avec la décision prise, et événements récents du journal. LLM local (Ollama) ; s'il ne répond pas,
on renvoie un résumé brut du même contexte.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import requests

log = logging.getLogger("assistant")

REGLES = """RÈGLES DE L'AGENT
- Lecture des 3 sites toutes les 10 minutes. Les offres déjà en ligne au premier démarrage sont ignorées pour toujours.
- Critères : T2 minimum, 40 m² minimum ; Paris ≤ 880 € (≤ 850 € sur LOC'Annonces) ; Saint-Ouen, Saint-Denis,
  Aubervilliers, Pantin, Le Pré-Saint-Gervais, Les Lilas, Bagnolet, Montreuil ≤ 800 € ; tout le reste est rejeté.
  Loyer charges comprises. Pantin et Montreuil passent en premier.
- ALJT (Pass Logement) : candidature auto si aucune candidature en cours et moins de 5 candidats.
- LOC'Annonces (Paris) : candidature auto avec lettre de motivation si aucune en cours et 0, 1 ou 2 candidats.
- Action Logement : candidature auto tant qu'il reste au moins 2 places libres (la dernière est gardée pour
  l'utilisateur, candidatures non annulables) ; sinon alerte 🚨 répétée toutes les 2 min jusqu'au « Vu ».
- DRY-RUN = mode test : l'agent prépare tout mais n'envoie aucune candidature réelle.
- Statuts d'une offre vue : ligne_de_base (déjà en ligne au démarrage, ignorée), rejetee (hors critères),
  notifiee (correspond mais pas de candidature, raison envoyée), alertee (alerte Action Logement),
  candidature / candidature_dry_run, echec, anomalie (garde-fou : trop d'offres d'un coup)."""

PROMPT = """Tu es l'agent logement de l'utilisateur et tu lui réponds sur Telegram, en français, en le tutoyant,
de façon courte et simple (pas de jargon technique, 8 lignes maximum).
Réponds UNIQUEMENT à partir des informations ci-dessous. Si l'information n'y est pas, dis-le simplement.
N'invente aucune offre, aucun chiffre, aucune candidature. Heures au format HH:MM.

{regles}

ÉTAT ACTUEL
{statut}

DERNIÈRES OFFRES VUES (plus récentes en premier)
{offres}

INCIDENTS DES DERNIÈRES 24 H (veille, arrêts, erreurs) — à mentionner si on te demande ce qui s'est passé
{incidents}

ÉVÉNEMENTS RÉCENTS DU JOURNAL
{journal}

QUESTION DE L'UTILISATEUR : {question}
RÉPONSE :"""

_UTILES = re.compile(r"offres lues|CORRESPOND|rejetée|Candidature|candidature|→ Telegram|ERROR|WARNING|veille|"
                     r"réveil|démarrage|Agent démarré|session")


def evenements_recents(journal: Path, n: int = 40) -> str:
    try:
        lignes = journal.read_text(encoding="utf-8", errors="replace").splitlines()[-1500:]
    except OSError:
        return "(journal illisible)"
    garde = []
    for l in lignes:
        if l[:4] == "2026" or l[:2] == "20":
            if _UTILES.search(l):
                l = re.sub(r"<[^>]+>", "", l)                          # balises HTML des notifications
                l = re.sub(r"^\d{4}-(\d\d)-(\d\d) (\d\d:\d\d):\d\d,\d+ +\w+ +", r"\2/\1 \3 ", l)
                garde.append(l[:220])
    # on écrase les lignes répétitives « 7 offres lues, 0 nouvelles » : seule la dernière par plateforme
    vu, sortie = set(), []
    for l in reversed(garde):
        cle = re.sub(r"^\S+ \S+ ", "", l) if "0 nouvelles" in l else l
        if cle in vu:
            continue
        vu.add(cle)
        sortie.append(l)
    return "\n".join(reversed(sortie[:n]))


def incidents(journal: Path, heures: int = 24) -> str:
    from datetime import datetime, timedelta
    limite = (datetime.now() - timedelta(hours=heures)).strftime("%Y-%m-%d %H:%M")
    try:
        lignes = journal.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(journal illisible)"
    garde = [re.sub(r"<[^>]+>", "", l)[:200] for l in lignes
             if l[:16] >= limite and re.search(r"veille|ARRÊTÉ|ERROR|AGENT KO|BATTERIE", l)]
    garde = [re.sub(r"^\d{4}-(\d\d)-(\d\d) (\d\d:\d\d):\d\d,\d+ +\w+ +", r"\2/\1 \3 ", l) for l in garde]
    return "\n".join(garde[-25:]) or "aucun"


def dernieres_offres(moteurs: dict, n: int = 8) -> str:
    morceaux = []
    for p, m in moteurs.items():
        vues = sorted(m.m.etat["vues"].items(), key=lambda kv: kv[1].get("t", ""), reverse=True)[:n]
        lignes = [f"  {v.get('t', '')[5:16].replace('T', ' ')} [{v.get('statut')}] {v.get('resume')}" for _, v in vues]
        morceaux.append(f"{p} :\n" + ("\n".join(lignes) or "  (aucune)"))
    return "\n".join(morceaux)


def repondre(question: str, statut: str, moteurs: dict, journal: Path, url: str, modele: str,
             timeout: int = 60, session=None) -> str:
    statut_txt = re.sub(r"<[^>]+>", "", statut)
    contexte = dict(regles=REGLES, statut=statut_txt, offres=dernieres_offres(moteurs),
                    journal=evenements_recents(journal), incidents=incidents(journal), question=question.strip()[:500])
    try:
        r = (session or requests).post(f"{url.rstrip('/')}/api/generate", timeout=timeout, json={
            "model": modele, "prompt": PROMPT.format(**contexte), "stream": False,
            "options": {"temperature": 0.2, "num_predict": 400}})
        r.raise_for_status()
        rep = (r.json().get("response") or "").strip()
        if rep:
            return rep
    except Exception as e:
        log.warning("assistant LLM indisponible : %s", e)
    derniers = "\n".join(contexte["journal"].splitlines()[-8:])
    return (f"(IA locale indisponible, voici l'état brut)\n\n{statut_txt}\n\nDerniers événements :\n{derniers}")
