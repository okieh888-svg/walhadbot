"""Politique de chaque plateforme : que faire d'une offre qui correspond aux critères ?

Séparé du code d'accès aux sites : fonctions pures, faciles à tester.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .criteres import Verdict
from .offre import Offre

CANDIDATER, NOTIFIER, ALERTER, IGNORER = "candidater", "notifier", "alerter", "ignorer"

ALJT_MAX_CANDIDATS = 5          # seuls les 5 premiers sont acceptés
PARIS_MAX_CANDIDATS = 3         # seuls les 3 premiers dossiers sont retenus


@dataclass
class Decision:
    action: str
    raison: str


def politique_aljt(offre: Offre, verdict: Verdict, candidature_active: bool) -> Decision:
    if not verdict:
        return Decision(IGNORER, verdict.raison)
    if candidature_active:
        return Decision(NOTIFIER, "candidature déjà active")
    if offre.nb_candidats is not None and offre.nb_candidats >= ALJT_MAX_CANDIDATS:
        return Decision(NOTIFIER, f"déjà {offre.nb_candidats} candidats (max {ALJT_MAX_CANDIDATS})")
    if offre.eligible is False:
        return Decision(NOTIFIER, "le site indique que je ne suis pas éligible")
    return Decision(CANDIDATER, verdict.raison)


def politique_locannonces(offre: Offre, verdict: Verdict, candidature_active: bool) -> Decision:
    if not verdict:
        return Decision(IGNORER, verdict.raison)
    if candidature_active:
        return Decision(NOTIFIER, "candidature déjà active")
    if offre.nb_candidats is None:
        return Decision(NOTIFIER, "nombre de candidatures inconnu : pas de candidature automatique")
    if offre.nb_candidats >= PARIS_MAX_CANDIDATS:
        return Decision(NOTIFIER, f"déjà {offre.nb_candidats} candidatures (≥ {PARIS_MAX_CANDIDATS})")
    if offre.eligible is False:
        return Decision(NOTIFIER, "inéligible selon le site (plafonds de ressources)")
    return Decision(CANDIDATER, verdict.raison)


AL_RESERVE = 1                  # places gardées pour l'utilisateur (candidatures non annulables)


def politique_action_logement(offre: Offre, verdict: Verdict, candidature_active: bool = False) -> Decision:
    """Candidature auto tant qu'il reste plus de AL_RESERVE place(s) libre(s) dans la catégorie
    de l'offre (5 « multiples », 1 « unique ») ; sinon alerte maximale."""
    if not verdict:
        return Decision(IGNORER, verdict.raison)
    if offre.eligible is False:
        return Decision(ALERTER, "le site refuse la candidature automatique sur cette offre")
    if offre.quota_restant is None:
        return Decision(ALERTER, "places de candidature restantes inconnues")
    if offre.quota_restant <= AL_RESERVE:
        return Decision(ALERTER, f"{offre.quota_restant} place(s) libre(s) : réservée(s) pour toi")
    return Decision(CANDIDATER, verdict.raison)


def politique_alin(offre: Offre, verdict: Verdict, candidature_active: bool) -> Decision:
    """AL'in : une seule candidature à la fois (annulable avant transmission au bailleur)."""
    if not verdict:
        return Decision(IGNORER, verdict.raison)
    if candidature_active:
        return Decision(NOTIFIER, "candidature AL'in déjà en cours")
    if offre.eligible is False:
        return Decision(NOTIFIER, "le site indique que je ne suis pas éligible")
    return Decision(CANDIDATER, verdict.raison)


POLITIQUES = {
    "aljt": politique_aljt,
    "locannonces": politique_locannonces,
    "action_logement": politique_action_logement,
    "alin": politique_alin,
}


def decider(offre: Offre, verdict: Verdict, candidature_active: bool,
            plateforme: Optional[str] = None) -> Decision:
    return POLITIQUES[plateforme or offre.plateforme](offre, verdict, candidature_active)
