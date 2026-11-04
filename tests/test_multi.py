"""Multi-personnes : critères par zones, annuaire, coffre chiffré, aiguillage Telegram, interface web."""
import json
import re

import pytest

import coeur.personnes as P
from coeur.criteres import Criteres, ResolveurCP
from coeur.offre import Offre


@pytest.fixture
def donnees(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "DATA", tmp_path)
    return tmp_path


def o(ville, cp, loyer, surface=45, pieces=2):
    return Offre(plateforme="x", id="1", ville=ville, code_postal=cp, loyer=loyer, surface=surface, pieces=pieces)


# ------------------------------------------------------------------ critères
def test_criteres_par_zones_et_plafond_plateforme(tmp_path):
    zones = [P.Zone(["75"], 880), P.Zone(["93100", "93500"], 800)]
    r = ResolveurCP(tmp_path / "cache.json")
    c = Criteres(zones, 2, 40)
    assert c.classer(o("Paris", "75020", 880), r)
    assert not c.classer(o("Paris", "75020", 881), r)
    assert c.classer(o("MONTREUIL", "93", 790), r)                 # LOC'Annonces : seulement le département
    assert not c.classer(o("Montreuil", "62170", 500), r)          # homonyme hors zone
    assert not c.classer(o("Bobigny", "93000", 500), r)
    assert not c.classer(o("Paris", "75011", 700, surface=39), r)
    assert not c.classer(o("Paris", "75011", 700, pieces=1), r)
    assert not Criteres(zones, 2, 40, loyer_max_plateforme=850).classer(o("Paris", "75020", 860), r)
    assert Criteres(zones, 3, 60).classer(o("Paris", "75020", 800, surface=60, pieces=3), r)
    assert not Criteres([], 2, 40).classer(o("Paris", "75020", 500), r)    # aucune zone : rien ne correspond


def test_codes_depuis_texte():
    assert P.codes_depuis_texte("75, 93100 93500;94") == ["75", "93100", "93500", "94"]


# ------------------------------------------------------------------ annuaire / coffre
def test_annuaire_et_liaison_telegram(donnees):
    a = P.Annuaire(donnees / "personnes.json")
    a.enregistrer(P.Personne(id="ana", nom="Ana", code_liaison="ABC123"))
    a.enregistrer(P.Personne(id="bob", nom="Bob", code_liaison="DEF456", telegram_chat_id="42"))
    assert a.lier_telegram("zzz", "42") is None
    p = a.lier_telegram("abc123", "42")                            # insensible à la casse
    assert p.id == "ana" and a.obtenir("ana").telegram_chat_id == "42" and a.obtenir("ana").code_liaison == ""
    assert a.obtenir("bob").telegram_chat_id == ""                  # un chat = une seule personne
    assert a.par_chat("42").id == "ana"


def test_coffre_chiffre_et_jamais_en_clair(donnees):
    p = P.Personne(id="ana", nom="Ana")
    p.coffre.enregistrer("alin", "S3cret#pass")
    brut = (p.dossier / "secrets" / "alin.bin").read_bytes()
    assert b"S3cret" not in brut and p.coffre.lire("alin") == "S3cret#pass"
    p.coffre.effacer("alin")
    assert not p.coffre.present("alin") and p.coffre.lire("alin") == ""


# ------------------------------------------------------------------ agent : aiguillage par personne
class Args:
    dry_run = True; plateformes = None; visible = False; debug = False; personne = None
    telegram_factice = True; sans_connexion_auto = True; once = True


@pytest.fixture
def agent(donnees):
    import main
    from coeur.config import Config
    a = P.Annuaire(donnees / "personnes.json")
    a.enregistrer(P.Personne(id="ana", nom="Ana", telegram_chat_id="1", zones=[P.Zone(["75"], 900)],
                             plateformes={"aljt": P.ReglagePlateforme(actif=True)}))
    a.enregistrer(P.Personne(id="bob", nom="Bob", code_liaison="B0B000", zones=[P.Zone(["93"], 800)],
                             plateformes={"alin": P.ReglagePlateforme(actif=True, production=True)}))
    return main.Agent(Config(intervalles={}, production={}, identifiants={}, telegram_chat_id="999"), Args(), annuaire=a)


def test_chaque_personne_ne_voit_que_son_agent(agent):
    s1 = agent.recevoir("1", "commande", "/statut")
    assert "Ana" in s1 and "Pass Logement" in s1 and "AL'in" not in s1
    assert "/lier" in agent.recevoir("555", "commande", "/statut")   # inconnu : invité à se lier
    assert "Bob" in agent.recevoir("777", "commande", "/lier b0b000")
    assert agent.annuaire.par_chat("777").id == "bob"
    agent.recevoir("1", "commande", "/pause")
    assert agent.espaces["ana"].pause.is_set() and not agent.espaces["bob"].pause.is_set()
    admin = agent.recevoir("999", "commande", "/statut")             # l'administrateur voit tout
    assert "Ana" in admin and "Bob" in admin


def test_moteurs_selon_plateformes_et_mode(agent):
    assert set(agent.espaces["ana"].moteurs) == {"aljt"}
    assert agent.espaces["ana"].moteurs["aljt"].dry_run is True
    assert agent.espaces["bob"].moteurs["alin"].dry_run is True      # --dry-run force le mode test
    assert agent.espaces["bob"].moteurs["alin"].criteres.zones[0].codes_postaux == ["93"]


def test_notif_personne_non_reliee_va_a_l_admin(agent):
    esp = agent.espaces["bob"]
    esp.notif.envoyer("alin", "test")
    assert "Bob" in agent.notif_admin.textes()[-1] and "non relié" in agent.notif_admin.textes()[-1]


# ------------------------------------------------------------------ interface web
@pytest.fixture
def client(donnees):
    from interface import serveur
    app = serveur.creer_app(agent=None, annuaire=P.Annuaire(donnees / "personnes.json"))
    app.config["TESTING"] = True
    return app.test_client(), serveur.JETON


def test_interface_creation_modification_sans_fuite_de_mot_de_passe(client, donnees):
    c, jeton = client
    form = {"jeton": jeton, "nom": "Chloé Martin", "actif": "on", "pieces_min": "3", "surface_min": "55",
            "zone_cp_0": "75011, 75020", "zone_loyer_0": "1100", "zone_cp_1": "93100", "zone_loyer_1": "900",
            "alin_actif": "on", "alin_identifiant": "chloe@exemple.test", "alin_mdp": "MotDePasse!42",
            "aljt_identifiant": "", "profil": "- Situation professionnelle : CDI\n"}
    r = c.post("/personne/nouvelle", data=form)
    assert r.status_code == 302
    personnes = json.loads((donnees / "personnes.json").read_text(encoding="utf-8"))["personnes"]
    assert len(personnes) == 1 and "MotDePasse" not in json.dumps(personnes)
    p = P.Annuaire(donnees / "personnes.json").obtenir("chloe-martin")
    assert p.pieces_min == 3 and p.zones[1].codes_postaux == ["93100"] and p.zones[1].loyer_max == 900
    assert p.plateformes["alin"].actif and not p.plateformes["alin"].production
    assert p.coffre.lire("alin") == "MotDePasse!42"
    page = c.get("/personne/chloe-martin").get_data(as_text=True)
    assert "MotDePasse" not in page and "enregistré ✔" in page and re.search(r"/lier [0-9A-F]{6}", page)
    # modification sans retaper le mot de passe : il est conservé
    form.update({"alin_mdp": "", "alin_production": "on"})
    c.post("/personne/chloe-martin", data=form)
    p = P.Annuaire(donnees / "personnes.json").obtenir("chloe-martin")
    assert p.plateformes["alin"].production and p.coffre.lire("alin") == "MotDePasse!42"
    assert "Chloé Martin" in c.get("/").get_data(as_text=True)


def test_interface_refuse_sans_jeton_et_suppression_confirmee(client, donnees):
    c, jeton = client
    assert c.post("/personne/nouvelle", data={"nom": "Pirate"}).status_code == 403
    c.post("/personne/nouvelle", data={"jeton": jeton, "nom": "Dan", "zone_cp_0": "75", "zone_loyer_0": "900",
                                      "aljt_mdp": "x"})
    c.post("/personne/dan/supprimer", data={"jeton": jeton, "confirmation": "mauvais"})
    assert P.Annuaire(donnees / "personnes.json").obtenir("dan")
    c.post("/personne/dan/supprimer", data={"jeton": jeton, "confirmation": "Dan"})
    assert P.Annuaire(donnees / "personnes.json").obtenir("dan") is None
    assert not (donnees / "personnes" / "dan" / "secrets").exists()


def test_admin_relie_a_sa_fiche_voit_tout_le_monde(agent):
    agent.cfg.telegram_chat_id = "1"                          # l'admin est aussi « Ana »
    s = agent.recevoir("1", "commande", "/statut")
    assert "Ana" in s and "Bob" in s
    agent.recevoir("1", "commande", "/pause")                # les autres commandes restent les siennes
    assert agent.espaces["ana"].pause.is_set() and not agent.espaces["bob"].pause.is_set()
