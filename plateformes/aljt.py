"""Pass Logement (ALJT) — https://offres.passlogement.com/account

Application ExtJS ; on appelle directement ses API JSON depuis la page (même origine,
cookie de session, décodage du jeu de caractères par le navigateur). Voir
exploration/aljt/OBSERVATIONS.md.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from coeur.criteres import normaliser
from coeur.offre import Offre

from .base import (Adaptateur, ConnexionImpossible, ResultatCandidature, SessionExpiree, _num,
                   listes_candidates, offre_depuis_dict)

TRI_OFFRES = json.dumps([{"property": "dateCreated", "direction": "DESC"}])
TRI_CANDIDATURES = json.dumps([{"property": "app_date_updated", "direction": "DESC"}])

# Statuts « terminés » observés sur « Mes candidatures ». Tout autre statut = candidature active.
STATUTS_TERMINES = ("desist", "attribue a un autre", "plus disponible", "non transmis", "refus", "expir",
                    "annul", "rejet", "non retenu", "clotur", "retire", "abandon")

JS_FETCH = """async ([m, u, d]) => {
  const o = {method: m, credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}};
  if (d) { o.body = new URLSearchParams(d); }
  const r = await fetch(u, o);
  return [r.status, r.headers.get('content-type') || '', await r.text()];
}"""


class AdaptateurALJT(Adaptateur):
    nom = "aljt"
    url_base = "https://offres.passlogement.com"

    # ---------------------------------------------------------------- outils
    def _api(self, methode: str, chemin: str, params: Optional[dict] = None, data: Optional[dict] = None) -> dict:
        url = self.url_base + chemin
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        if not self.page.url.startswith(self.url_base):
            self.page.goto(self.url_base + "/account", wait_until="domcontentloaded")
        statut, ctype, texte = self.page.evaluate(JS_FETCH, [methode, url, data])
        if statut in (401, 403) or "json" not in ctype and "<html" in texte[:500].lower():
            raise SessionExpiree(f"{chemin} → {statut}")
        try:
            j = json.loads(texte)
        except ValueError:
            raise SessionExpiree(f"{chemin} : réponse non JSON ({statut})")
        st = j.get("_status") or {}
        if str(st.get("code")) == "1":
            raise SessionExpiree(st.get("message") or "session expirée")
        return j

    # ---------------------------------------------------------------- connexion
    def _verifier_session(self) -> None:
        """Hors connexion, les listes JSON répondent « 200, 0 élément » : seul cet appel le révèle."""
        j = self._api("GET", "/account/index/information")
        if j.get("success") is False and not j.get("cndData"):
            raise SessionExpiree("ALJT : non connecté")

    def _session_valide(self) -> bool:
        try:
            self._verifier_session()
            return True
        except SessionExpiree:
            return False

    def connexion(self) -> None:
        self.page.goto(self.url_base + "/account", wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)
        if self._session_valide():
            self.log.info("session ALJT valide")
            return
        identifiant, mdp = self.identifiants_ou_erreur("ALJT_EMAIL / ALJT_PASSWORD")
        self.log.info("connexion ALJT…")
        self.page.goto(self.url_base + "/account", wait_until="domcontentloaded")
        self.page.locator("#username-inputEl").fill(identifiant)
        self.page.locator("#password-inputEl").fill(mdp)
        self.page.get_by_text("S'identifier", exact=True).click()
        try:
            self.page.get_by_text("Les offres", exact=True).first.wait_for(timeout=20_000)
        except Exception:
            capture = self.capture("connexion-echec")
            if self.page.get_by_text(re.compile("Identifiants incorrects", re.I)).count():
                raise ConnexionImpossible(f"ALJT : identifiants incorrects (capture : {capture})")
            # sinon : réseau absent (réveil de veille), site lent… → simple nouvel essai au passage suivant
            raise RuntimeError(f"connexion ALJT sans réponse, nouvel essai plus tard (capture : {capture})")
        if not self._session_valide():
            raise ConnexionImpossible("connexion ALJT : session toujours invalide")
        self.log.info("connecté à l'ALJT")

    # ---------------------------------------------------------------- lecture
    @staticmethod
    def vers_offre(d: dict, url_base: str) -> Offre:
        piece = re.search(r"\d", str(d.get("accommodationTypeLabel") or ""))
        cree = d.get("dateCreated")
        return Offre(
            plateforme="aljt", id=str(d["id"]),
            url=f"{url_base}/account",          # application mono-page : pas de lien profond
            titre=str(d.get("specialId") or ""),
            ville=str(d.get("city") or ""), code_postal=str(d.get("zipcode") or "")[:5],
            loyer=_num(d.get("rentalPrice")), loyer_cc=True,      # « Loyer (charges comprises) »
            surface=_num(d.get("surface")), pieces=int(piece.group()) if piece else None,
            # dateCreated n'est pas une date de mise en ligne garantie : non utilisée comme date de publication
            date_publication=None,
            date_limite=d.get("dateValidity"),
            nb_candidats=int(_num(d.get("numberCandidatesOnOffer"))) if _num(d.get("numberCandidatesOnOffer")) is not None else None,
            financement=d.get("incomeCeilingLabel"),
            description=str(d.get("description") or ""),
            brut={**d, "_cree": cree, "_partenaire": d.get("partnerLabel")},
        )

    def _toutes_offres_brutes(self) -> list[dict]:
        brutes, page, limite = [], 1, 100
        while True:
            j = self._api("GET", "/account/offer/listing/json",
                          {"page": page, "start": (page - 1) * limite, "limit": limite, "sort": TRI_OFFRES})
            lot = j.get("offer")
            if lot is None:   # secours : structure inattendue → première liste de dicts
                lot = next(iter(listes_candidates(j)), [])
            brutes.extend(lot)
            total = int(_num(j.get("totalCount")) or 0)
            if not lot or len(brutes) >= total or page >= 50:
                break
            page += 1
        return brutes

    def lister_offres(self) -> list[Offre]:
        brutes = self._toutes_offres_brutes()
        if not brutes:
            self._verifier_session()      # liste vide = peut-être déconnecté
        self.debug_json("offres", brutes)
        offres = []
        for d in brutes:
            try:
                offres.append(self.vers_offre(d, self.url_base))
            except Exception:
                o = offre_depuis_dict("aljt", d)
                if o:
                    offres.append(o)
        return offres

    def nombre_candidats(self, offre: Offre) -> Optional[int]:
        for d in self._toutes_offres_brutes():
            if str(d.get("id")) == offre.id:
                n = _num(d.get("numberCandidatesOnOffer"))
                return int(n) if n is not None else None
        return offre.nb_candidats

    def candidatures(self) -> list[dict]:
        self._verifier_session()          # sinon « aucune candidature » à tort
        j = self._api("GET", "/account/application/listing/json",
                      {"page": 1, "start": 0, "limit": 50, "sort": TRI_CANDIDATURES})
        return j.get("candidate") or []

    @staticmethod
    def statut_termine(libelle: str) -> bool:
        n = normaliser(libelle)
        return any(m in n for m in STATUTS_TERMINES)

    def resume_candidatures(self) -> str:
        toutes = self.candidatures()
        actives = [c for c in toutes if not self.statut_termine(c.get("ast_label") or "")]
        if not actives:
            return f"aucune en cours ({len(toutes)} terminées)"
        return " ; ".join(f"{c.get('off_city')} {c.get('off_special_id')} [{c.get('ast_label')}]" for c in actives)

    def candidature_active(self) -> bool:
        actives = [c for c in self.candidatures() if not self.statut_termine(c.get("ast_label") or "")]
        if actives:
            self.log.info("candidature(s) ALJT active(s) : %s",
                          ", ".join(f"{c.get('off_special_id')} [{c.get('ast_label')}]" for c in actives))
        return bool(actives)

    # ---------------------------------------------------------------- candidature
    def _ouvrir_fiche_ui(self, offre: Offre) -> bool:
        """Ouvre la fenêtre « Voir l'offre » dans l'interface (pour la capture). Ne clique pas « Postuler »."""
        try:
            self.page.get_by_text("Les offres", exact=True).first.click()
            self.page.wait_for_timeout(1200)
            ok = self.page.evaluate("""(ref) => {
                const r = [...document.querySelectorAll('tr.x-grid-row')].find(e => e.innerText.includes(ref));
                if (!r) return false;
                r.querySelector('img.x-action-col-icon').click();
                return true; }""", offre.titre)
            if ok:
                self.page.locator("#offerAdd").wait_for(timeout=10_000)
            return bool(ok)
        except Exception as e:
            self.log.debug("fiche UI : %s", e)
            return False

    def candidater(self, offre: Offre, motif: Optional[str], dry_run: bool) -> ResultatCandidature:
        if dry_run:
            vu = self._ouvrir_fiche_ui(offre)
            capture = self.capture(f"dryrun-avant-postuler-{offre.id}")
            return ResultatCandidature(True, "DRY-RUN : fiche ouverte, bouton « Postuler à cette offre » "
                                             f"{'visible' if vu else 'non trouvé'} — NON cliqué.", capture)
        # Production : appel direct de l'API utilisée par le bouton (le plus rapide possible)
        t0 = time.time()
        j = self._api("POST", "/account/application/register/json", data={"offId": offre.id})
        duree = time.time() - t0
        if j.get("success"):
            msg = re.sub(r"<[^>]+>", " ", str(j.get("message") or "Candidature enregistrée")).strip()
            capture = None
            try:
                self.page.reload(wait_until="domcontentloaded")
                self.page.get_by_text("Mes candidatures", exact=True).first.click()
                self.page.wait_for_timeout(1500)
                capture = self.capture(f"candidature-{offre.id}")
            except Exception:
                pass
            return ResultatCandidature(True, f"{msg} (réponse du site en {duree:.1f} s)", capture, envoye=True)
        erreur = (j.get("_status") or {}).get("message") or json.dumps(j, ensure_ascii=False)[:300]
        return ResultatCandidature(False, f"refus du site : {erreur}", self.capture(f"echec-{offre.id}"))
