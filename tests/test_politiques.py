from coeur.criteres import Verdict
from coeur.offre import Offre
from coeur.politiques import ALERTER, CANDIDATER, IGNORER, NOTIFIER, decider

OK, KO = Verdict(True, "ok"), Verdict(False, "non")


def o(p, nb=None, eligible=None):
    return Offre(plateforme=p, id="1", nb_candidats=nb, eligible=eligible)


def test_aljt():
    assert decider(o("aljt", 0), OK, False).action == CANDIDATER
    assert decider(o("aljt", 4), OK, False).action == CANDIDATER
    assert decider(o("aljt", None), OK, False).action == CANDIDATER   # nombre non affiché : on tente
    assert decider(o("aljt", 5), OK, False).action == NOTIFIER
    assert decider(o("aljt", 9), OK, False).action == NOTIFIER
    assert decider(o("aljt", 0), OK, True).action == NOTIFIER          # candidature déjà active
    assert decider(o("aljt", 0), KO, False).action == IGNORER


def test_locannonces():
    for n in (0, 1, 2):
        assert decider(o("locannonces", n), OK, False).action == CANDIDATER
    for n in (3, 4, 239):
        assert decider(o("locannonces", n), OK, False).action == NOTIFIER
    assert decider(o("locannonces", None), OK, False).action == NOTIFIER   # inconnu : pas de candidature
    assert decider(o("locannonces", 0), OK, True).action == NOTIFIER       # une seule à la fois
    assert decider(o("locannonces", 0, eligible=False), OK, False).action == NOTIFIER
    assert decider(o("locannonces", 0), KO, False).action == IGNORER


def al(quota, eligible=None):
    x = o("action_logement", eligible=eligible)
    x.quota_restant = quota
    return x


def test_action_logement_garde_une_place_en_reserve():
    assert decider(al(3), OK, False).action == CANDIDATER
    assert decider(al(2), OK, False).action == CANDIDATER
    assert decider(al(1), OK, False).action == ALERTER       # dernière place : pour l'utilisateur
    assert decider(al(0), OK, False).action == ALERTER
    assert decider(al(None), OK, False).action == ALERTER    # quota inconnu : pas de risque
    assert decider(al(3, eligible=False), OK, False).action == ALERTER
    assert decider(al(3), KO, False).action == IGNORER
