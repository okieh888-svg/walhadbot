"""Mots de passe chiffrés avec la protection des données Windows (DPAPI, liée à ton compte Windows).

Un fichier par personne et par plateforme dans data/personnes/<id>/secrets/. Le texte clair n'est
jamais écrit sur le disque, ni affiché, ni journalisé. Sous un autre système (tests, Docker), repli
sur un simple encodage signalé comme NON protégé.
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
import sys
from pathlib import Path

ENTROPIE = b"agent-logement/v1"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _sortie(b: _Blob) -> bytes:
    try:
        return ctypes.string_at(b.pbData, b.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(b.pbData)


def chiffrer(texte: str) -> bytes:
    if sys.platform != "win32":
        return b"NONPROTEGE:" + base64.b64encode(texte.encode())
    entree, ent, sortie = _blob(texte.encode("utf-8")), _blob(ENTROPIE), _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(entree), "agent-logement", ctypes.byref(ent),
                                                  None, None, 0x1, ctypes.byref(sortie)):
        raise OSError("CryptProtectData a échoué")
    return _sortie(sortie)


def dechiffrer(donnees: bytes) -> str:
    if donnees.startswith(b"NONPROTEGE:"):
        return base64.b64decode(donnees[len(b"NONPROTEGE:"):]).decode()
    entree, ent, sortie = _blob(donnees), _blob(ENTROPIE), _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(entree), None, ctypes.byref(ent),
                                                    None, None, 0x1, ctypes.byref(sortie)):
        raise OSError("CryptUnprotectData a échoué (autre compte Windows ?)")
    return _sortie(sortie).decode("utf-8")


class Coffre:
    """Mots de passe d'une personne : un fichier chiffré par plateforme."""

    def __init__(self, dossier: Path):
        self.dossier = Path(dossier)

    def _f(self, plateforme: str) -> Path:
        return self.dossier / f"{plateforme}.bin"

    def enregistrer(self, plateforme: str, mot_de_passe: str) -> None:
        self.dossier.mkdir(parents=True, exist_ok=True)
        self._f(plateforme).write_bytes(chiffrer(mot_de_passe))

    def lire(self, plateforme: str) -> str:
        f = self._f(plateforme)
        return dechiffrer(f.read_bytes()) if f.exists() else ""

    def present(self, plateforme: str) -> bool:
        return self._f(plateforme).exists()

    def effacer(self, plateforme: str) -> None:
        f = self._f(plateforme)
        if f.exists():
            f.unlink()
