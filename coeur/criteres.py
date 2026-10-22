"""Critères de recherche communs + exceptions par plateforme.

classer(offre) -> Verdict(ok, raison, prioritaire)
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .offre import Offre

PIECES_MIN = 2
SURFACE_MIN = 40.0
LOYER_MAX_PARIS = 880.0
LOYER_MAX_93 = 800.0
LOYER_MAX_PARIS_LOCANNONCES = 850.0

# Les 8 communes du 93 limitrophes de Paris (code postal, nom normalisé)
COMMUNES_93 = {
    "saint ouen sur seine": "93400",
    "saint denis": "93200",          # 93200 / 93210
    "aubervilliers": "93300",
    "pantin": "93500",
    "le pre saint gervais": "93310",
    "les lilas": "93260",
    "bagnolet": "93170",
    "montreuil": "93100",
}
CP_93 = {"93400", "93200", "93210", "93300", "93500", "93310", "93260", "93170", "93100"}
PRIORITAIRES = {"pantin", "montreuil"}
ALIAS = {"saint ouen": "saint ouen sur seine", "pre saint gervais": "le pre saint gervais",
         "lilas": "les lilas", "montreuil sous bois": "montreuil"}


def normaliser(txt: str) -> str:
    txt = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode()
    txt = re.sub(r"[^a-z0-9]+", " ", txt.lower()).strip()
    return txt


def _nom_commune(ville: str) -> str:
    v = normaliser(ville)
    v = re.sub(r"\b(cedex|\d+)\b", "", v)
    v = re.sub(r"\bst\b", "saint", v)
    v = re.sub(r"\bste\b", "sainte", v)
    v = re.sub(r"\s+", " ", v).strip()
    return ALIAS.get(v, v)


def commune_93(offre: Offre) -> str | None:
    """Nom normalisé de la commune 93 limitrophe, ou None.

    Un code postal hors 93 exclut la commune (il existe d'autres Montreuil, Saint-Denis…).
    Saint-Denis inclut 93380 depuis la fusion avec Pierrefitte (2025)."""
    cp = (offre.code_postal or "").strip()
    if cp and not cp.startswith("93"):
        return None
    v = _nom_commune(offre.ville)
    if v in COMMUNES_93:
        return v
    if cp in CP_93 and not v:
        for nom, cp in COMMUNES_93.items():
            if cp == offre.code_postal:
                return nom
    return None


def est_paris(offre: Offre) -> bool:
    cp = (offre.code_postal or "").strip()
    if re.fullmatch(r"75\d{3}", cp):
        return True
    if cp and cp != "75":
        return False
    v = normaliser(offre.ville)
    return bool(re.match(r"^paris( \d+ ?(e|er|eme)?)?( arrondissement)?$", v))


@dataclass
class Verdict:
    ok: bool
    raison: str
    prioritaire: bool = False

    def __bool__(self):
        return self.ok


def classer(offre: Offre) -> Verdict:
    if offre.pieces is None or offre.pieces < PIECES_MIN:
        return Verdict(False, f"pièces {offre.pieces} < {PIECES_MIN}")
    if offre.surface is None or offre.surface < SURFACE_MIN:
        return Verdict(False, f"surface {offre.surface} < {SURFACE_MIN}")
    if offre.loyer is None:
        return Verdict(False, "loyer inconnu")
    if est_paris(offre):
        plafond = LOYER_MAX_PARIS_LOCANNONCES if offre.plateforme == "locannonces" else LOYER_MAX_PARIS
        if offre.loyer > plafond:
            return Verdict(False, f"Paris : loyer {offre.loyer:.0f} > {plafond:.0f}")
        return Verdict(True, f"Paris ≤ {plafond:.0f} €")
    c = commune_93(offre)
    if c:
        if offre.loyer > LOYER_MAX_93:
            return Verdict(False, f"{c} : loyer {offre.loyer:.0f} > {LOYER_MAX_93:.0f}")
        return Verdict(True, f"93 limitrophe ({c}) ≤ {LOYER_MAX_93:.0f} €", prioritaire=c in PRIORITAIRES)
    return Verdict(False, f"commune hors zone : {offre.ville or '?'} {offre.code_postal or ''}".strip())
