"""Personnes surveillées : profil, critères, plateformes, Telegram (data/personnes.json).

Les mots de passe ne sont PAS ici : ils sont chiffrés à part (coeur/secrets.py).
"""
from __future__ import annotations

import json
import os
import re
import secrets as _alea
import threading
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .config import DATA
from .secrets import Coffre

PLATEFORMES = {
    "aljt": "Pass Logement (ALJT)",
    "locannonces": "LOC'Annonces (Paris)",
    "action_logement": "Action Logement",
    "alin": "AL'in",
}
FICHIER = DATA / "personnes.json"


@dataclass
class Zone:
    """Un ensemble de codes postaux (« 75 » = tout Paris, « 93100 » = Montreuil) et un loyer maximum."""
    codes_postaux: list[str] = field(default_factory=list)
    loyer_max: float = 900.0


@dataclass
class ReglagePlateforme:
    actif: bool = False
    production: bool = False             # False = mode test (aucune candidature réelle)
    identifiant: str = ""
    loyer_max: Optional[float] = None    # plafond propre à cette plateforme (ex. 850 € sur LOC'Annonces)


@dataclass
class Personne:
    id: str
    nom: str
    actif: bool = True
    zones: list[Zone] = field(default_factory=list)
    pieces_min: int = 2
    surface_min: float = 40.0
    notif_hors_criteres: bool = True
    telegram_chat_id: str = ""
    code_liaison: str = ""
    plateformes: dict[str, ReglagePlateforme] = field(default_factory=dict)

    @property
    def dossier(self) -> Path:
        return DATA / "personnes" / self.id

    @property
    def coffre(self) -> Coffre:
        return Coffre(self.dossier / "secrets")

    def reglage(self, p: str) -> ReglagePlateforme:
        return self.plateformes.setdefault(p, ReglagePlateforme())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Personne":
        d = dict(d)
        d["zones"] = [Zone(**z) for z in d.get("zones", [])]
        d["plateformes"] = {k: ReglagePlateforme(**v) for k, v in (d.get("plateformes") or {}).items()}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def identifiant_depuis_nom(nom: str, existants: set[str]) -> str:
    base = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-") or "personne"
    ident, n = base, 2
    while ident in existants:
        ident, n = f"{base}-{n}", n + 1
    return ident


def nouveau_code() -> str:
    return _alea.token_hex(3).upper()


def codes_depuis_texte(texte: str) -> list[str]:
    """« 75, 93100 93500 » → ['75', '93100', '93500'] (2 à 5 chiffres par code)."""
    return [c for c in re.findall(r"\b\d{2,5}\b", texte or "")]


class Annuaire:
    """Lecture / écriture atomique de data/personnes.json."""

    def __init__(self, fichier: Path = FICHIER):
        self.fichier = Path(fichier)
        self._verrou = threading.RLock()

    def charger(self) -> list[Personne]:
        if not self.fichier.exists():
            return []
        donnees = json.loads(self.fichier.read_text(encoding="utf-8"))
        return [Personne.from_dict(p) for p in donnees.get("personnes", [])]

    def sauver(self, personnes: list[Personne]) -> None:
        with self._verrou:
            self.fichier.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.fichier.with_suffix(".tmp")
            tmp.write_text(json.dumps({"personnes": [p.to_dict() for p in personnes]}, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, self.fichier)

    def obtenir(self, ident: str) -> Optional[Personne]:
        return next((p for p in self.charger() if p.id == ident), None)

    def enregistrer(self, personne: Personne) -> None:
        with self._verrou:
            tous = [p for p in self.charger() if p.id != personne.id]
            tous.append(personne)
            self.sauver(tous)

    def supprimer(self, ident: str) -> None:
        with self._verrou:
            self.sauver([p for p in self.charger() if p.id != ident])

    def par_chat(self, chat_id: str) -> Optional[Personne]:
        return next((p for p in self.charger() if p.telegram_chat_id and p.telegram_chat_id == str(chat_id)), None)

    def lier_telegram(self, code: str, chat_id: str) -> Optional[Personne]:
        with self._verrou:
            tous = self.charger()
            cible = next((p for p in tous if p.code_liaison and p.code_liaison == code.strip().upper()), None)
            if not cible:
                return None
            for p in tous:                       # un chat Telegram = une seule personne
                if p.telegram_chat_id == str(chat_id):
                    p.telegram_chat_id = ""
            cible.telegram_chat_id, cible.code_liaison = str(chat_id), ""
            self.sauver(tous)
            return cible
