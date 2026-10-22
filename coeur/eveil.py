"""Garder le PC éveillé tant que l'agent tourne (sur secteur uniquement) et suivre les arrêts.

Utilise SetThreadExecutionState, comme un lecteur vidéo : aucun réglage Windows n'est modifié,
la demande disparaît dès que l'agent s'arrête. Sur batterie, la veille normale est respectée.
"""
from __future__ import annotations

import ctypes
import sys
import time
from datetime import datetime
from pathlib import Path

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class _Etat(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


def sur_secteur() -> bool:
    if sys.platform != "win32":
        return True
    e = _Etat()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(e)):
        return True
    return e.ACLineStatus != 0          # 1 = secteur, 255 = inconnu (PC fixe)


def maintenir_eveil(actif: bool) -> None:
    """À rappeler régulièrement : demande « système requis » si actif, sinon la retire."""
    if sys.platform != "win32":
        return
    etat = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if actif else 0)
    ctypes.windll.kernel32.SetThreadExecutionState(etat)


class Battement:
    """Horodatage écrit toutes les ~15 s ; au démarrage, l'écart révèle un arrêt du PC ou de l'agent."""

    def __init__(self, fichier: Path):
        self.fichier = Path(fichier)

    def dernier(self) -> float | None:
        try:
            return float(self.fichier.read_text().strip())
        except Exception:
            return None

    def battre(self) -> None:
        self.fichier.parent.mkdir(parents=True, exist_ok=True)
        self.fichier.write_text(str(time.time()))

    @staticmethod
    def message_arret(avant: float | None, maintenant: float, seuil_s: int = 180) -> str | None:
        if not avant or maintenant - avant < seuil_s:
            return None
        d, f = datetime.fromtimestamp(avant), datetime.fromtimestamp(maintenant)
        duree = int((maintenant - avant) / 60)
        jour = "" if d.date() == f.date() else f" (le {d:%d/%m})"
        return (f"⏻ L'agent était ARRÊTÉ de {d:%H:%M}{jour} à {f:%H:%M} — {duree} min sans surveillance "
                f"(PC éteint, redémarré ou agent coupé). Les offres encore en ligne vont être vérifiées.")
