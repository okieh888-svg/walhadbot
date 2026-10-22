"""Action Logement — https://logement-actionlogement.fr

Candidature automatique tant qu'il reste plus d'une place libre dans la catégorie de l'offre
(les candidatures ne s'annulent pas) ; sinon alerte maximale. Parcours du site (code du front) :
GET /demands/is-allowed-to-candidate-to-offer/<guid> → confirmation → POST /demands/candidate {offerGuid}.

SPA Angular + API REST (jeton Auth0 conservé par le front dans localStorage).
Voir exploration/action_logement/OBSERVATIONS.md.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

from coeur.offre import Offre

from .base import Adaptateur, ConnexionImpossible, ResultatCandidature, SessionExpiree, _num

# Paris (arrondissements) + 8 communes limitrophes du 93 : (code INSEE, code postal)
COMMUNES = [(f"751{i:02d}", "75116" if i == 16 else f"750{i:02d}") for i in range(1, 21)] + [
    ("75116", "75016"),
    ("93070", "93400"),                                          # Saint-Ouen-sur-Seine
    ("93066", "93200"), ("93066", "93210"), ("93066", "93380"),  # Saint-Denis (dont ex-Pierrefitte)
    ("93001", "93300"),                                          # Aubervilliers
    ("93055", "93500"),                                          # Pantin
    ("93061", "93310"),                                          # Le Pré-Saint-Gervais
    ("93045", "93260"),                                          # Les Lilas
    ("93006", "93170"),                                          # Bagnolet
    ("93048", "93100"),                                          # Montreuil
]
COMMUNES = list(dict.fromkeys(COMMUNES))

JS_JETON = """() => {
  for (const k of Object.keys(localStorage)) {
    if (k.startsWith('@@auth0spajs@@') && !k.endsWith('@@user@@')) {
      try { const v = JSON.parse(localStorage.getItem(k));
            const b = v.body || {}; if (b.access_token) return [b.access_token, v.expiresAt || 0]; } catch (e) {}
    }
  }
  return null;
}"""

JS_REFRESH = """async (authUrl) => {
  const k = Object.keys(localStorage).find(k => k.startsWith('@@auth0spajs@@') && !k.endsWith('@@user@@'));
  if (!k) return 'pas de cache';
  const v = JSON.parse(localStorage.getItem(k)); const b = v.body || {};
  if (!b.refresh_token) return 'pas de refresh_token';
  const r = await fetch(authUrl + '/oauth/token', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({grant_type: 'refresh_token', client_id: b.client_id, refresh_token: b.refresh_token})});
  if (r.status !== 200) return r.status;
  const n = await r.json();
  v.body = Object.assign({}, b, n);                  // rotation : le nouveau refresh_token remplace l'ancien
  v.expiresAt = Math.floor(Date.now() / 1000) + (n.expires_in || 3600);
  localStorage.setItem(k, JSON.stringify(v));
  return 200;
}"""

JS_API = """async ([api, jeton, chemin, corps]) => {
  const o = {method: corps === null ? 'GET' : 'POST',
             headers: {'Authorization': 'Bearer ' + jeton, 'Accept': 'application/json', 'Content-Type': 'application/json'}};
  if (corps !== null) o.body = JSON.stringify(corps);
  const r = await fetch(api + chemin, o);
  return [r.status, await r.text()];
}"""


class AdaptateurActionLogement(Adaptateur):
    nom = "action_logement"
    url_base = "https://logement-actionlogement.fr"

    def __init__(self, *a, api: Optional[str] = None, **k):
        super().__init__(*a, **k)
        self.api = (api or os.environ.get("AL_API", "https://api.logement-actionlogement.fr/api/v1")).rstrip("/")
        self.auth_url = os.environ.get("AL_AUTH", "https://authentification.actionlogement.fr").rstrip("/")
        self._jeton: Optional[str] = None
        self._expire = 0.0

    # ---------------------------------------------------------------- connexion
    def _refuser_cookies(self) -> None:
        try:
            b = self.page.get_by_role("button", name="Refuser tous les cookies")
            if b.count() and b.first.is_visible():
                b.first.click()
        except Exception:
            pass

    def _verifier_cloudflare(self) -> None:
        t = (self.page.title() or "").lower()
        if "just a moment" in t or "un instant" in t or "attention required" in t:
            self.page.wait_for_timeout(5000)          # le défi se valide seul dans un vrai Chrome
            t = (self.page.title() or "").lower()
        if "just a moment" in t or "un instant" in t or "attention required" in t:
            raise ConnexionImpossible("vérification Cloudflare affichée : pas de contournement, "
                                      "lance « python main.py --connexion action_logement » et passe-la à la main")

    def _lire_jeton(self) -> bool:
        r = self.page.evaluate(JS_JETON)
        if r and r[0]:
            self._jeton, self._expire = r[0], float(r[1] or 0)
            return self._expire == 0 or self._expire - time.time() > 60
        return False

    def _rafraichir_jeton(self) -> None:
        """Jeton valide dans le cache Auth0 du site, sinon renouvellement par son refresh_token
        (même appel /oauth/token que le site ; aucun mot de passe)."""
        try:
            self.page.goto(self.url_base + "/search", wait_until="domcontentloaded")
        except Exception as e:     # réseau pas encore revenu (réveil de veille) : simple nouvel essai plus tard
            raise RuntimeError(f"site Action Logement injoignable ({type(e).__name__})")
        if not self.page.url.startswith(self.url_base):
            raise RuntimeError(f"site Action Logement non chargé ({self.page.url[:60]})")
        self._verifier_cloudflare()
        for _ in range(6):
            try:
                if self._lire_jeton():
                    return
            except Exception:      # navigation interne du front pendant la lecture
                pass
            self.page.wait_for_timeout(500)
        try:
            statut = self.page.evaluate(JS_REFRESH, self.auth_url)
        except Exception as e:     # échec réseau du renouvellement ≠ session expirée
            raise RuntimeError(f"renouvellement du jeton impossible ({type(e).__name__}), nouvel essai plus tard")
        self.log.info("renouvellement du jeton Action Logement : %s", statut)
        if statut == 200 and self._lire_jeton():
            return
        if statut in (400, 401, 403) or statut in ("pas de cache", "pas de refresh_token"):
            raise SessionExpiree(f"aucun jeton Auth0 valide (renouvellement : {statut})")
        raise RuntimeError(f"renouvellement du jeton : réponse {statut}, nouvel essai plus tard")

    def connexion(self) -> None:
        try:
            self._rafraichir_jeton()
            self.log.info("session Action Logement valide")
            return
        except SessionExpiree:
            pass
        identifiant, mdp = self.identifiants_ou_erreur("AL_EMAIL / AL_PASSWORD")
        self.log.info("connexion Action Logement…")
        self._refuser_cookies()
        self.page.get_by_text("Se connecter", exact=True).first.click()
        champ = self.page.locator("input[type=email], #username").first
        champ.wait_for(timeout=30_000)
        if self.page.locator("iframe[src*='captcha'], [class*=captcha]").count():
            raise ConnexionImpossible("captcha sur la connexion Action Logement : connexion manuelle nécessaire")
        champ.fill(identifiant)
        self.page.get_by_role("button", name=re.compile("Continuer")).first.click()
        mot = self.page.locator("input[type=password]").first
        mot.wait_for(timeout=30_000)
        mot.fill(mdp)
        self.page.get_by_role("button", name=re.compile("Continuer|Se connecter")).first.click()
        try:
            self.page.wait_for_url(lambda u: u.startswith(self.url_base + "/search")
                                   or u.startswith(self.url_base + "/demand"), timeout=30_000)
        except Exception:
            pass
        if "authentification.actionlogement.fr" in self.page.url:
            capture = self.capture("connexion-echec")
            raise ConnexionImpossible(f"connexion Action Logement échouée (code de vérification ?) — {capture}")
        self._rafraichir_jeton()
        self.log.info("connecté à Action Logement")

    # ---------------------------------------------------------------- API
    def _appel(self, chemin: str, corps=None) -> dict:
        if not self._jeton or (self._expire and self._expire - time.time() < 60):
            self._rafraichir_jeton()
        if not self.page.url.startswith(self.url_base):
            self.page.goto(self.url_base + "/search", wait_until="domcontentloaded")
        statut, texte = self.page.evaluate(JS_API, [self.api, self._jeton, chemin, corps])
        if statut == 401:
            self._jeton = None
            raise SessionExpiree(f"{chemin} → 401")
        if statut == 403:
            raise ConnexionImpossible(f"{chemin} → 403 (Cloudflare ou droits)")
        if statut >= 400:
            raise RuntimeError(f"API AL {chemin} → {statut} : {texte[:200]}")
        return json.loads(texte)

    @staticmethod
    def corps_recherche() -> dict:
        return {"municipalities": [{"code": c, "postcode": p} for c, p in COMMUNES],
                "searchRadiusInKm": 0, "productGuids": [], "typologyCodes": []}

    def vers_offre(self, d: dict) -> Offre:
        lg = d.get("lodging") or {}
        mun = lg.get("municipality") or (lg.get("address") or {}).get("municipality") or {}
        typo = str((lg.get("lodgingTypology") or {}).get("code") or "")
        m = re.search(r"\d", typo)
        return Offre(
            plateforme=self.nom, id=str(d.get("id")),
            url=f"{self.url_base}/search/detail/{d.get('guid')}",
            titre=f"{(lg.get('lodgingCategory') or {}).get('label', '')} {typo} — {(d.get('residency') or {}).get('name', '')}".strip(),
            ville=str(mun.get("name") or ""), code_postal=str(mun.get("postcode") or ""),
            loyer=_num(d.get("totalRentAmountBaseBound")), loyer_cc=True,
            surface=_num(lg.get("area")), pieces=int(m.group()) if m else None,
            date_publication=d.get("startDateOfPublication"),
            description=str(d.get("description") or "")[:1500],
            brut={"guid": d.get("guid"), "offerCategory": d.get("offerCategory"),
                  "photo": ((d.get("pictures") or [{}])[0] or {}).get("url"),
                  "candidacy": d.get("candidacy"), "paymentType": (d.get("paymentType") or {}).get("code")},
        )

    def lister_offres(self) -> list[Offre]:
        corps, page, offres = self.corps_recherche(), 0, []
        while True:
            j = self._appel(f"/demands/offers-overview?size=50&page={page}", corps)
            lot = j.get("data") or []
            for d in lot:
                o = self.vers_offre(d)
                if o.brut.get("paymentType") not in (None, "MSL"):   # ex. tarif à la nuit
                    o.loyer = None
                offres.append(o)
            page += 1
            if page >= int(j.get("totalPages") or 1) or not lot or page > 40:
                break
        self.debug_json("offres", [o.to_dict() for o in offres])
        return offres

    def enrichir(self, offre: Offre) -> Offre:
        guid = offre.brut.get("guid")
        if not guid:
            return offre
        d = (self._appel("/demands/offer-details", {"offerGuid": guid}).get("data")) or {}
        floor = ((d.get("lodging") or {}).get("floor") or {}).get("label")
        if floor is not None:
            offre.etage = "RDC" if str(floor) == "0" else str(floor)
        fin = d.get("endDateOfPublication")
        if fin:
            offre.date_limite = fin[:16].replace("T", " ") + " UTC"
        if d.get("candidacy"):
            offre.eligible = False                     # déjà candidaté sur cette offre
        else:
            autorise = self._appel(f"/demands/is-allowed-to-candidate-to-offer/{guid}").get("data") or {}
            if autorise and autorise.get("isAllowed") is False:
                offre.eligible = False
                offre.brut["refus"] = autorise.get("disallowedReasonCode")
        offre.quota_restant = self.places_restantes(offre.brut.get("offerCategory") or "BLUE")
        return offre

    def places_restantes(self, categorie: str) -> Optional[int]:
        """Places libres dans la catégorie (BLUE = candidatures multiples, RED = unique)."""
        for c in self._appel("/demands/ongoing-candidacies-counters").get("data") or []:
            if c.get("offerCategory") == categorie:
                return int(c.get("limit") or 0) - int(c.get("counter") or 0)
        return None

    def capture_offre(self, offre: Offre):
        """Fiche Angular : attendre son rendu (« N° du logement ») avant la capture."""
        try:
            self.page.goto(offre.url, wait_until="domcontentloaded")
            self._verifier_cloudflare()
            try:
                self.page.get_by_text(re.compile(r"N° du logement")).first.wait_for(timeout=12_000)
                self.page.wait_for_timeout(800)
                return self.capture(f"offre-{offre.id}")
            except Exception:
                self.log.info("fiche %s non rendue par le site : capture d'une fiche reconstituée", offre.id)
            return self._capture_reconstituee(offre)
        except Exception as e:
            self.log.warning("capture de l'offre impossible : %s", e)
            return None

    def _capture_reconstituee(self, offre: Offre):
        """Fiche HTML locale construite avec les données de l'API (photo comprise)."""
        import html as h
        photo = offre.brut.get("photo") or ""
        p = self.ctx.new_page()
        try:
            p.set_content(f"""<html><body style="font-family:sans-serif;max-width:900px;margin:24px">
              <h1 style="color:#e41e4c">🚨 {h.escape(offre.titre)}</h1>
              <h2>{h.escape(offre.ville)} ({h.escape(offre.code_postal)})</h2>
              <p style="font-size:22px"><b>{offre.loyer or '?'} € / mois CC</b> · {offre.surface or '?'} m² · T{offre.pieces or '?'}
                 · étage {h.escape(offre.etage or '?')}</p>
              <p>Publiée le {h.escape(offre.date_publication or '?')} · date limite {h.escape(offre.date_limite or '?')}</p>
              {f'<img src="{h.escape(photo)}" style="max-width:100%">' if photo else ''}
              <p style="white-space:pre-wrap">{h.escape(offre.description)}</p>
              <p style="color:#666">Lien : {h.escape(offre.url)}</p></body></html>""")
            p.wait_for_timeout(1500)
            return self.capture(f"offre-{offre.id}", page=p)
        finally:
            p.close()

    def nombre_candidats(self, offre: Offre) -> Optional[int]:
        return None     # non exposé par Action Logement

    def resume_candidatures(self) -> str:
        noms = {"BLUE": "multiples", "RED": "unique"}
        morceaux = [f"{noms.get(c.get('offerCategory'), c.get('offerCategory'))} : {c.get('counter')}/{c.get('limit')} utilisées"
                    for c in self._appel("/demands/ongoing-candidacies-counters").get("data") or []]
        return " · ".join(morceaux) or "inconnu"

    def candidature_active(self) -> bool:
        return False    # le plafond par catégorie est géré par quota_restant

    def candidater(self, offre: Offre, motif: Optional[str], dry_run: bool) -> ResultatCandidature:
        from coeur.politiques import AL_RESERVE
        guid = offre.brut.get("guid")
        restant = self.places_restantes(offre.brut.get("offerCategory") or "BLUE")   # relu juste avant
        if restant is None or restant <= AL_RESERVE:
            return ResultatCandidature(False, f"plus assez de places libres ({restant}) : candidature annulée",
                                       self.capture_offre(offre))
        autorise = self._appel(f"/demands/is-allowed-to-candidate-to-offer/{guid}").get("data") or {}
        if autorise.get("isAllowed") is False:
            return ResultatCandidature(False, f"le site refuse : {autorise.get('disallowedReasonCode')}",
                                       self.capture_offre(offre))
        if dry_run:
            return ResultatCandidature(True, f"DRY-RUN : candidature autorisée par le site, {restant} place(s) libre(s) "
                                             f"— POST /candidate NON envoyé.", self.capture_offre(offre))
        t0 = time.time()
        rep = self._appel("/demands/candidate", {"offerGuid": guid})
        duree = time.time() - t0
        verif = self._appel(f"/demands/offer-candidacy-in-progress/{guid}").get("data")
        capture = self.capture_offre(offre)
        if verif or rep.get("data") is not None:
            return ResultatCandidature(True, f"candidature enregistrée par Action Logement en {duree:.1f} s "
                                             f"({restant - 1} place(s) libre(s) restante(s)). Pense à vérifier tes "
                                             f"pièces justificatives.", capture, envoye=True)
        return ResultatCandidature(False, f"réponse inattendue : {json.dumps(rep, ensure_ascii=False)[:300]}",
                                   capture, envoye=True)
