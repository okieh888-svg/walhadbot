"""Interface commune des adaptateurs + outillage Playwright partagé."""
from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from coeur.offre import Offre


class SessionExpiree(Exception):
    """La session n'est plus valide : le moteur rappellera connexion()."""


class ConnexionImpossible(Exception):
    """Captcha, double authentification, identifiants absents… intervention humaine requise."""


class IdentifiantsRefuses(ConnexionImpossible):
    """Le site a refusé l'identifiant / le mot de passe : ne JAMAIS réessayer (blocage du compte
    après quelques échecs). Le moteur s'arrête jusqu'à ce que les identifiants soient modifiés."""


@dataclass
class ResultatCandidature:
    ok: bool
    message: str
    capture: Optional[Path] = None
    envoye: bool = False          # True seulement si le bouton final a été cliqué


class Adaptateur(ABC):
    nom: str = ""
    url_base: str = ""
    #: longueur maximale du champ motif (si la plateforme en a un)
    motif_max: Optional[int] = None
    besoin_motif: bool = False

    def __init__(self, dossier: Path, identifiants: tuple[str, str] = ("", ""), headless: bool = True,
                 canal: str = "chrome", debug: bool = False, url_base: Optional[str] = None):
        self.dossier = Path(dossier)
        self.dossier.mkdir(parents=True, exist_ok=True)
        (self.dossier / "captures").mkdir(exist_ok=True)
        self.identifiants = identifiants
        self.headless, self.canal, self.debug = headless, canal, debug
        if url_base:
            self.url_base = url_base.rstrip("/")
        self.log = logging.getLogger(self.nom)
        self.reduire = True            # fenêtre réduite quand le navigateur n'est pas headless
        self.connexion_auto = True     # False : jamais de saisie d'identifiants, connexion manuelle exigée
        self._pw = self.ctx = self.page = None

    def identifiants_ou_erreur(self, variables: str) -> tuple[str, str]:
        if not self.connexion_auto:
            raise ConnexionImpossible(f"session {self.nom} expirée et connexion automatique désactivée : "
                                      f"lance « python main.py --connexion {self.nom} »")
        identifiant, mdp = self.identifiants
        if not (identifiant and mdp):
            raise ConnexionImpossible(f"{variables} absents de .env (ou lance : python main.py --connexion {self.nom})")
        marque = self.dossier / "identifiants_refuses"
        if marque.exists():
            if self._empreinte(identifiant, mdp, marque) == marque.read_text(encoding="ascii").strip():
                raise IdentifiantsRefuses(f"{self.nom} : ces identifiants ont déjà été refusés par le site, "
                                          "aucun nouvel essai tant qu'ils ne sont pas modifiés")
            marque.unlink(missing_ok=True)                  # identifiants modifiés : on réessaie
        return identifiant, mdp

    @staticmethod
    def _empreinte(identifiant: str, mdp: str, marque: Optional[Path] = None, sel: Optional[bytes] = None) -> str:
        """Empreinte salée et lente (PBKDF2) : permet de reconnaître un mot de passe déjà refusé
        sans jamais l'écrire sur le disque."""
        import hashlib
        import os
        if sel is None:
            try:
                sel = bytes.fromhex(marque.read_text(encoding="ascii").split(":")[0]) if marque else None
            except Exception:
                sel = None
        sel = sel or os.urandom(16)
        h = hashlib.pbkdf2_hmac("sha256", f"{identifiant}\0{mdp}".encode(), sel, 200_000).hex()
        return f"{sel.hex()}:{h}"

    def noter_identifiants_refuses(self) -> None:
        identifiant, mdp = self.identifiants
        if identifiant and mdp:
            (self.dossier / "identifiants_refuses").write_text(self._empreinte(identifiant, mdp, sel=None),
                                                                encoding="ascii")

    # ---- cycle de vie du navigateur (un par plateforme, profil persistant) -------
    def demarrer(self) -> None:
        if self.ctx:
            return
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        opts: dict[str, Any] = dict(headless=self.headless, locale="fr-FR", timezone_id="Europe/Paris",
                                    viewport={"width": 1366, "height": 900})
        if self.canal:
            opts["channel"] = self.canal
        if not self.headless and self.reduire:
            # réduite ET hors de l'écran : rien ne s'affiche ni ne vole le focus
            opts["args"] = ["--start-minimized", "--window-position=-32000,-32000"]
        self.ctx = self._pw.chromium.launch_persistent_context(str(self.dossier / "navigateur"), **opts)
        self.ctx.set_default_timeout(30_000)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def fermer(self) -> None:
        for f in (lambda: self.ctx and self.ctx.close(), lambda: self._pw and self._pw.stop()):
            try:
                f()
            except Exception:
                pass
        self._pw = self.ctx = self.page = None

    def capture(self, nom: str, page=None) -> Optional[Path]:
        page = page or self.page
        if not page:
            return None
        chemin = self.dossier / "captures" / f"{time.strftime('%Y%m%d-%H%M%S')}-{re.sub(r'[^A-Za-z0-9_-]', '_', nom)[:60]}.png"
        try:
            try:   # bandeau rouge temporaire, retiré juste après la capture
                page.evaluate("""(t) => { const d = document.createElement('div'); d.id = '__bandeau_agent';
                    d.textContent = '🔴 AGENT LOGEMENT — ' + t;
                    d.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#d50000;' +
                      'color:#fff;font:bold 22px sans-serif;padding:10px 16px;text-align:center';
                    document.body.appendChild(d); }""", f"{self.nom.upper()} · {nom}")
            except Exception:
                pass
            page.screenshot(path=str(chemin), full_page=True,
                            mask=[page.locator("input[type=password]")])
            try:
                page.evaluate("() => { const d = document.getElementById('__bandeau_agent'); if (d) d.remove(); }")
            except Exception:
                pass
            return chemin
        except Exception as e:
            self.log.debug("capture impossible : %s", e)
            return None

    def debug_json(self, nom: str, donnees: Any) -> None:
        if self.debug:
            (self.dossier / "debug").mkdir(exist_ok=True)
            (self.dossier / "debug" / f"{nom}.json").write_text(
                json.dumps(donnees, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    def est_connecte(self) -> bool:
        """Vérifie la session sans jamais saisir d'identifiants."""
        auto, self.connexion_auto = self.connexion_auto, False
        try:
            self.connexion()
            return True
        except (ConnexionImpossible, SessionExpiree):
            return False
        finally:
            self.connexion_auto = auto

    def attendre_connexion_manuelle(self, delai_s: int = 600) -> None:
        """Navigateur visible : laisse l'utilisateur se connecter lui-même, puis continue."""
        self.page.goto(self.url_base + ("/account" if self.nom == "aljt" else "/"), wait_until="domcontentloaded")
        print(f"\n>>> Connecte-toi à {self.nom} dans la fenêtre Chrome ouverte (délai {delai_s // 60} min)…", flush=True)
        fin = time.time() + delai_s
        while time.time() < fin:
            self.page.wait_for_timeout(5000)
            try:
                if self.nom == "locannonces" and "teleservices" not in self.page.url and "127.0.0.1" not in self.page.url:
                    continue          # en cours sur la page Mon Paris : ne pas l'interrompre
                if self.nom == "action_logement" and "authentification" in self.page.url:
                    continue
                if self.nom == "aljt" and not self.page.get_by_text("Les offres", exact=True).count():
                    continue
                if self.est_connecte():
                    print(">>> Connexion détectée.", flush=True)
                    return
            except Exception:
                pass
        raise ConnexionImpossible("connexion manuelle non effectuée dans le délai")

    # ---- interface commune ---------------------------------------------------
    @abstractmethod
    def connexion(self) -> None: ...

    @abstractmethod
    def lister_offres(self) -> list[Offre]:
        """Toutes les offres de toutes les pages. Lève SessionExpiree si déconnecté."""

    def enrichir(self, offre: Offre) -> Offre:
        """Complète une offre CORRESPONDANTE avec sa fiche (nb de candidats, étage, éligibilité…)."""
        return offre

    def nombre_candidats(self, offre: Offre) -> Optional[int]:
        """Nombre de candidats actuel (relu sur le site), None si non affiché."""
        return offre.nb_candidats

    def candidature_active(self) -> bool:
        return False

    def resume_candidatures(self) -> str:
        """Texte court pour /statut (relu à chaque passage)."""
        return "en cours" if self.candidature_active() else "aucune en cours"

    def candidater(self, offre: Offre, motif: Optional[str], dry_run: bool) -> ResultatCandidature:
        raise NotImplementedError

    def capture_offre(self, offre: Offre) -> Optional[Path]:
        if not offre.url or not self.page:
            return None
        try:
            p = self.ctx.new_page()
            p.goto(offre.url, wait_until="domcontentloaded")
            try:
                p.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            p.wait_for_timeout(800)
            chemin = self.capture(f"offre-{offre.id}", page=p)
            p.close()
            return chemin
        except Exception as e:
            self.log.warning("capture de l'offre impossible : %s", e)
            return None


# ---- heuristique de secours : JSON quelconque -> Offre --------------------------

def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d[\d\s  ]*(?:[.,]\d+)?", str(v))
    if not m:
        return None
    try:
        return float(re.sub(r"[\s  ]", "", m.group()).replace(",", "."))
    except ValueError:
        return None


CLES = {
    "id": ("id", "idoffre", "offreid", "reference", "ref", "numero", "code"),
    "loyer": ("loyercc", "loyerchargescomprises", "loyertotal", "montanttotal", "loyer", "rent", "prix", "price",
              "montant", "loyerhc"),
    "surface": ("surface", "surfacehabitable", "superficie", "area", "m2"),
    "pieces": ("nbpieces", "nombrepieces", "pieces", "typologie", "typelogement", "type", "rooms"),
    "ville": ("ville", "commune", "city", "localite", "libellecommune"),
    "code_postal": ("codepostal", "cp", "zipcode", "postalcode"),
    "nb_candidats": ("nbcandidats", "nombrecandidats", "nbcandidatures", "nombrecandidatures", "candidats"),
    "etage": ("etage", "floor"),
    "date_publication": ("datepublication", "datemiseenligne", "datecreation", "publishedat", "createdat"),
    "date_limite": ("datelimite", "datefin", "datecloture", "deadline"),
}


def _plat(d: dict, prefixe: str = "") -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        cle = re.sub(r"[^a-z0-9]", "", f"{prefixe}{k}".lower())
        if isinstance(v, dict):
            out.update(_plat(v, ""))
        else:
            out.setdefault(cle, v)
    return out


def offre_depuis_dict(plateforme: str, d: dict, url_modele: str = "") -> Optional[Offre]:
    p = _plat(d)

    def prendre(champ):
        for c in CLES[champ]:
            if c in p and p[c] not in (None, ""):
                return p[c]
        return None

    ident = prendre("id")
    if ident is None:
        return None
    pieces = prendre("pieces")
    n_pieces = None
    if pieces is not None:
        m = re.search(r"(?:T|F)\s*(\d)", str(pieces), re.I) or re.search(r"\d", str(pieces))
        n_pieces = int(m.group(1) if m and m.groups() else m.group()) if m else None
    cp = prendre("code_postal")
    nb = prendre("nb_candidats")
    o = Offre(plateforme=plateforme, id=str(ident),
              url=url_modele.format(id=ident) if url_modele else "",
              ville=str(prendre("ville") or ""), code_postal=str(cp or "")[:5],
              loyer=_num(prendre("loyer")), surface=_num(prendre("surface")), pieces=n_pieces,
              etage=str(prendre("etage")) if prendre("etage") is not None else None,
              date_publication=str(prendre("date_publication")) if prendre("date_publication") else None,
              date_limite=str(prendre("date_limite")) if prendre("date_limite") else None,
              nb_candidats=int(_num(nb)) if _num(nb) is not None else None, brut=d)
    if not o.code_postal:
        m = re.search(r"\b(75\d{3}|9[1-5]\d{3})\b", o.ville)
        if m:
            o.code_postal = m.group(1)
    return o


def listes_candidates(donnees: Any, profondeur: int = 0):
    """Parcourt un JSON et rend toutes les listes de dicts (candidates à « liste d'offres »)."""
    if profondeur > 6:
        return
    if isinstance(donnees, list) and donnees and all(isinstance(x, dict) for x in donnees):
        yield donnees
    if isinstance(donnees, dict):
        for v in donnees.values():
            yield from listes_candidates(v, profondeur + 1)
    elif isinstance(donnees, list):
        for v in donnees:
            yield from listes_candidates(v, profondeur + 1)
