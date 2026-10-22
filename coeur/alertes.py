"""Alertes maximales Action Logement : relance toutes les N secondes jusqu'au « ✅ Vu »."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from .telegram import Notifieur

log = logging.getLogger("alertes")


class GestionAlertes:
    def __init__(self, notifieur: Notifieur, fichier: Path, intervalle_s: int = 120, relances_max: int = 10,
                 horloge=time.time):
        self.notif = notifieur
        self.fichier = Path(fichier)
        self.intervalle = intervalle_s
        self.max = relances_max
        self.horloge = horloge
        self._verrou = threading.RLock()
        self.alertes: dict[str, dict] = {}
        if self.fichier.exists():
            self.alertes = json.loads(self.fichier.read_text(encoding="utf-8"))

    def _sauver(self):
        self.fichier.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.fichier.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.alertes, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.fichier)

    def nouvelle(self, plateforme: str, texte: str, photo: Optional[Path] = None) -> str:
        aid = uuid.uuid4().hex[:10]
        mid = self.notif.envoyer(plateforme, texte, photo=photo, boutons=[("✅ Vu", f"vu:{aid}")])
        with self._verrou:
            self.alertes[aid] = {"plateforme": plateforme, "texte": texte, "photo": str(photo) if photo else None,
                                 "relances": 0, "prochaine": self.horloge() + self.intervalle,
                                 "vu": False, "messages": [mid]}
            self._sauver()
        return aid

    def marquer_vu(self, aid: str) -> bool:
        with self._verrou:
            a = self.alertes.get(aid)
            if not a or a["vu"]:
                return False
            a["vu"] = True
            self._sauver()
            mids = list(a["messages"])
        for mid in mids:
            self.notif.retirer_boutons(mid)
        return True

    def en_attente(self) -> list[str]:
        return [k for k, a in self.alertes.items() if not a["vu"] and a["relances"] < self.max]

    def tick(self) -> int:
        """Renvoie les alertes échues. Retourne le nombre de relances envoyées."""
        n = 0
        now = self.horloge()
        with self._verrou:
            dues = [(k, a) for k, a in self.alertes.items()
                    if not a["vu"] and a["relances"] < self.max and a["prochaine"] <= now]
        for aid, a in dues:
            a["relances"] += 1
            entete = f"🔁 Relance {a['relances']}/{self.max}\n"
            photo = Path(a["photo"]) if a.get("photo") else None
            mid = self.notif.envoyer(a["plateforme"], entete + a["texte"], photo=photo,
                                     boutons=[("✅ Vu", f"vu:{aid}")])
            with self._verrou:
                a["messages"].append(mid)
                a["prochaine"] = now + self.intervalle
                self._sauver()
            n += 1
        # ménage : on garde 7 jours d'historique au plus
        with self._verrou:
            if len(self.alertes) > 200:
                for k in list(self.alertes)[:-200]:
                    del self.alertes[k]
                self._sauver()
        return n
