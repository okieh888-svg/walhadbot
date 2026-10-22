"""Mémoire persistante par plateforme : offres vues, ligne de base, candidatures.

Règle absolue : au premier passage réussi, TOUTES les offres présentes sont
marquées vues et jamais traitées. Ensuite seules les offres apparues après
sont renvoyées par filtrer_nouvelles().
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .offre import Offre


def maintenant() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.astimezone()
    except ValueError:
        return None


@dataclass
class InfoPassage:
    ligne_de_base: bool = False     # premier passage : tout a été marqué vu
    nb_ignorees: int = 0
    anomalie: bool = False          # trop de « nouvelles » d'un coup
    nb_anomalie: int = 0
    anciennes_par_date: int = 0     # publiées avant le premier démarrage


class Memoire:
    def __init__(self, dossier: Path, seuil_anomalie: int = 15):
        self.dossier = Path(dossier)
        self.dossier.mkdir(parents=True, exist_ok=True)
        self.fichier = self.dossier / "etat.json"
        self.seuil = seuil_anomalie
        self._verrou = threading.RLock()
        self.etat = {"initialise": False, "premier_demarrage": None, "vues": {},
                     "candidatures": [], "derniere_lecture": None, "derniere_erreur": None}
        if self.fichier.exists():
            self.etat.update(json.loads(self.fichier.read_text(encoding="utf-8")))

    def sauver(self) -> None:
        with self._verrou:
            tmp = self.fichier.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.etat, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.fichier)

    @property
    def initialise(self) -> bool:
        return bool(self.etat["initialise"])

    def deja_vue(self, offre_id: str) -> bool:
        return offre_id in self.etat["vues"]

    def marquer_vue(self, offre: Offre, statut: str = "vue") -> None:
        with self._verrou:
            self.etat["vues"][offre.id] = {"t": maintenant(), "statut": statut, "resume": offre.resume()}
            self.sauver()

    def filtrer_nouvelles(self, offres: list[Offre]) -> tuple[list[Offre], InfoPassage]:
        """À appeler uniquement après une lecture COMPLÈTE (toutes pages) et réussie."""
        info = InfoPassage()
        with self._verrou:
            self.etat["derniere_lecture"] = maintenant()
            if not self.etat["initialise"]:
                for o in offres:
                    self.etat["vues"][o.id] = {"t": maintenant(), "statut": "ligne_de_base", "resume": o.resume()}
                self.etat["initialise"] = True
                self.etat["premier_demarrage"] = maintenant()
                self.sauver()
                info.ligne_de_base, info.nb_ignorees = True, len({o.id for o in offres})
                return [], info

            vues = self.etat["vues"]
            uniques: dict[str, Offre] = {}
            for o in offres:
                if o.id not in vues:
                    uniques.setdefault(o.id, o)   # même offre sur deux pages

            debut = _date(self.etat["premier_demarrage"])
            gardees = []
            for o in uniques.values():
                pub = _date(o.date_publication)
                date_seule = bool(o.date_publication) and len(o.date_publication.strip()) == 10
                # une date sans heure (AAAA-MM-JJ) n'est « avant » que si le JOUR est antérieur
                avant = pub and debut and (pub.date() < debut.date() if date_seule else pub < debut)
                if avant:
                    vues[o.id] = {"t": maintenant(), "statut": "publiee_avant_demarrage", "resume": o.resume()}
                    info.anciennes_par_date += 1
                else:
                    gardees.append(o)

            if len(gardees) > self.seuil:
                for o in gardees:
                    vues[o.id] = {"t": maintenant(), "statut": "anomalie", "resume": o.resume()}
                info.anomalie, info.nb_anomalie = True, len(gardees)
                gardees = []
            self.sauver()
            return gardees, info

    def noter_candidature(self, offre: Offre, dry_run: bool, details: dict | None = None) -> None:
        with self._verrou:
            self.etat["candidatures"].append({"id": offre.id, "t": maintenant(), "dry_run": dry_run,
                                              "resume": offre.resume(), "url": offre.url, **(details or {})})
            self.sauver()

    def noter_erreur(self, erreur: str | None) -> None:
        with self._verrou:
            self.etat["derniere_erreur"] = erreur
            self.sauver()
