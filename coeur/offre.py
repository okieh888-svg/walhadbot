"""Représentation commune d'une offre, quelle que soit la plateforme."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Offre:
    plateforme: str                      # "aljt" | "locannonces" | "action_logement"
    id: str                              # identifiant stable côté site
    url: str = ""
    titre: str = ""
    ville: str = ""
    code_postal: str = ""
    loyer: Optional[float] = None        # charges comprises si possible
    loyer_cc: bool = False               # True si `loyer` est charges comprises
    surface: Optional[float] = None
    pieces: Optional[int] = None
    etage: Optional[str] = None
    date_publication: Optional[str] = None   # ISO 8601 si connue
    date_limite: Optional[str] = None
    nb_candidats: Optional[int] = None   # None = inconnu
    financement: Optional[str] = None    # PLAI / PLUS / PLS / PLI (LOC'Annonces)
    eligible: Optional[bool] = None      # éligibilité telle qu'indiquée par le site (None = non dit)
    quota_restant: Optional[int] = None  # places de candidature libres dans la catégorie (Action Logement)
    description: str = ""
    brut: dict[str, Any] = field(default_factory=dict, repr=False)

    def resume(self) -> str:
        morceaux = [
            f"{self.ville or '?'} ({self.code_postal or '?'})",
            f"{self.loyer:.0f} €{' CC' if self.loyer_cc else ' HC'}" if self.loyer is not None else "loyer ?",
            f"{self.surface:g} m²" if self.surface else "surface ?",
            f"T{self.pieces}" if self.pieces else "pièces ?",
        ]
        if self.etage:
            morceaux.append(f"étage {self.etage}")
        if self.financement:
            morceaux.append(self.financement)
        if self.quota_restant is not None:
            morceaux.append(f"{self.quota_restant} place(s) de candidature libre(s)")
        if self.nb_candidats is not None:
            morceaux.append(f"{self.nb_candidats} candidat(s)")
        if self.date_limite:
            morceaux.append(f"limite {self.date_limite}")
        return " · ".join(morceaux)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("brut", None)
        return d
