"""Journal avec masquage systématique des secrets (.env)."""
from __future__ import annotations

import logging
import logging.handlers
import sys

from .config import DATA, valeurs_secretes


class FiltreSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for s in valeurs_secretes():
            msg = msg.replace(s, "***")
        record.msg, record.args = msg, ()
        return True


def configurer(debug: bool = False) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
    racine = logging.getLogger()
    racine.handlers.clear()
    racine.setLevel(logging.DEBUG if debug else logging.INFO)
    fichier = logging.handlers.RotatingFileHandler(DATA / "agent.log", maxBytes=5_000_000,
                                                   backupCount=5, encoding="utf-8")
    try:   # console Windows en cp1252 : les émojis feraient échouer l'écriture
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    console = logging.StreamHandler(sys.stdout)
    for h in (fichier, console):
        h.setFormatter(fmt)
        h.addFilter(FiltreSecrets())
        racine.addHandler(h)
    for bruyant in ("urllib3", "asyncio"):
        logging.getLogger(bruyant).setLevel(logging.WARNING)
