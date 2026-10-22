import pytest

from coeur.criteres import classer
from coeur.offre import Offre


def o(ville, cp, loyer, surface=45, pieces=2, plateforme="aljt"):
    return Offre(plateforme=plateforme, id="x", ville=ville, code_postal=cp, loyer=loyer, surface=surface, pieces=pieces)


@pytest.mark.parametrize("loyer,ok", [(880, True), (879.99, True), (880.01, False), (950, False)])
def test_paris_880(loyer, ok):
    assert bool(classer(o("Paris 20e", "75020", loyer))) is ok


@pytest.mark.parametrize("loyer,ok", [(850, True), (851, False), (880, False)])
def test_paris_locannonces_850(loyer, ok):
    assert bool(classer(o("Paris", "75013", loyer, plateforme="locannonces"))) is ok


def test_paris_sans_code_postal_libelle_locannonces():
    assert classer(o("Paris", "75020", 800, plateforme="locannonces"))
    assert classer(o("Paris 15e Arrondissement", "75015", 800, plateforme="action_logement"))


@pytest.mark.parametrize("ville,cp", [
    ("Saint-Ouen-sur-Seine", "93400"), ("ST OUEN", "93400"), ("Saint-Denis", "93200"), ("ST DENIS", "93200"),
    ("Saint-Denis", "93380"), ("Aubervilliers", "93300"), ("PANTIN", "93500"), ("Le Pré-Saint-Gervais", "93310"),
    ("LE PRE ST GERVAIS", "93310"), ("Les Lilas", "93260"), ("Bagnolet", "93170"), ("Montreuil", "93100"),
    ("MONTREUIL", "93"),
])
def test_8_communes_93(ville, cp):
    assert classer(o(ville, cp, 800))
    assert not classer(o(ville, cp, 800.5))


def test_prioritaires_pantin_montreuil():
    assert classer(o("Pantin", "93500", 700)).prioritaire
    assert classer(o("Montreuil", "93100", 700)).prioritaire
    assert not classer(o("Bagnolet", "93170", 700)).prioritaire


@pytest.mark.parametrize("ville,cp", [("Bobigny", "93000"), ("Sevran", "93270"), ("Vincennes", "94300"),
                                      ("Malakoff", "92240"), ("Créteil", "94000"), ("Montreuil", "62170"),
                                      ("Saint-Denis", "97400"), ("Boulogne-Billancourt", "92100")])
def test_communes_rejetees(ville, cp):
    assert not classer(o(ville, cp, 500))


def test_surface_et_pieces():
    assert not classer(o("Paris", "75011", 700, surface=39.9))
    assert classer(o("Paris", "75011", 700, surface=40))
    assert not classer(o("Paris", "75011", 700, pieces=1))
    assert not classer(o("Paris", "75011", 700, pieces=None))
    assert classer(o("Paris", "75011", 700, pieces=3, surface=60))


def test_loyer_inconnu_rejete():
    assert not classer(o("Paris", "75011", None))
