import json
from datetime import datetime, timedelta

import pytest
import requests

from coeur.alertes import GestionAlertes
from coeur.memoire import Memoire
from coeur.motif import GenerateurMotif, faits_inventes, lire_profil, tronquer
from coeur.offre import Offre
from coeur.telegram import NotifieurFactice


def offres(*ids, pub=None):
    return [Offre(plateforme="aljt", id=str(i), date_publication=pub) for i in ids]


# ------------------------------------------------------------------ mémoire
def test_offres_deja_en_ligne_jamais_traitees(tmp_path):
    m = Memoire(tmp_path, seuil_anomalie=15)
    nouvelles, info = m.filtrer_nouvelles(offres(1, 2, 3))
    assert nouvelles == [] and info.ligne_de_base and info.nb_ignorees == 3
    nouvelles, _ = m.filtrer_nouvelles(offres(1, 2, 3, 4))
    assert [x.id for x in nouvelles] == ["4"]
    # persistance : un redémarrage ne retraite rien
    m2 = Memoire(tmp_path)
    for x in nouvelles:
        m2.marquer_vue(x)
    assert Memoire(tmp_path).filtrer_nouvelles(offres(1, 2, 3, 4))[0] == []


def test_ligne_de_base_vide_puis_nouvelle(tmp_path):
    m = Memoire(tmp_path)
    assert m.filtrer_nouvelles([])[1].ligne_de_base
    assert [x.id for x in m.filtrer_nouvelles(offres(7))[0]] == ["7"]


def test_date_publication_anterieure_ignoree(tmp_path):
    m = Memoire(tmp_path)
    m.filtrer_nouvelles([])
    hier = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    aujourdhui = datetime.now().strftime("%Y-%m-%d")
    nouv, info = m.filtrer_nouvelles(offres(1, pub=hier) + offres(2, pub=aujourdhui))
    assert [x.id for x in nouv] == ["2"]            # même jour (date seule) : gardée
    assert info.anciennes_par_date == 1
    avant = (datetime.now().astimezone() - timedelta(hours=2)).isoformat()
    assert m.filtrer_nouvelles(offres(3, pub=avant))[0] == []


def test_garde_fou_anomalie(tmp_path):
    m = Memoire(tmp_path, seuil_anomalie=5)
    m.filtrer_nouvelles(offres(1))
    nouv, info = m.filtrer_nouvelles(offres(*range(100, 110)))
    assert nouv == [] and info.anomalie and info.nb_anomalie == 10
    assert all(m.deja_vue(str(i)) for i in range(100, 110))
    assert len(m.filtrer_nouvelles(offres(*range(100, 110), 200, 201))[0]) == 2   # sous le seuil : traitées


# ------------------------------------------------------------------ relances AL
class Horloge:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_relance_alerte_jusqu_au_vu(tmp_path):
    n, h = NotifieurFactice(), Horloge()
    g = GestionAlertes(n, tmp_path / "a.json", intervalle_s=120, relances_max=10, horloge=h)
    aid = g.nouvelle("action_logement", "🚨 offre")
    assert len(n.textes()) == 1 and n.messages[0]["boutons"] == [("✅ Vu", f"vu:{aid}")]
    assert g.tick() == 0                       # pas encore 2 min
    h.t += 121
    assert g.tick() == 1 and "Relance 1/10" in n.textes()[-1]
    h.t += 121
    g.tick()
    assert g.marquer_vu(aid)
    h.t += 1000
    assert g.tick() == 0                       # plus rien après « Vu »
    assert len(n.textes()) == 3


def test_relances_plafonnees_a_10_et_persistantes(tmp_path):
    n, h = NotifieurFactice(), Horloge()
    g = GestionAlertes(n, tmp_path / "a.json", 120, 10, horloge=h)
    g.nouvelle("action_logement", "🚨")
    for _ in range(30):
        h.t += 121
        g.tick()
    assert len(n.textes()) == 11               # 1 alerte + 10 relances
    assert GestionAlertes(n, tmp_path / "a.json", 120, 10, horloge=h).en_attente() == []


# ------------------------------------------------------------------ motif
PROFIL = """# Profil
- Situation professionnelle : CDI d'ingénieur depuis 2021
- Revenus nets mensuels : 3200 €
- Logement actuel : résidence ALJT à L'Île-Saint-Denis
- Ancienneté de la demande : depuis 2023
- Lien avec le quartier : ...
"""


class FauxLLM:
    def __init__(self, reponse=None, erreur=None):
        self.reponse, self.erreur, self.appels = reponse, erreur, 0

    def post(self, *a, **k):
        self.appels += 1
        if self.erreur:
            raise self.erreur

        class R:
            def raise_for_status(s): pass
            def json(s): return {"response": self.reponse}
        return R()


@pytest.fixture
def profil(tmp_path):
    p = tmp_path / "profil_candidat.md"
    p.write_text(PROFIL, encoding="utf-8")
    return p


OFFRE = Offre(plateforme="locannonces", id="18734", ville="Paris", code_postal="75020", loyer=817, surface=37, pieces=2)


def test_lecture_profil_ignore_les_vides(profil):
    champs = lire_profil(profil)
    assert "Lien avec le quartier" not in champs and champs["Revenus nets mensuels"] == "3200 €"


def test_motif_llm_accepte_si_faits_du_profil(profil):
    txt = ("Je travaille en CDI d'ingénieur depuis 2021 et je perçois 3200 € nets par mois. "
           "Je vis actuellement en résidence ALJT et ma demande date de 2023. Ce T2 de 37 m² me conviendrait.")
    m = GenerateurMotif(profil=profil, session=FauxLLM(txt)).generer(OFFRE, 1000)
    assert m.source == "llm" and m.texte == txt


def test_motif_llm_qui_invente_un_chiffre_bascule_sur_secours(profil):
    txt = ("Je travaille en CDI depuis 2021, j'ai deux enfants de 4 et 7 ans et je gagne 3200 €. "
           "Ce logement serait idéal pour ma famille, nous attendons depuis longtemps.")
    m = GenerateurMotif(profil=profil, session=FauxLLM(txt)).generer(OFFRE, 1000)
    assert m.source == "secours" and "4" in m.raison_secours
    assert "enfant" not in m.texte


def test_motif_secours_si_llm_indisponible(profil):
    m = GenerateurMotif(profil=profil, session=FauxLLM(erreur=requests.ConnectionError("ollama éteint"))).generer(OFFRE, 300)
    assert m.source == "secours" and len(m.texte) <= 300
    assert "CDI d'ingénieur depuis 2021" in m.texte
    assert not faits_inventes(m.texte, PROFIL + " 18734 75020 2 817 37")


def test_longueur_max_respectee(profil):
    long = "Je travaille en CDI d'ingénieur depuis 2021. " * 80
    assert len(GenerateurMotif(profil=profil, session=FauxLLM(long)).generer(OFFRE, 500).texte) <= 500
    assert len(tronquer("a " * 1000, 120)) <= 120


# ------------------------------------------------------------------ veille / arrêt
def test_message_arret_du_pc():
    from coeur.eveil import Battement
    assert Battement.message_arret(None, 1000) is None            # premier lancement
    assert Battement.message_arret(1000, 1100) is None            # simple redémarrage de l'agent
    msg = Battement.message_arret(1000, 1000 + 3600 * 2)
    assert "ARRÊTÉ" in msg and "120 min" in msg


def test_eveil_ne_plante_pas():
    from coeur import eveil
    assert isinstance(eveil.sur_secteur(), bool)
    eveil.maintenir_eveil(True)
    eveil.maintenir_eveil(False)


# ------------------------------------------------------------------ questions libres sur Telegram
def test_assistant_repond_meme_sans_llm(tmp_path):
    from coeur.assistant import repondre
    journal = tmp_path / "agent.log"
    journal.write_text("2026-10-24 03:20:52,395 INFO    [telegram] → Telegram : <b>[Agent]</b> 😴 La machine était "
                       "en veille de 13:30 à 03:20\n", encoding="utf-8")
    rep = repondre("tu fais quoi ?", "<b>Agent</b> actif", {}, journal, "http://127.0.0.1:9", "x", timeout=2,
                   session=FauxLLM(erreur=requests.ConnectionError("pas d'Ollama")))
    assert "IA locale indisponible" in rep and "Agent actif" in rep and "veille" in rep
