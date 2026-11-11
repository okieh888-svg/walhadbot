"""Bout en bout : vrais adaptateurs + vrai Chrome (headless) contre les faux sites locaux.
Telegram simulé. Lancer :  pytest -m e2e
"""
import pytest
import requests

from coeur.alertes import GestionAlertes
from coeur.memoire import Memoire
from coeur.moteur import Moteur
from coeur.motif import GenerateurMotif
from coeur.telegram import NotifieurFactice
from faux_sites.serveur import IDENTIFIANT_TEST, MDP_TEST, FauxAL, FauxALJT, FauxParis
from plateformes import AdaptateurActionLogement, AdaptateurALJT, AdaptateurLocAnnonces

pytestmark = pytest.mark.e2e
IDS = (IDENTIFIANT_TEST, MDP_TEST)


class LLMEteint:
    def post(self, *a, **k):
        raise requests.ConnectionError("pas d'Ollama pendant les tests")


@pytest.fixture
def site(request):
    s = request.param().demarrer()
    yield s
    s.arreter()


def fabriquer(classe, site, tmp_path, dry_run, prefixe="", **k):
    a = classe(tmp_path / classe.nom, identifiants=IDS, headless=True, url_base=site.url + prefixe, **k)
    n = NotifieurFactice()
    profil = tmp_path / "profil.md"
    profil.write_text("- Situation professionnelle : CDI depuis 2021\n- Revenus nets mensuels : 3200 €\n", encoding="utf-8")
    m = Moteur(a, Memoire(a.dossier), n, dry_run=dry_run,
               motifs=GenerateurMotif(profil=profil, session=LLMEteint()),
               alertes=GestionAlertes(n, tmp_path / "alertes.json"))
    return a, n, m


# ----------------------------------------------------------------------------- ALJT
@pytest.mark.parametrize("site", [FauxALJT], indirect=True)
def test_aljt_bout_en_bout(site, tmp_path):
    site.etat["offres"] += [FauxALJT.offre(1), FauxALJT.offre(2, "PARIS", "75011", 700)]
    a, n, m = fabriquer(AdaptateurALJT, site, tmp_path, dry_run=True)
    try:
        m.passage()                                          # connexion auto + ligne de base
        assert "2 offres déjà en ligne ignorées" in n.textes()[0]

        site.etat["offres"].append(FauxALJT.offre(3, "MONTREUIL", "93100", 790))
        m.passage()                                          # DRY-RUN : jamais d'envoi
        assert site.posts("register") == []
        assert "DRY-RUN" in n.textes()[-1] and "NON cliqué" in n.textes()[-1]
        assert n.messages[-1]["photo"]                       # capture avant le clic final

        m.dry_run = False                                    # production simulée
        site.etat["offres"] += [FauxALJT.offre(4, "ST DENIS", "93200", 650),
                                FauxALJT.offre(5, "PANTIN", "93500", 700, nb=5),
                                FauxALJT.offre(6, "BOBIGNY", "93000", 600)]
        m.passage()
        assert [p[2] for p in site.posts("register")] == ["offId=4"]       # une seule candidature
        textes = "\n".join(n.textes())
        assert "CANDIDATURE ENVOYÉE" in textes and "déjà 5 candidats" in textes
        site.etat["offres"].append(FauxALJT.offre(7, "PANTIN", "93500", 700))
        m.passage()
        assert len(site.posts("register")) == 1 and "candidature déjà active" in n.textes()[-1]
    finally:
        a.fermer()


@pytest.mark.parametrize("site", [FauxALJT], indirect=True)
def test_aljt_session_expiree_ne_fausse_pas_la_ligne_de_base(site, tmp_path):
    """Hors connexion le vrai site renvoie « 0 offre » : il ne faut ni initialiser la mémoire à vide,
    ni croire qu'aucune candidature n'est active."""
    site.etat["offres"] += [FauxALJT.offre(1), FauxALJT.offre(2)]
    a, n, m = fabriquer(AdaptateurALJT, site, tmp_path, dry_run=True)
    a.connexion_auto = False
    try:
        m.boucle(__import__("threading").Event(), une_fois=True)
        assert not m.m.initialise                            # rien d'enregistré
        assert "connexion automatique désactivée" in (m.m.etat["derniere_erreur"] or "")
    finally:
        a.fermer()


# ----------------------------------------------------------------------------- LOC'ANNONCES
@pytest.mark.parametrize("site", [FauxParis], indirect=True)
def test_locannonces_bout_en_bout(site, tmp_path):
    site.etat["offres"].append(FauxParis.offre(10))
    a, n, m = fabriquer(AdaptateurLocAnnonces, site, tmp_path, dry_run=True, prefixe="/locannonces")
    try:
        m.passage()
        assert "1 offres déjà en ligne ignorées" in n.textes()[0]

        site.etat["offres"].append(FauxParis.offre(11, loyer=700, charges=120, nb=1))
        m.passage()                                          # DRY-RUN : arrêt avant « Candidater »
        assert site.posts("candidature.html") == []
        assert "DRY-RUN" in n.textes()[-1] and "Motif (secours)" in n.textes()[-1]
        assert list((a.dossier / "motifs").glob("*.txt"))

        m.dry_run = False
        site.etat["offres"].append(FauxParis.offre(12, loyer=800, nb=2))
        site.etat["nb_apres_lecture"][12] = 1                # un 3e candidat juste avant l'envoi
        site.etat["offres"].append(FauxParis.offre(13, loc="MONTREUIL 93", loyer=780, nb=3))
        site.etat["offres"].append(FauxParis.offre(14, loyer=840, nb=0))
        m.passage()
        assert [p[1] for p in site.posts("candidature.html")] == ["/locannonces/logement/14/candidature.html"]
        assert site.etat["candidature"] == 14
        assert 0 < len(site.etat["motifs"][0]) <= FauxParis.MAXLEN
        textes = "\n".join(n.textes())
        assert "3 candidatures" in textes and "CANDIDATURE ENVOYÉE" in textes

        site.etat["offres"].append(FauxParis.offre(15, loyer=700, nb=0))
        m.passage()
        assert len(site.posts("candidature.html")) == 1 and "candidature déjà active" in n.textes()[-1]
    finally:
        a.fermer()


# ----------------------------------------------------------------------------- ACTION LOGEMENT
@pytest.mark.parametrize("site", [FauxAL], indirect=True)
def test_action_logement_bout_en_bout(site, tmp_path):
    site.etat["offres"] += [FauxAL.offre(1), FauxAL.offre(2, "Montreuil", "93100", 790)]
    a, n, m = fabriquer(AdaptateurActionLogement, site, tmp_path, dry_run=True, api=site.url + "/api/v1")
    try:
        m.passage()
        assert "2 offres déjà en ligne ignorées" in n.textes()[0]
        # DRY-RUN : autorisation lue, mais POST /candidate jamais envoyé
        site.etat["offres"] += [FauxAL.offre(3, "Paris 18e Arrondissement", "75018", 870.0),
                                FauxAL.offre(4, "Bobigny", "93000", 600.0)]
        m.passage()
        assert site.posts("/demands/candidate") == []
        dry = [m for m in n.messages if "DRY-RUN" in m.get("texte", "")]
        assert len(dry) == 1 and "🔴🔴" in dry[0]["texte"] and dry[0]["photo"]
        assert any("hors critères" in t and "Bobigny" in t for t in n.textes())   # Bobigny : simple info

        # Production : 2/5 utilisées → 2 candidatures auto, puis la dernière place est gardée (alerte 🚨)
        m.dry_run = False
        site.etat["offres"] += [FauxAL.offre(5, "Pantin", "93500", 700.0), FauxAL.offre(6, "Montreuil", "93100", 720.0),
                                FauxAL.offre(7, "Paris 11e Arrondissement", "75011", 800.0)]
        m.passage()
        assert len(site.posts("/demands/candidate")) == 2 and site.etat["compteur"] == 4
        textes = "\n".join(n.textes())
        assert textes.count("CANDIDATURE ENVOYÉE") == 2
        alerte = n.messages[-1]
        assert "🚨" in alerte["texte"] and "75011" in alerte["texte"] and "réservée" in alerte["texte"]
        assert alerte["boutons"] == [("✅ Vu", alerte["boutons"][0][1])] and alerte["photo"]
    finally:
        a.fermer()


# ----------------------------------------------------------------------------- AL'IN
@pytest.mark.parametrize("site", [__import__("faux_sites.serveur", fromlist=["FauxAlin"]).FauxAlin], indirect=True)
def test_alin_bout_en_bout(site, tmp_path):
    from faux_sites.serveur import FauxAlin
    from plateformes import AdaptateurAlin
    site.etat["offres"].append(FauxAlin.offre(1))
    a, n, m = fabriquer(AdaptateurAlin, site, tmp_path, dry_run=True, api=site.url + "/api")
    try:
        m.passage()                                             # connexion par formulaire + ligne de base
        assert "1 offres déjà en ligne ignorées" in n.textes()[0]

        site.etat["offres"] += [FauxAlin.offre(2, "Pantin", "93500", 760.0), FauxAlin.offre(3, "Bobigny", "93000", 600.0)]
        m.passage()                                             # DRY-RUN : rien envoyé
        assert site.etat["candidatures"] == []
        assert any("DRY-RUN" in t for t in n.textes()) and any("hors critères" in t and "Bobigny" in t for t in n.textes())

        m.dry_run = False
        site.etat["offres"] += [FauxAlin.offre(4, "Montreuil", "93100", 790.0), FauxAlin.offre(5, "Paris", "75011", 800.0)]
        m.passage()                                             # une seule candidature, la suivante est notifiée
        assert [c["housing_offer_id"] for c in site.etat["candidatures"]] == ["of4"]
        textes = "\n".join(n.textes())
        assert "CANDIDATURE ENVOYÉE" in textes and "déjà en cours" in textes

        # Session expirée côté site, vieux jeton resté dans le navigateur (fenêtre « session expirée ») :
        # l'agent doit se reconnecter quand même.
        a.ctx.clear_cookies()
        a.page.evaluate("() => sessionStorage.setItem('paw_id_token', 'x.eyJleHAiOjF9.y')")
        a.connexion()
        assert a._jeton_valide()
    finally:
        a.fermer()


@pytest.mark.parametrize("site", [__import__("faux_sites.serveur", fromlist=["FauxAlin"]).FauxAlin], indirect=True)
def test_alin_mot_de_passe_refuse_un_seul_essai(site, tmp_path):
    """Mot de passe refusé : un seul essai, moteur arrêté, pas de nouvel essai même après redémarrage,
    reprise dès que le mot de passe est modifié (le site bloque le compte après 5 échecs)."""
    import threading
    from plateformes import AdaptateurAlin
    a, n, m = fabriquer(AdaptateurAlin, site, tmp_path, dry_run=True, api=site.url + "/api")
    a.identifiants = (IDENTIFIANT_TEST, "mauvais")
    m.forcer.set()
    m.boucle(threading.Event())                               # revient tout seul : moteur arrêté
    assert m.arrete and site.etat["essais"] == 1
    assert "mauvais" not in (a.dossier / "identifiants_refuses").read_text()
    assert any("Surveillance arrêtée" in t for t in n.textes())

    a2, n2, m2 = fabriquer(AdaptateurAlin, site, tmp_path, dry_run=True, api=site.url + "/api")
    a2.identifiants = (IDENTIFIANT_TEST, "mauvais")           # « redémarrage » avec le même mot de passe
    m2.boucle(threading.Event())
    assert m2.arrete and site.etat["essais"] == 1             # aucun essai de plus

    a3, n3, m3 = fabriquer(AdaptateurAlin, site, tmp_path, dry_run=True, api=site.url + "/api")
    try:                                                      # mot de passe corrigé : reprise
        m3.passage()
        assert site.etat["essais"] == 2 and not (a3.dossier / "identifiants_refuses").exists()
    finally:
        a3.fermer()
