"""Génération du motif de candidature (LOC'Annonces).

- Source unique des faits : profil_candidat.md + champs de l'annonce.
- LLM local (Ollama). Si indisponible, trop lent, ou si le texte contient un
  fait vérifiable absent des sources (nombre, montant, date…), on bascule sur
  le motif de secours construit uniquement à partir du profil.
- Le texte respecte toujours la longueur maximale du champ du site.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from .config import RACINE
from .offre import Offre

log = logging.getLogger("motif")

PROFIL = RACINE / "profil_candidat.md"


@dataclass
class Motif:
    texte: str
    source: str           # "llm" | "secours"
    raison_secours: str = ""


def lire_profil(chemin: Path = PROFIL) -> dict[str, str]:
    """Lit les lignes « - Clé : valeur » du profil (les lignes vides ou « ... » sont ignorées)."""
    champs: dict[str, str] = {}
    if not chemin.exists():
        return champs
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*[-*]\s*([^:]{2,60}?)\s*:\s*(.+?)\s*$", ligne)
        if m and m.group(2) not in ("...", "…", "-") and not m.group(2).startswith("<"):
            champs[m.group(1).strip()] = m.group(2).strip()
    return champs


def texte_profil(champs: dict[str, str]) -> str:
    return "\n".join(f"- {k} : {v}" for k, v in champs.items())


def tronquer(texte: str, maxi: int) -> str:
    texte = re.sub(r"[ \t]+", " ", texte).strip()
    if len(texte) <= maxi:
        return texte
    coupe = texte[:maxi]
    fin = max(coupe.rfind(". "), coupe.rfind(".\n"), coupe.rfind("!"))
    if fin > maxi * 0.5:
        return coupe[: fin + 1].strip()
    return coupe[: coupe.rfind(" ")].rstrip(" ,;") + "."


def _nombres(t: str) -> set[str]:
    return {n.replace(" ", "").replace(" ", "").replace(",", ".").lstrip("0") or "0"
            for n in re.findall(r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?", t)}


def _nombres_simples(t: str) -> set[str]:
    return {n.replace(",", ".").lstrip("0") or "0" for n in re.findall(r"\d+(?:[.,]\d+)?", t)}


def faits_inventes(texte: str, sources: str) -> list[str]:
    """Nombres présents dans le texte mais absents des sources (heuristique anti-invention).

    « 3 200 » est accepté si « 3200 » figure dans les sources, ou si « 3 » et « 200 » y figurent."""
    connus = _nombres(sources) | _nombres_simples(sources)
    inventes = []
    for brut in re.findall(r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?", texte):
        groupe = next(iter(_nombres(brut)))
        if groupe in connus or _nombres_simples(brut) <= connus:
            continue
        inventes.append(groupe)
    return sorted(set(inventes))


def contexte_annonce(o: Offre) -> str:
    morceaux = [f"Référence : {o.id}", f"Adresse/commune : {o.titre or ''} {o.ville} {o.code_postal}".strip()]
    if o.pieces:
        morceaux.append(f"Typologie : T{o.pieces}")
    if o.surface:
        morceaux.append(f"Surface : {o.surface:g} m²")
    if o.loyer is not None:
        morceaux.append(f"Loyer : {o.loyer:.0f} €")
    if o.financement:
        morceaux.append(f"Financement : {o.financement}")
    if o.description:
        morceaux.append(f"Description : {o.description[:1500]}")
    return "\n".join(morceaux)


def motif_secours(o: Offre, champs: dict[str, str], maxi: int) -> str:
    """Modèle fixe rempli uniquement avec le profil. Aucun fait ajouté."""
    lieu = f"au {o.titre}" if o.titre else f"à {o.ville}"
    phrases = [f"Madame, Monsieur, je souhaite candidater pour le logement {lieu} "
               f"({'T' + str(o.pieces) + ', ' if o.pieces else ''}{o.ville or 'Paris'})."]
    for cle, v in champs.items():
        if cle.lower().startswith(("nom", "prénom", "prenom", "téléphone", "telephone", "email", "mail")):
            continue
        phrases.append(f"{cle} : {v}.")
    phrases.append("Ce logement correspond à ma situation et à mon budget. "
                   "Je reste à votre disposition pour tout complément. Cordialement.")
    return tronquer(" ".join(phrases), maxi)


PROMPT = """Tu rédiges le motif d'une candidature à un logement social de la Ville de Paris (LOC'Annonces).

RÈGLES STRICTES :
- Utilise UNIQUEMENT les faits du PROFIL et de l'ANNONCE ci-dessous. N'invente rien : aucun chiffre, aucune date, aucun employeur, aucun lien avec le quartier qui n'y figure pas.
- Si une information manque, n'en parle pas.
- Français, ton sobre et sincère, à la première personne, sans formule pompeuse.
- {maxi} caractères MAXIMUM, espaces compris. Pas de titre, pas d'objet, pas de signature avec nom inventé.
- Réponds uniquement par le texte du motif.

PROFIL :
{profil}

ANNONCE :
{annonce}
"""


class GenerateurMotif:
    def __init__(self, url: str = "http://127.0.0.1:11434", modele: str = "qwen2.5:7b-instruct",
                 timeout: int = 40, profil: Path = PROFIL, session: Optional[requests.Session] = None):
        self.url, self.modele, self.timeout, self.profil = url.rstrip("/"), modele, timeout, profil
        self.session = session or requests.Session()

    def _llm(self, prompt: str) -> str:
        r = self.session.post(f"{self.url}/api/generate", timeout=self.timeout, json={
            "model": self.modele, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.3, "num_predict": 700}})
        r.raise_for_status()
        return (r.json().get("response") or "").strip()

    def generer(self, offre: Offre, maxi: int = 1000) -> Motif:
        champs = lire_profil(self.profil)
        if not champs:
            log.warning("profil_candidat.md vide ou absent : motif minimal")
        profil_txt, annonce_txt = texte_profil(champs), contexte_annonce(offre)
        try:
            brut = self._llm(PROMPT.format(maxi=maxi, profil=profil_txt or "(vide)", annonce=annonce_txt))
            brut = brut.strip().strip('"').strip()
            if len(brut) < 80:
                raise ValueError("réponse trop courte")
            inventes = faits_inventes(brut, profil_txt + "\n" + annonce_txt + f"\n{maxi}")
            if inventes:
                raise ValueError(f"chiffres absents du profil/annonce : {', '.join(inventes[:5])}")
            return Motif(tronquer(brut, maxi), "llm")
        except Exception as e:
            log.warning("Motif LLM indisponible → secours (%s)", e)
            return Motif(motif_secours(offre, champs, maxi), "secours", str(e)[:200])
