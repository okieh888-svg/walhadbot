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


# ============================================================================ critères par personne
import json as _json
import logging as _logging
import threading as _threading
from pathlib import Path as _Path

_log = _logging.getLogger("criteres")


class ResolveurCP:
    """Nom de commune + département → codes postaux (api géo de l'État, mis en cache sur disque).

    Sert pour les annonces sans code postal complet (LOC'Annonces : « MONTREUIL 93 »)."""

    def __init__(self, cache: _Path, session=None):
        self.cache = _Path(cache)
        self.session = session
        self._verrou = _threading.Lock()
        try:
            self._d = _json.loads(self.cache.read_text(encoding="utf-8"))
        except Exception:
            self._d = {}

    def codes(self, ville: str, departement: str) -> list[str]:
        cle = f"{departement}:{_nom_commune(ville)}"
        if cle in self._d:
            return self._d[cle]
        nom = _nom_commune(ville)
        connus = [cp for n, cp in COMMUNES_93.items() if n == nom]
        if connus:
            codes = sorted(CP_93 & {c for c in CP_93 if c.startswith(connus[0][:4])}) or connus
        else:
            try:
                import requests
                r = (self.session or requests).get("https://geo.api.gouv.fr/communes", timeout=8, params={
                    "nom": ville, "codeDepartement": departement, "fields": "nom,codesPostaux", "limit": 5})
                res = [c for c in r.json() if _nom_commune(c.get("nom", "")) == nom] or r.json()[:1]
                codes = sorted({cp for c in res for cp in c.get("codesPostaux", [])})
            except Exception as e:
                _log.debug("api géo indisponible pour %s : %s", cle, e)
                return []
        with self._verrou:
            self._d[cle] = codes
            try:
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                self.cache.write_text(_json.dumps(self._d, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
        return codes


def codes_postaux_offre(offre: Offre, resolveur: "ResolveurCP | None" = None) -> list[str]:
    cp = (offre.code_postal or "").strip()
    if re.fullmatch(r"\d{5}", cp):
        return [cp]
    if est_paris(offre):
        m = re.search(r"\b(\d{1,2})\s*(e|er|eme)?\b", normaliser(offre.ville))
        return [f"750{int(m.group(1)):02d}"] if m else ["75"]
    dep = cp[:2] if re.fullmatch(r"\d{2,3}", cp) else ""
    if dep and resolveur and offre.ville:
        trouves = resolveur.codes(offre.ville, dep)
        if trouves:
            return trouves
    return [dep] if dep else []


def _correspond(code_offre: str, motif: str) -> bool:
    """« 75 » couvre 75001…75020 ; « 93100 » exige le code exact. Un code d'offre partiel (« 93 »)
    ne couvre qu'un motif de même niveau."""
    if len(code_offre) == 5:
        return code_offre.startswith(motif)
    return motif == code_offre


@dataclass
class Criteres:
    zones: list  # list[Zone]
    pieces_min: int = 2
    surface_min: float = 40.0
    loyer_max_plateforme: "float | None" = None

    @classmethod
    def depuis_personne(cls, personne, plateforme: str) -> "Criteres":
        r = personne.plateformes.get(plateforme)
        return cls(personne.zones, personne.pieces_min, personne.surface_min, r.loyer_max if r else None)

    def classer(self, offre: Offre, resolveur: "ResolveurCP | None" = None) -> Verdict:
        if offre.pieces is None or offre.pieces < self.pieces_min:
            return Verdict(False, f"pièces {offre.pieces} < {self.pieces_min}")
        if offre.surface is None or offre.surface < self.surface_min:
            return Verdict(False, f"surface {offre.surface} < {self.surface_min:g}")
        if offre.loyer is None:
            return Verdict(False, "loyer inconnu")
        codes = codes_postaux_offre(offre, resolveur)
        for i, z in enumerate(self.zones):
            if any(_correspond(c, m) for c in codes for m in z.codes_postaux):
                plafond = z.loyer_max if self.loyer_max_plateforme is None else min(z.loyer_max, self.loyer_max_plateforme)
                if offre.loyer > plafond:
                    return Verdict(False, f"{offre.ville or codes[0]} : loyer {offre.loyer:.0f} > {plafond:.0f}")
                return Verdict(True, f"zone {', '.join(z.codes_postaux[:4])}{'…' if len(z.codes_postaux) > 4 else ''} "
                                     f"≤ {plafond:.0f} €", prioritaire=(i == 0))
        return Verdict(False, f"hors zones : {offre.ville or '?'} {offre.code_postal or ''}".strip())
