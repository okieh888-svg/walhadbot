"""Faux sites locaux reproduisant la structure RÉELLE observée (voir exploration/*/OBSERVATIONS.md).

Utilisés par les tests de bout en bout. Identifiants de test uniquement.
  python -m faux_sites.serveur   → démarre les trois faux sites et affiche leurs URL
"""
from __future__ import annotations

import html
import json
import re
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

IDENTIFIANT_TEST = "test@exemple.test"
MDP_TEST = "mdp-de-test-123"
JETON_TEST = "jeton-de-test"


class FauxSite:
    """Serveur HTTP local ; `etat` est modifiable par les tests."""

    def __init__(self):
        self.etat = self.etat_initial()
        self.requetes: list[tuple[str, str, str]] = []
        site = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _corps(self) -> str:
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n).decode("utf-8") if n else ""

            def _cookie(self, nom):
                c = SimpleCookie(self.headers.get("Cookie") or "")
                return c[nom].value if nom in c else None

            def envoyer(self, code=200, corps="", ctype="text/html; charset=utf-8", entetes=None):
                b = corps.encode("utf-8") if isinstance(corps, str) else corps
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(b)))
                for k, v in (entetes or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(b)

            def json(self, obj, code=200):
                self.envoyer(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

            def do_GET(self):
                site.requetes.append(("GET", self.path, ""))
                site.get(self)

            def do_POST(self):
                corps = self._corps()
                site.requetes.append(("POST", self.path, corps))
                site.post(self, corps)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self._fil = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def demarrer(self):
        self._fil.start()
        return self

    def arreter(self):
        self.httpd.shutdown()

    def posts(self, motif: str) -> list[tuple[str, str, str]]:
        return [r for r in self.requetes if r[0] == "POST" and motif in r[1]]


# =============================================================================== ALJT
class FauxALJT(FauxSite):
    def etat_initial(self):
        return {"offres": [], "candidatures": []}

    @staticmethod
    def offre(i, ville="PANTIN", cp="93500", loyer=750, surface=45, typ="T2", nb=0):
        return {"id": i, "specialId": f"REF-{i}", "accommodationTypeLabel": typ, "surface": str(surface),
                "rentalPrice": str(loyer), "zipcode": cp, "city": ville, "dateValidity": "2026-12-31",
                "numberCandidatesOnOffer": str(nb), "incomeCeilingLabel": "PLS", "description": "Test",
                "dateCreated": "2026-10-22 10:00:00", "partnerLabel": "Bailleur test"}

    def get(self, h):
        u = urlparse(h.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        connecte = h._cookie("PHPSESSID") == "ok"
        if u.path == "/account/index/information":
            if not connecte:
                return h.json({"_status": {"code": 1, "message": "Vous n'être pas connecté"}, "success": False})
            return h.json({"cndData": {"login": IDENTIFIANT_TEST}, "success": True})
        # comme le vrai site : hors connexion, les listes répondent 200 avec 0 élément
        if u.path == "/account/offer/listing/json" and not connecte:
            return h.json({"totalCount": 0, "offer": []})
        if u.path == "/account/application/listing/json" and not connecte:
            return h.json({"totalCount": 0, "candidate": []})
        if u.path == "/account/offer/listing/json":
            start, lim = int(q.get("start", 0)), int(q.get("limit", 50))
            offres = sorted(self.etat["offres"], key=lambda o: -o["id"])
            return h.json({"totalCount": str(len(offres)), "offer": offres[start:start + lim]})
        if u.path == "/account/application/listing/json":
            return h.json({"totalCount": str(len(self.etat["candidatures"])), "candidate": self.etat["candidatures"]})
        if u.path == "/account":
            if not connecte:
                return h.envoyer(200, PAGE_ALJT_CONNEXION)
            lignes = "".join(
                f'<tr class="x-grid-row"><td><div class="x-grid-cell-inner">{o["partnerLabel"]}</div></td>'
                f'<td><div class="x-grid-cell-inner">{o["specialId"]}</div></td><td>{o["city"]}</td><td>{o["accommodationTypeLabel"]}</td>'
                f'<td>{o["rentalPrice"]} €</td><td class="x-action-col-cell"><img class="x-action-col-icon" data-id="{o["id"]}" '
                f'data-qtip="Voir l\'offre" src="data:," onclick="voir({o["id"]})"></td><td>{o["numberCandidatesOnOffer"]}</td></tr>'
                for o in self.etat["offres"])
            return h.envoyer(200, PAGE_ALJT_APP.replace("%LIGNES%", lignes))
        h.envoyer(404, "introuvable")

    def post(self, h, corps):
        u = urlparse(h.path)
        f = {k: v[0] for k, v in parse_qs(corps).items()}
        if u.path == "/account/auth/login":
            if f.get("username") == IDENTIFIANT_TEST and f.get("password") == MDP_TEST:
                return h.envoyer(200, '{"success":true}', "application/json", {"Set-Cookie": "PHPSESSID=ok; Path=/"})
            return h.json({"success": False, "_status": {"code": "2", "message": "Identifiants invalides"}})
        if h._cookie("PHPSESSID") != "ok":
            return h.json({"_status": {"code": "1", "message": "Session expirée"}})
        if u.path == "/account/application/register/json":
            oid = int(f["offId"])
            o = next(x for x in self.etat["offres"] if x["id"] == oid)
            self.etat["candidatures"].append({"app_id": "1", "ast_label": "En attente", "off_id": str(oid),
                                              "off_special_id": o["specialId"]})
            return h.json({"success": True, "message": "Votre candidature a bien été enregistrée"})
        h.envoyer(404, "introuvable")


PAGE_ALJT_CONNEXION = """<html><head><title>Gestion des dossiers - identification</title></head><body>
<h2>Identification</h2>
<input type="text" name="username" id="username-inputEl"><input type="password" name="password" id="password-inputEl">
<button id="button-1012-btnEl" onclick="go()">S'identifier</button>
<script>async function go(){const b=new URLSearchParams({username:document.getElementById('username-inputEl').value,
password:document.getElementById('password-inputEl').value});await fetch('/account/auth/login',{method:'POST',body:b});location.reload();}</script>
</body></html>"""

PAGE_ALJT_APP = """<html><head><title>Offres de logement</title></head><body>
<a href="/account/auth/logout">Se déconnecter</a>
<ul><li class="tab">Mon dossier</li><li class="tab" onclick="document.getElementById('offres').hidden=false">Les offres</li>
<li class="tab">Mes candidatures</li></ul>
<div id="offres"><table>%LIGNES%</table></div>
<div class="x-window" id="win" hidden><span id="titre"></span>
<a class="x-btn" id="offerAdd"><span id="offerAdd-btnEl" onclick="postuler()">Postuler à cette offre</span></a></div>
<script>let cur=null;function voir(id){cur=id;document.getElementById('win').hidden=false;document.getElementById('titre').innerText='Offre '+id;}
async function postuler(){await fetch('/account/application/register/json',{method:'POST',body:new URLSearchParams({offId:cur})});}</script>
</body></html>"""


# =============================================================================== LOC'ANNONCES
class FauxParis(FauxSite):
    MAXLEN = 500

    def etat_initial(self):
        return {"offres": [], "candidature": None, "motifs": [], "nb_apres_lecture": {}}

    @staticmethod
    def offre(i, loc="PARIS 20<sup>e</sup>", loyer=820, charges=0, surface=45, pieces=2, nb=0, plafond="C (PLS)"):
        return {"id": i, "loc": loc, "loyer": loyer, "charges": charges, "surface": surface, "pieces": pieces,
                "nb": nb, "plafond": plafond, "lectures": 0}

    def get(self, h):
        u = urlparse(h.path)
        connecte = h._cookie("LUTECE") == "ok"
        if u.path == "/auth/login":
            return h.envoyer(200, PAGE_KEYCLOAK)
        if u.path == "/locannonces/servlet/plugins/oauth2/callback":
            return h.envoyer(302, "", entetes={"Location": "/auth/login"})
        if u.path in ("/locannonces/", "/locannonces"):
            if not connecte:
                return h.envoyer(200, PAGE_PARIS_ACCUEIL_DECO)
            cartes = "".join(
                f'<li class="col-md-4"><a href="logement/{o["id"]}"><div><p class="dateLimite" '
                f'title="Date limite de candidature jusqu\'au 31/12/2026">Date limite <span>31 déc.</span></p></div>'
                f'<p class="lgtInfo"><span class="loyer">{o["loyer"] + o["charges"]} € <span><abbr>CCC</abbr></span></span>'
                f'<span class="loc">{o["loc"]}</span><span class="surface">{o["surface"]}&nbsp;m²</span>'
                f'<span class="pieces">{o["pieces"]} pièces</span></p></a></li>' for o in self.etat["offres"])
            return h.envoyer(200, PAGE_PARIS_ACCUEIL.replace("%CARTES%", cartes))
        m = re.match(r"^/locannonces/logement/(\d+)$", u.path)
        if m:
            o = next((x for x in self.etat["offres"] if x["id"] == int(m.group(1))), None)
            if not o:
                return h.envoyer(404, "introuvable")
            if not connecte:
                return h.envoyer(200, "<html><body><p>Postulez sur ce logement en vous connectant :</p></body></html>")
            o["lectures"] += 1
            nb = o["nb"] + (self.etat["nb_apres_lecture"].get(o["id"], 0) if o["lectures"] > 1 else 0)
            return h.envoyer(200, PAGE_PARIS_DETAIL.format(id=o["id"], loc=o["loc"], loyer=o["loyer"], charges=o["charges"],
                                                           surface=o["surface"], pieces=o["pieces"], nb=nb, plafond=o["plafond"]))
        if u.path == "/locannonces/candidature.html":
            if not connecte:
                return h.envoyer(200, "<html><body>Postulez en vous connectant</body></html>")
            c = self.etat["candidature"]
            bloc = (f"<p>Logement {c} — candidature transmise</p><button>Annuler la candidature</button>" if c
                    else "<p>Vous n'avez pas de candidature en cours. Si vous en aviez une, celle-ci a été traitée.</p>")
            return h.envoyer(200, f"<html><body><h1>Votre Candidature</h1><section class='synthese'>Ma synthèse</section>"
                                  f"<h2>CANDIDATURE EN COURS</h2>{bloc}</body></html>")
        h.envoyer(404, "introuvable")

    def post(self, h, corps):
        u = urlparse(h.path)
        f = {k: v[0] for k, v in parse_qs(corps).items()}
        if u.path == "/auth/login":
            if f.get("username") == IDENTIFIANT_TEST and f.get("password") == MDP_TEST:
                return h.envoyer(302, "", entetes={"Location": "/locannonces/", "Set-Cookie": "LUTECE=ok; Path=/"})
            return h.envoyer(200, PAGE_KEYCLOAK.replace("<!--E-->", "<p>Identifiants invalides</p>"))
        if h._cookie("LUTECE") != "ok":
            return h.envoyer(302, "", entetes={"Location": "/auth/login"})
        m = re.match(r"^/locannonces/logement/(\d+)/candidature\.html$", u.path)
        if m:
            return h.envoyer(200, PAGE_PARIS_MOTIF.format(id=m.group(1), maxlen=self.MAXLEN))
        m = re.match(r"^/locannonces/logement/(\d+)/valider$", u.path)
        if m:
            self.etat["candidature"] = int(m.group(1))
            self.etat["motifs"].append(f.get("motif", ""))
            return h.envoyer(200, "<html><body><p>Votre candidature a bien été enregistrée.</p>"
                                  "<button>Annuler la candidature</button></body></html>")
        h.envoyer(404, "introuvable")


PAGE_KEYCLOAK = """<html><head><title>Mon Paris - Authentification</title></head><body>
<!--E--><form method="POST" action="/auth/login"><input type="email" name="username" id="username">
<input type="password" name="password" id="password"><button type="submit" name="Submit">Continuer</button></form></body></html>"""

PAGE_PARIS_ACCUEIL_DECO = """<html><body><a class="banner-mon-compte_menu__connexion" href="/auth/login" style="display:none">Connexion</a>
<div id="logts"><section class="row synthese notAvailable">Connectez-vous</section>
<ul class="row"><li><a href="logement/999"><span class="loyer">700 € CCC</span><span class="loc">PARIS 11e</span>
<span class="surface">50 m²</span><span class="pieces">2 pièces</span></a></li></ul></div></body></html>"""

PAGE_PARIS_ACCUEIL = """<html><head><base href="/locannonces/"></head><body><a href="#">Déconnexion</a>
<div id="logts"><section class="row synthese"><h2>Ma synthèse</h2><strong>C (PLS)</strong></section>
<ul class="row">%CARTES%</ul></div></body></html>"""

PAGE_PARIS_DETAIL = """<html><head><base href="/locannonces/"></head><body><h1>APPARTEMENT {pieces} PIÈCES, {surface} M²</h1>
<dl><div><dt>Pièces</dt><dd>{pieces}</dd></div><div><dt>Surface</dt><dd>{surface}&nbsp;m²</dd></div>
<div><dt>Loyer</dt><dd>{loyer} €</dd></div><div><dt>Charges</dt><dd>{charges} €</dd></div><div><dt>Étage</dt><dd>3</dd></div></dl>
<div class="obs"><h4>Lumineux</h4></div><p class="loc">Métro ligne 3</p>
<section class="dateLimAndNbCand"><p class="dateLimCand" title="Date limite de candidature jusqu'au 31/12/2026">Postulez jusqu'au 31 décembre</p>
<p class="nbCand"><strong>{nb}</strong> personnes ont déjà postulé</p></section>
<p>Ce logement vous est présenté car il correspond à votre profil.</p>
<section class="adeq"><ul><li>Dont les ressources sont inférieures au <a>plafond {plafond}</a></li></ul></section>
<form method="POST" action="logement/{id}/candidature.html"><button type="submit" id="linkPostuler">Candidater</button></form>
</body></html>"""

PAGE_PARIS_MOTIF = """<html><head><base href="/locannonces/"></head><body><h1>Votre candidature</h1>
<form method="POST" action="logement/{id}/valider"><label>Motif</label><textarea name="motif" maxlength="{maxlen}"></textarea>
<input type="checkbox" name="certif" required> Je certifie l'exactitude des informations
<button type="submit">Valider ma candidature</button></form></body></html>"""


# =============================================================================== ACTION LOGEMENT
class FauxAL(FauxSite):
    def etat_initial(self):
        return {"offres": [], "compteur": 2, "candidatures": []}

    @staticmethod
    def offre(i, ville="Pantin", cp="93500", loyer=780.0, surface=47.0, typo="T2", pub="2026-10-22"):
        return {"id": i, "guid": f"guid-{i}", "lodging": {"area": surface, "lodgingTypology": {"code": typo},
                "lodgingCategory": {"label": "Appartement"}, "municipality": {"name": ville, "postcode": cp}},
                "residency": {"name": "Résidence test"}, "totalRentAmountBaseBound": loyer,
                "startDateOfPublication": pub, "paymentType": {"code": "MSL"}, "offerCategory": "BLUE",
                "candidacy": None, "description": "Bel appartement"}

    def get(self, h):
        u = urlparse(h.path)
        connecte = h._cookie("auth0") == "ok"
        if u.path.startswith("/api/v1/"):
            if h.headers.get("Authorization") != f"Bearer {JETON_TEST}":
                return h.json({"message": "Unauthorized"}, 401)
            if u.path == "/api/v1/demands/ongoing-candidacies-counters":
                return h.json({"code": "DEM100", "data": [
                    {"offerCategory": "BLUE", "limit": 5, "counter": self.etat["compteur"]},
                    {"offerCategory": "RED", "limit": 1, "counter": 0}]})
            if u.path.startswith("/api/v1/demands/is-allowed-to-candidate-to-offer/"):
                return h.json({"code": "DEM", "data": {"isAllowed": True}})
            if u.path.startswith("/api/v1/demands/offer-candidacy-in-progress/"):
                guid = u.path.rsplit("/", 1)[1]
                return h.json({"code": "DEM53", "data": {"guid": guid} if guid in self.etat["candidatures"] else None})
            return h.envoyer(404, "introuvable")
        if u.path == "/search" or u.path.startswith("/search/detail/"):
            corps = PAGE_AL_CONNECTE if connecte else PAGE_AL_DECO
            if u.path.startswith("/search/detail/"):
                guid = u.path.rsplit("/", 1)[1]
                o = next((x for x in self.etat["offres"] if x["guid"] == guid), None)
                corps = corps.replace("%CONTENU%", f"<h1>{o['lodging']['lodgingTypology']['code']} — "
                                                   f"{o['lodging']['municipality']['name']}</h1><p>{o['totalRentAmountBaseBound']} €/mois-CC</p>"
                                                   f"<p>N° du logement : {o['id']}</p><button>Je candidate</button>" if o else "introuvable")
            return h.envoyer(200, corps.replace("%CONTENU%", "<h1>Trouvez votre logement</h1>"))
        if u.path == "/u/login":
            return h.envoyer(200, PAGE_AL_EMAIL)
        if u.path == "/u/login/password":
            return h.envoyer(200, PAGE_AL_MDP)
        h.envoyer(404, "introuvable")

    def post(self, h, corps):
        u = urlparse(h.path)
        if u.path == "/u/login/password":
            f = {k: v[0] for k, v in parse_qs(corps).items()}
            if f.get("password") == MDP_TEST:
                return h.envoyer(302, "", entetes={"Location": "/search", "Set-Cookie": "auth0=ok; Path=/"})
            return h.envoyer(200, PAGE_AL_MDP)
        if u.path.startswith("/api/v1/"):
            if h.headers.get("Authorization") != f"Bearer {JETON_TEST}":
                return h.json({"message": "Unauthorized"}, 401)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/api/v1/demands/offers-overview":
                taille, page = int(q.get("size", 20)), int(q.get("page", 0))
                d = self.etat["offres"]
                return h.json({"code": "DEM31", "totalPages": max(1, -(-len(d) // taille)), "totalSize": len(d),
                               "data": d[page * taille:(page + 1) * taille]})
            if u.path == "/api/v1/demands/candidate":
                guid = json.loads(corps)["offerGuid"]
                self.etat["candidatures"].append(guid)
                self.etat["compteur"] += 1
                return h.json({"code": "DEM", "data": {"offerDetails": {"guid": guid}}})
            if u.path == "/api/v1/demands/offer-details":
                guid = json.loads(corps)["offerGuid"]
                return h.json({"code": "DEM41", "data": {"guid": guid, "lodging": {"floor": {"label": "2"}},
                                                         "endDateOfPublication": "2026-11-18T22:59:00Z"}})
        h.envoyer(404, "introuvable")


_JS_JETON = """<script>localStorage.setItem('@@auth0spajs@@::CLIENT::https://api::openid',
JSON.stringify({body:{access_token:'%s'},expiresAt:Math.floor(Date.now()/1000)+3600}));</script>""" % JETON_TEST

PAGE_AL_DECO = """<html><head><title>Plateforme du logement</title></head><body>
<button onclick="this.parentNode.removeChild(this)">Refuser tous les cookies</button>
<a href="/u/login">Se connecter</a>%CONTENU%</body></html>"""
PAGE_AL_CONNECTE = """<html><head><title>Recherchez un logement</title></head><body>
<a>Mon espace demandeur</a>%CONTENU%""" + _JS_JETON + "</body></html>"
PAGE_AL_EMAIL = """<html><head><title>Connexion | Action Logement</title></head><body>
<form onsubmit="event.preventDefault();location.href='/u/login/password'"><input type="email" id="username">
<button type="submit">Continuer</button></form></body></html>"""
PAGE_AL_MDP = """<html><head><title>Connexion | Action Logement</title></head><body>
<form method="POST" action="/u/login/password"><input type="password" name="password" id="password">
<button type="submit">Continuer</button></form></body></html>"""


if __name__ == "__main__":
    import time
    sites = {"aljt": FauxALJT().demarrer(), "paris": FauxParis().demarrer(), "al": FauxAL().demarrer()}
    sites["aljt"].etat["offres"].append(FauxALJT.offre(1))
    sites["paris"].etat["offres"].append(FauxParis.offre(10))
    sites["al"].etat["offres"].append(FauxAL.offre(100))
    for k, s in sites.items():
        print(k, s.url)
    while True:
        time.sleep(3600)
