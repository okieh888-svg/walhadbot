"""AL'in (Action Logement, logement social) — https://al-in.fr

SPA Angular + API JSON:API `https://api.al-in.fr/api` (jeton dans localStorage.id_token, 15 min,
pas de renouvellement → reconnexion avec le mot de passe chiffré). Une seule candidature à la fois.
Voir exploration/alin/OBSERVATIONS.md.
"""
from __future__ import annotations

import base64
import json
import re
import time
from typing import Optional

from coeur.offre import Offre

from .base import Adaptateur, ConnexionImpossible, ResultatCandidature, SessionExpiree, _num

TYPES_ELIGIBILITE = ("seeked", "bordering", "other_from_department")

# Comme le front (TokenService.getToken) : jeton d'API = paw_id_token (sessionStorage, sinon localStorage).
# localStorage.id_token est le jeton IAM de connexion : refusé (401) par l'API des offres.
JS_JETON = "() => sessionStorage.getItem('paw_id_token') || localStorage.getItem('paw_id_token')"

JS_API = """async ([api, chemin, corps]) => {
  const jeton = sessionStorage.getItem('paw_id_token') || localStorage.getItem('paw_id_token');
  if (!jeton) return [401, 'pas de jeton'];
  const o = {method: corps === null ? 'GET' : 'POST',
             headers: {'Authorization': 'Bearer ' + jeton, 'Accept': 'application/json', 'Content-Type': 'application/json'}};
  if (corps !== null) o.body = JSON.stringify(corps);
  const r = await fetch(api + chemin, o);
  return [r.status, await r.text()];
}"""


class AdaptateurAlin(Adaptateur):
    nom = "alin"
    url_base = "https://al-in.fr"

    def __init__(self, *a, api: Optional[str] = None, **k):
        super().__init__(*a, **k)
        self.api = (api or "https://api.al-in.fr/api").rstrip("/")
        self._demande: dict = {}

    # ---------------------------------------------------------------- connexion
    def _jeton_valide(self) -> bool:
        try:
            jeton = self.page.evaluate(JS_JETON)
        except Exception:
            return False
        if not jeton:
            return False
        try:
            charge = jeton.split(".")[1]
            exp = json.loads(base64.urlsafe_b64decode(charge + "=" * (-len(charge) % 4))).get("exp", 0)
            return exp - time.time() > 90
        except Exception:
            return True

    def _refuser_cookies(self) -> None:
        try:
            b = self.page.get_by_role("button", name="Refuser tous les cookies")
            if b.count() and b.first.is_visible():
                b.first.click()
        except Exception:
            pass

    def connexion(self) -> None:
        try:
            self.page.goto(self.url_base + "/#/HomeSalarie", wait_until="domcontentloaded")
        except Exception as e:
            raise RuntimeError(f"AL'in injoignable ({type(e).__name__})")
        self.page.wait_for_timeout(2500)
        if self._jeton_valide():
            self.log.info("session AL'in valide")
            return
        identifiant, mdp = self.identifiants_ou_erreur("identifiants AL'in")
        self.log.info("connexion AL'in…")
        self.page.goto(self.url_base + "/#/deco", wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)
        self._refuser_cookies()
        # Deux liens « Se connecter » : celui du menu mobile est caché ; on prend le visible.
        lien = self.page.locator("text=Se connecter >> visible=true")
        if lien.count():
            lien.first.click()
        else:
            self.page.goto(self.url_base + "/#/connexion-demandeur", wait_until="domcontentloaded")
        self._refuser_cookies()
        champ = self.page.locator("input[formcontrolname=mail], input[type=email], input[name*=login i], "
                                  "input[formcontrolname*=login i], input[id*=mail i]").first
        champ.wait_for(timeout=20_000)
        if self.page.locator("iframe[src*='captcha'], [class*=captcha]").count():
            raise ConnexionImpossible("captcha sur la connexion AL'in : connexion manuelle nécessaire")
        champ.fill(identifiant)
        mot = self.page.locator("input[type=password]").first
        mot.fill(mdp)
        mot.press("Enter")
        for _ in range(30):
            self.page.wait_for_timeout(1000)
            if self._jeton_valide():
                self.log.info("connecté à AL'in")
                return
        if self.page.get_by_text(re.compile(r"incorrect|invalide|erron", re.I)).count():
            raise ConnexionImpossible(f"AL'in : identifiants refusés (capture : {self.capture('connexion-echec')})")
        raise RuntimeError(f"connexion AL'in sans réponse, nouvel essai plus tard ({self.capture('connexion-echec')})")

    # ---------------------------------------------------------------- API
    def _appel(self, chemin: str, corps=None) -> dict:
        if not self._jeton_valide():
            raise SessionExpiree("jeton AL'in expiré (15 min)")
        statut, texte = self.page.evaluate(JS_API, [self.api, chemin, corps])
        if statut in (401, 403):
            raise SessionExpiree(f"{chemin} → {statut}")
        if statut >= 400:
            raise RuntimeError(f"API AL'in {chemin} → {statut} : {texte[:200]}")
        return json.loads(texte) if texte else {}

    def demande(self) -> dict:
        d = (self._appel("/dmo/housing_requests/last_request").get("data")) or {}
        if not d:
            raise RuntimeError("AL'in : aucune demande de logement sur ce compte")
        self._demande = d
        return d

    def _als_id(self, d: dict) -> str:
        a = d.get("attributes") or {}
        return str(a.get("als_id") or a.get("request_als_id") or d.get("id"))

    @staticmethod
    def vers_offre(d: dict) -> Offre:
        a = d.get("attributes") or {}
        m = re.search(r"\d", str(a.get("typology") or ""))
        photo = (a.get("main_picture") or {}).get("full_size_absolute")
        return Offre(
            plateforme="alin", id=str(d.get("id")), url=f"https://al-in.fr/#/fiche-logement/{d.get('id')}",
            titre=f"{a.get('typology') or ''} — {a.get('residence_title') or ''} ({a.get('external_ref') or ''})",
            ville=str(a.get("district") or ""), code_postal=str(a.get("postal_code") or ""),
            loyer=_num(a.get("rent_with_charges")), loyer_cc=True, surface=_num(a.get("surface")),
            pieces=int(m.group()) if m else None, etage=str(a["floor"]) if a.get("floor") is not None else None,
            date_publication=(a.get("date_publication_start") or "")[:10] or None,
            date_limite=(a.get("publication_end_date") or "")[:10] or None,
            description=str(a.get("details") or a.get("description") or "")[:1500],
            brut={"photo": photo, "residence": a.get("residence_title"), "ref": a.get("external_ref")})

    def lister_offres(self) -> list[Offre]:
        als = self._als_id(self.demande())
        offres: dict[str, Offre] = {}
        for type_ in TYPES_ELIGIBILITE:
            page = 1
            while page <= 20:
                j = self._appel(f"/dmo/housing_requests/{als}/eligible_offers?per_page=30&page={page}"
                                f"&sort%5Bpublication_end_date%5D=1&eligibility_type={type_}")
                for d in j.get("data") or []:
                    offres.setdefault(str(d.get("id")), self.vers_offre(d))
                pag = ((j.get("meta") or {}).get("pagination") or {})
                if page >= int(pag.get("total_pages") or 1):
                    break
                page += 1
        self.debug_json("offres", [o.to_dict() for o in offres.values()])
        return list(offres.values())

    def nombre_candidats(self, offre: Offre) -> Optional[int]:
        j = self._appel(f"/dmo/housing_offers/with_recount_eligible_requests_nb?housing_offer_ids%5B%5D={offre.id}")
        for d in j.get("data") or []:
            n = (d.get("attributes") or {}).get("applicated_nb")
            if n is not None:
                return int(n)
        return None

    def enrichir(self, offre: Offre) -> Offre:
        offre.nb_candidats = self.nombre_candidats(offre)
        return offre

    def candidature_active(self) -> bool:
        a = (self.demande().get("attributes") or {})
        return bool(a.get("active_application_id"))

    def resume_candidatures(self) -> str:
        a = (self.demande().get("attributes") or {})
        if a.get("need_to_be_renewed") or a.get("is_striked_off"):
            return "⚠️ demande à renouveler / radiée : candidature impossible"
        return "1 en cours" if a.get("active_application_id") else "aucune en cours"

    # ---------------------------------------------------------------- candidature
    def capture_offre(self, offre: Offre):
        import html as h
        p = self.ctx.new_page()
        try:
            photo = offre.brut.get("photo") or ""
            p.set_content(f"""<html><body style="font-family:sans-serif;max-width:900px;margin:24px">
              <h1 style="color:#d50000">AL'in — {h.escape(offre.titre)}</h1>
              <h2>{h.escape(offre.ville)} ({h.escape(offre.code_postal)})</h2>
              <p style="font-size:22px"><b>{offre.loyer or '?'} € / mois CC</b> · {offre.surface or '?'} m² ·
                 T{offre.pieces or '?'} · étage {h.escape(offre.etage or '?')} · {offre.nb_candidats if offre.nb_candidats is not None else '?'} candidat(s)</p>
              <p>Publiée le {h.escape(offre.date_publication or '?')} · date limite {h.escape(offre.date_limite or '?')}</p>
              {f'<img src="{h.escape(photo)}" style="max-width:100%">' if photo else ''}
              <p style="white-space:pre-wrap">{h.escape(offre.description)}</p>
              <p style="color:#666">{h.escape(offre.url)}</p></body></html>""")
            p.wait_for_timeout(1500)
            return self.capture(f"offre-{offre.id}", page=p)
        except Exception as e:
            self.log.warning("capture AL'in impossible : %s", e)
            return None
        finally:
            p.close()

    def candidater(self, offre: Offre, motif: Optional[str], dry_run: bool) -> ResultatCandidature:
        d = self.demande()                                    # relu juste avant l'envoi
        a = d.get("attributes") or {}
        if a.get("active_application_id"):
            return ResultatCandidature(False, "une candidature AL'in est déjà en cours", self.capture_offre(offre))
        if a.get("need_to_be_renewed") or a.get("is_striked_off"):
            return ResultatCandidature(False, "demande AL'in à renouveler ou radiée : candidature impossible",
                                       self.capture_offre(offre))
        corps = {"data": {"attributes": {"status": "EN ATTENTE", "housing_request_id": d.get("id"),
                                         "housing_offer_id": offre.id}}}
        if dry_run:
            return ResultatCandidature(True, "DRY-RUN : aucune candidature AL'in en cours, POST /housing_applications "
                                             "NON envoyé.", self.capture_offre(offre))
        t0 = time.time()
        rep = self._appel("/dmo/housing_applications", corps)
        duree = time.time() - t0
        capture = self.capture_offre(offre)
        if (rep.get("data") or {}).get("id"):
            return ResultatCandidature(True, f"candidature AL'in enregistrée en {duree:.1f} s "
                                             "(annulable sur al-in.fr tant qu'elle n'est pas transmise au bailleur)",
                                       capture, envoye=True)
        return ResultatCandidature(False, f"réponse inattendue : {json.dumps(rep, ensure_ascii=False)[:300]}",
                                   capture, envoye=True)
