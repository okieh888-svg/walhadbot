"""Moteur + politiques avec un adaptateur simulé (sans navigateur)."""
from coeur.alertes import GestionAlertes
from coeur.memoire import Memoire
from coeur.moteur import Moteur
from coeur.offre import Offre
from coeur.telegram import NotifieurFactice
from plateformes.base import Adaptateur, ResultatCandidature


class FauxAdaptateur(Adaptateur):
    def __init__(self, dossier, nom="aljt", besoin_motif=False):
        self.nom = nom
        self.besoin_motif = besoin_motif
        super().__init__(dossier)
        self.offres: list[Offre] = []
        self.active = False
        self.envois: list[tuple[str, str, bool]] = []
        self.nb_relu: dict[str, int] = {}

    def demarrer(self): pass
    def fermer(self): pass
    def connexion(self): pass
    def capture(self, nom, page=None): return None
    def capture_offre(self, offre): return None
    def lister_offres(self): return [Offre(**{**o.to_dict()}) for o in self.offres]
    def candidature_active(self): return self.active
    def nombre_candidats(self, offre): return self.nb_relu.get(offre.id, offre.nb_candidats)

    def enrichir(self, offre):
        if self.nom == "action_logement":
            offre.quota_restant = self.quota
        return offre

    quota = None

    def candidater(self, offre, motif, dry_run):
        self.envois.append((offre.id, motif, dry_run))
        if self.nom == "action_logement" and not dry_run:
            self.quota -= 1
        if not dry_run:
            self.active = True
        return ResultatCandidature(True, "ok", envoye=not dry_run)


class FauxMotifs:
    def generer(self, offre, maxi):
        from coeur.motif import Motif
        return Motif("Motif de test.", "secours")


def offre(i, ville="Pantin", cp="93500", loyer=750, nb=0, p="aljt"):
    return Offre(plateforme=p, id=str(i), ville=ville, code_postal=cp, loyer=loyer, surface=45, pieces=2, nb_candidats=nb)


def moteur(tmp_path, nom="aljt", dry=False, **k):
    a = FauxAdaptateur(tmp_path / nom, nom, besoin_motif=(nom == "locannonces"))
    n = NotifieurFactice()
    m = Moteur(a, Memoire(tmp_path / nom), n, dry_run=dry, motifs=FauxMotifs(), **k)
    return a, n, m


def test_aljt_premier_passage_puis_une_seule_candidature(tmp_path):
    a, n, m = moteur(tmp_path)
    a.offres = [offre(1), offre(2)]
    m.passage()
    assert a.envois == [] and "2 offres déjà en ligne ignorées" in n.textes()[0]
    a.offres += [offre(3, "Bagnolet", "93170"), offre(4, "Montreuil", "93100"), offre(5, "Bobigny", "93000")]
    m.passage()
    assert [e[0] for e in a.envois] == ["4"]               # Montreuil prioritaire, puis une seule
    t = "\n".join(n.textes())
    assert "✅" in t and "CANDIDATURE ENVOYÉE" in t and " s</b>" in t
    assert "NON CANDIDATÉE" in t and "candidature déjà active" in t     # l'offre 3 est notifiée
    m.passage()
    assert len(a.envois) == 1                                              # rien de nouveau


def test_aljt_cinq_candidats_et_candidature_active(tmp_path):
    a, n, m = moteur(tmp_path)
    m.passage()
    a.offres = [offre(1, nb=5)]
    m.passage()
    assert a.envois == [] and "déjà 5 candidats" in n.textes()[-1]
    a.active = True
    a.offres.append(offre(2, nb=0))
    m.passage()
    assert a.envois == [] and "candidature déjà active" in n.textes()[-1]


def test_aljt_dry_run_ne_envoie_rien(tmp_path):
    a, n, m = moteur(tmp_path, dry=True)
    m.passage()
    a.offres = [offre(1)]
    m.passage()
    assert a.envois == [("1", None, True)] and "DRY-RUN" in n.textes()[-1]


def test_locannonces_reverification_avant_envoi(tmp_path):
    a, n, m = moteur(tmp_path, "locannonces")
    m.passage()
    a.offres = [offre(1, "Paris", "75020", 840, nb=2, p="locannonces")]
    a.nb_relu["1"] = 3                                     # un 3e candidat arrive entre-temps
    m.passage()
    assert a.envois == [] and "3 candidatures" in n.textes()[-1]
    a.offres.append(offre(2, "Paris", "75020", 840, nb=1, p="locannonces"))
    m.passage()
    assert a.envois == [("2", "Motif de test.", False)]
    assert "Motif (secours)" in n.textes()[-1]
    assert list((tmp_path / "locannonces" / "motifs").glob("*.txt"))
    a.offres.append(offre(3, "Paris", "75019", 700, nb=0, p="locannonces"))
    m.passage()
    assert len(a.envois) == 1 and "candidature déjà active" in n.textes()[-1]


def test_locannonces_plafond_850_et_93_800(tmp_path):
    a, n, m = moteur(tmp_path, "locannonces")
    m.passage()
    a.offres = [offre(1, "Paris", "75020", 860, p="locannonces"), offre(2, "Pantin", "93500", 820, p="locannonces")]
    m.passage()
    assert a.envois == [] and len(n.textes()) == 1          # rejetées sans notification


def test_action_logement_candidate_puis_garde_la_derniere_place(tmp_path):
    n = NotifieurFactice()
    al = GestionAlertes(n, tmp_path / "al.json")
    a = FauxAdaptateur(tmp_path / "action_logement", "action_logement")
    a.quota = 2                                            # 3/5 utilisées
    m = Moteur(a, Memoire(tmp_path / "action_logement"), n, alertes=al, dry_run=False)
    m.passage()
    a.offres = [offre(1, "Paris 18e Arrondissement", "75018", 820, p="action_logement"),
                offre(2, "Montreuil", "93100", 780, p="action_logement")]
    m.passage()
    assert [e[0] for e in a.envois] == ["2"]               # Montreuil d'abord, puis réserve atteinte
    assert any("CANDIDATURE ENVOYÉE" in t for t in n.textes())
    alerte = n.messages[-1]
    assert "🔴🔴" in alerte["texte"] and "🚨" in alerte["texte"] and "réservée" in alerte["texte"]
    assert alerte["boutons"][0][0] == "✅ Vu" and al.en_attente()


def test_garde_fou_anomalie_notifie(tmp_path):
    a, n, m = moteur(tmp_path)
    m.m.seuil = 3
    m.passage()
    a.offres = [offre(i) for i in range(10)]
    m.passage()
    assert a.envois == [] and "GARDE-FOU" in n.textes()[-1]


def test_agent_ko_sans_spam(tmp_path):
    import threading
    a, n, m = moteur(tmp_path)
    a.lister_offres = lambda: (_ for _ in ()).throw(RuntimeError("site en panne"))
    arret = threading.Event()
    for _ in range(6):
        m.boucle(arret, une_fois=True)
    ko = [t for t in n.textes() if "AGENT KO" in t]
    assert len(ko) == 1                                    # un seul message malgré 6 échecs


def test_chien_de_garde_relance_si_plateforme_bloquee(monkeypatch):
    import time as _t
    import pytest
    import main
    from coeur.config import Config

    class A:
        dry_run = True; plateformes = "aljt"; visible = False; debug = False
        telegram_factice = True; sans_connexion_auto = True; once = True

    ag = main.Agent(Config(intervalles={"aljt": 300}, production={}, identifiants={"aljt": ("", "")}), A())
    monkeypatch.setattr(main.os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    ag._chien_de_garde()                                    # récent : rien
    assert not ag.notif.textes()
    ag.moteurs["aljt"].derniere_activite = _t.time() - 3 * 3600
    with pytest.raises(SystemExit):
        ag._chien_de_garde()
    assert "BLOQUÉ" in ag.notif.textes()[-1]
