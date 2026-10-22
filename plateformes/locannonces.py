"""LOC'Annonces (Ville de Paris) — https://teleservices.paris.fr/locannonces/

Site Lutece rendu côté serveur, connexion par le SSO Mon Paris (Keycloak).
Voir exploration/locannonces/OBSERVATIONS.md.

⚠️ L'étape qui suit le bouton « Candidater » (formulaire POST) n'a pas pu être observée sans
risquer une vraie candidature. En dry-run l'agent s'arrête AVANT ce bouton. En production il
gère les deux cas : page avec champ motif (rempli puis validé) ou enregistrement direct.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from coeur.offre import Offre

from .base import Adaptateur, ConnexionImpossible, ResultatCandidature, SessionExpiree, _num

MOIS = {"janv": 1, "févr": 2, "fevr": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6, "juil": 7, "août": 8,
        "aout": 8, "sept": 9, "oct": 10, "nov": 11, "déc": 12, "dec": 12}

JS_CARTES = """() => [...document.querySelectorAll('#logts a[href*="logement/"]')].map(a => ({
  href: a.getAttribute('href'),
  loyer: (a.querySelector('.loyer') || {}).innerText || '',
  loc: (a.querySelector('.loc') || {}).innerText || '',
  surface: (a.querySelector('.surface') || {}).innerText || '',
  pieces: (a.querySelector('.pieces') || {}).innerText || '',
  limite: (a.querySelector('.dateLimite') || {title: ''}).title || ''
}))"""

JS_FICHE = """() => {
  const t = s => (document.querySelector(s) || {}).innerText || '';
  const dl = {};
  document.querySelectorAll('dl > div').forEach(d => {
    const k = (d.querySelector('dt') || {}).innerText, v = (d.querySelector('dd') || {}).innerText;
    if (k) dl[k.trim().toLowerCase()] = (v || '').trim();
  });
  return {
    titre: t('h1'), nbCand: t('p.nbCand strong'), nbCandTexte: t('p.nbCand'),
    limite: (document.querySelector('p.dateLimCand') || {title: ''}).title || '',
    adeq: t('section.adeq'), obs: t('.obs'), loc: t('p.loc'), dl: dl,
    bouton: !!document.querySelector('#linkPostuler'),
    corps: document.body.innerText.slice(0, 20000)
  };
}"""


def parse_loc(loc: str) -> tuple[str, str]:
    """« PARIS 20e » → (Paris, 75020) ; « MONTREUIL 93 » → (MONTREUIL, 93)."""
    loc = re.sub(r"\s+", " ", loc or "").strip()
    m = re.match(r"^paris\s*(\d{1,2})\s*(?:e|er|ème|eme)?\b", loc, re.I)
    if m:
        return "Paris", f"750{int(m.group(1)):02d}"
    m = re.match(r"^(.*?)\s+(\d{2,5})$", loc)
    if m:
        return m.group(1).strip(), m.group(2)
    return loc, ""


def parse_date_fr(texte: str) -> Optional[str]:
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", texte or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


class AdaptateurLocAnnonces(Adaptateur):
    nom = "locannonces"
    url_base = "https://teleservices.paris.fr/locannonces"
    besoin_motif = True

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.motif_max = int(os.environ.get("PARIS_MOTIF_MAX", "1000"))

    # ---------------------------------------------------------------- connexion
    def _accueil(self) -> None:
        self.page.goto(self.url_base + "/", wait_until="domcontentloaded")
        try:   # aller-retour OIDC prompt=none
            self.page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

    def _connecte(self) -> bool:
        # Déconnecté, la page affiche TOUTES les annonces et une synthèse « notAvailable ».
        return self.page.locator("#logts section.synthese:not(.notAvailable)").count() > 0

    def _refuser_cookies(self) -> None:
        try:
            b = self.page.locator("#tarteaucitronAllDenied")
            if b.count() and b.is_visible():
                b.click()
        except Exception:
            pass

    def connexion(self) -> None:
        self._accueil()
        if self._connecte():
            self.log.info("session LOC'Annonces valide")
            return
        identifiant, mdp = self.identifiants_ou_erreur("PARIS_LOGIN / PARIS_PASSWORD")
        self.log.info("connexion Mon Paris…")
        # Point d'entrée OIDC du site (sans prompt=none) → page de connexion Mon Paris (Keycloak).
        # Le lien « Connexion » de la bannière est caché dans un menu : on ne s'en sert qu'en secours.
        self.page.goto(self.url_base + "/servlet/plugins/oauth2/callback?data_client=authData",
                       wait_until="domcontentloaded")
        if not self.page.locator("#username").count():
            lien = self.page.locator("a.banner-mon-compte_menu__connexion:visible, a:visible:has-text('Connexion')").first
            if lien.count():
                lien.click()
        self.page.locator("#username").wait_for(timeout=30_000)
        self._refuser_cookies()
        if self.page.get_by_text("captcha", exact=False).count():
            raise ConnexionImpossible("captcha sur Mon Paris : connexion manuelle nécessaire")
        self.page.locator("#username").fill(identifiant)
        self.page.locator("#password").fill(mdp)
        self.page.locator("button[type=submit][name=Submit], button:has-text('Continuer')").first.click()
        try:
            self.page.wait_for_url(lambda u: u.startswith(self.url_base), timeout=30_000)
        except Exception:
            pass
        self._accueil()
        if not self._connecte():
            capture = self.capture("connexion-echec")
            raise ConnexionImpossible(f"connexion Mon Paris échouée (code, captcha ou identifiants ?) — {capture}")
        self.log.info("connecté à LOC'Annonces")

    # ---------------------------------------------------------------- lecture
    def lister_offres(self) -> list[Offre]:
        self._accueil()
        if not self._connecte():
            raise SessionExpiree("page d'accueil sans synthèse personnelle")
        offres: dict[str, Offre] = {}
        for _ in range(20):                       # pagination éventuelle
            for c in self.page.evaluate(JS_CARTES):
                m = re.search(r"logement/(\d+)", c["href"])
                if not m:
                    continue
                ville, cp = parse_loc(c["loc"])
                pieces = re.search(r"\d+", c["pieces"])
                offres.setdefault(m.group(1), Offre(
                    plateforme=self.nom, id=m.group(1), url=f"{self.url_base}/logement/{m.group(1)}",
                    ville=ville, code_postal=cp, loyer=_num(c["loyer"]),
                    loyer_cc="CCC" in c["loyer"] or "CC" in c["loyer"],
                    surface=_num(c["surface"]), pieces=int(pieces.group()) if pieces else None,
                    date_limite=parse_date_fr(c["limite"]), brut=c))
            suivant = self.page.locator("#logts a[rel=next], #logts .pagination a:has-text('Suivant'), "
                                        "#logts a:has-text('›')").first
            if not suivant.count():
                break
            suivant.click()
            self.page.wait_for_load_state("domcontentloaded")
        self.debug_json("offres", [o.to_dict() for o in offres.values()])
        return list(offres.values())

    def _lire_fiche(self, offre: Offre) -> dict:
        self.page.goto(offre.url, wait_until="domcontentloaded")
        f = self.page.evaluate(JS_FICHE)
        if "en vous connectant" in f["corps"] and not f["bouton"]:
            raise SessionExpiree("fiche affichée en mode déconnecté")
        return f

    def enrichir(self, offre: Offre) -> Offre:
        f = self._lire_fiche(offre)
        self.debug_json(f"fiche-{offre.id}", f)
        if f["nbCand"]:
            offre.nb_candidats = int(_num(f["nbCand"]))
        elif re.search(r"aucune? (personne|candidat)", f["nbCandTexte"] + f["corps"], re.I):
            offre.nb_candidats = 0
        dl = f["dl"]
        if dl.get("étage"):
            offre.etage = dl["étage"].split("\n")[0].strip()
        loyer_hc, charges = _num(dl.get("loyer")), _num(dl.get("charges"))
        if loyer_hc is not None and charges is not None:
            offre.loyer, offre.loyer_cc = loyer_hc + charges, True
        m = re.search(r"plafond\s+([A-D])\s*\((PLAI|PLUS|PLS|PLI)\)", f["adeq"], re.I)
        if m:
            offre.financement = f"{m.group(1).upper()} ({m.group(2).upper()})"
        offre.date_limite = parse_date_fr(f["limite"]) or offre.date_limite
        offre.titre = f["titre"]
        offre.description = "\n".join(x for x in (f["obs"], f["adeq"], f["loc"]) if x)[:2000]
        corps = f["corps"].lower()
        # Éligibilité : on fait confiance au site (choix de l'utilisateur)
        if "correspond à votre profil" in corps:
            offre.eligible = True
        elif re.search(r"ne correspond pas à votre (profil|situation)|vous ne pouvez pas (postuler|candidater)", corps):
            offre.eligible = False
        if not f["bouton"] and "annuler la candidature" not in corps:
            offre.eligible = False if offre.eligible is None else offre.eligible
        return offre

    def nombre_candidats(self, offre: Offre) -> Optional[int]:
        f = self._lire_fiche(offre)
        if f["nbCand"]:
            return int(_num(f["nbCand"]))
        return offre.nb_candidats

    def candidature_active(self) -> bool:
        self.page.goto(self.url_base + "/candidature.html", wait_until="domcontentloaded")
        texte = self.page.inner_text("body")
        if "candidature en cours" not in texte.lower():
            raise SessionExpiree("page « Candidature » sans le bloc CANDIDATURE EN COURS (déconnecté ?)")
        if re.search(r"n'avez pas de candidature en cours", texte, re.I):
            return False
        self.log.info("candidature LOC'Annonces en cours détectée")
        return True   # tout autre contenu (ou format inconnu) : prudence, on considère active

    # ---------------------------------------------------------------- candidature
    def candidater(self, offre: Offre, motif: Optional[str], dry_run: bool) -> ResultatCandidature:
        f = self._lire_fiche(offre)
        if not f["bouton"]:
            return ResultatCandidature(False, "bouton « Candidater » absent de la fiche", self.capture(f"sans-bouton-{offre.id}"))
        if dry_run:
            capture = self.capture(f"dryrun-avant-candidater-{offre.id}")
            return ResultatCandidature(True, "DRY-RUN : arrêt AVANT le bouton « Candidater » (ce clic peut déjà "
                                             "enregistrer la candidature). Motif généré ci-dessous, non envoyé.", capture)

        self.page.locator("#linkPostuler").click()
        self.page.wait_for_load_state("domcontentloaded")
        self.page.wait_for_timeout(1000)
        texte = self.page.inner_text("body")
        bas = texte.lower()
        # Fenêtre « enregistrer cette candidature en annulant la précédente » : on n'accepte jamais.
        if re.search(r"annul\w* (votre|la) (candidature )?pr[ée]c[ée]dente|remplac", bas):
            return ResultatCandidature(False, "le site propose d'annuler la candidature précédente : refusé",
                                       self.capture(f"remplacement-{offre.id}"))
        champ = self.page.locator("form textarea").first
        if champ.count():
            maxi = champ.get_attribute("maxlength")
            if maxi and maxi.isdigit() and motif and len(motif) > int(maxi):
                from coeur.motif import tronquer
                motif = tronquer(motif, int(maxi))
            champ.fill(motif or "")
            for case in self.page.locator("form input[type=checkbox][required]").all():
                if not case.is_checked():
                    case.check()
            self.capture(f"avant-envoi-{offre.id}")
            form = champ.locator("xpath=ancestor::form")
            form.locator("button[type=submit], input[type=submit]").first.click()
            self.page.wait_for_load_state("domcontentloaded")
            self.page.wait_for_timeout(1000)
            texte = self.page.inner_text("body")
            bas = texte.lower()
        capture = self.capture(f"apres-candidature-{offre.id}")
        if re.search(r"candidature (a bien été |a été )?(enregistr|prise en compte|envoy|transmise)|confirm", bas):
            return ResultatCandidature(True, "candidature enregistrée par le site", capture, envoye=True)
        if "annuler la candidature" in bas:
            return ResultatCandidature(True, "candidature en cours sur cette annonce (bouton « Annuler » présent)",
                                       capture, envoye=True)
        return ResultatCandidature(False, "résultat inattendu après « Candidater » (voir capture)", capture,
                                   envoye=True)
