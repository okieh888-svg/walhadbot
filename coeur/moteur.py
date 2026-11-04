"""Boucle principale d'une plateforme : lire → filtrer les nouvelles → décider → agir → notifier.

Une instance par plateforme, chacune dans son thread : une panne n'arrête pas les autres.
"""
from __future__ import annotations

import logging
import random
import threading
import time
import traceback
from datetime import datetime
from typing import Callable, Optional

from plateformes.base import Adaptateur, ConnexionImpossible, SessionExpiree

from .alertes import GestionAlertes
from .criteres import classer
from .memoire import Memoire, maintenant
from .motif import GenerateurMotif
from .offre import Offre
from .politiques import ALERTER, CANDIDATER, IGNORER, NOTIFIER, decider
from .telegram import Notifieur, echapper

ECHECS_AVANT_KO = 3
RAPPEL_KO_S = 3600


def _h(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


class Moteur:
    def __init__(self, adaptateur: Adaptateur, memoire: Memoire, notif: Notifieur, dry_run: bool = True,
                 intervalle: int = 30, motifs: Optional[GenerateurMotif] = None,
                 alertes: Optional[GestionAlertes] = None, pause: Optional[threading.Event] = None,
                 dormir: Callable[[float], None] = time.sleep):
        self.a = adaptateur
        self.p = adaptateur.nom
        self.m = memoire
        self.notif = notif
        self.dry_run = dry_run
        self.intervalle = intervalle
        self.motifs = motifs
        self.alertes = alertes
        self.pause = pause or threading.Event()
        self.forcer = threading.Event()
        self.criteres = None          # Criteres de la personne (None = critères historiques par défaut)
        self.resolveur = None         # ResolveurCP (nom de commune → code postal)
        self.limiteur = None          # threading.Semaphore partagé : nombre de navigateurs ouverts en même temps
        self.fermer_apres = False     # fermer le navigateur après chaque passage (économie de mémoire)
        self.etiquette = ""           # nom de la personne, pour le journal
        self.notif_hors_criteres = True
        self.derniere_activite = time.time()     # surveillé par le chien de garde (main.py)
        self.dormir = dormir
        self.log = logging.getLogger(f"moteur.{self.p}")
        self.connecte = False
        self.echecs = 0
        self.ko_signale_a = 0.0
        self.actif_cache: Optional[bool] = None
        self.dernier_passage: Optional[str] = None
        self.etat_candidatures: str = "pas encore lu"

    # ------------------------------------------------------------------
    def _assurer_connexion(self):
        if not self.connecte:
            self.a.demarrer()
            self.a.connexion()
            self.connecte = True

    def passage(self) -> list[Offre]:
        """Un passage complet. Rend les nouvelles offres traitées."""
        self._assurer_connexion()
        try:
            offres = self.a.lister_offres()
        except SessionExpiree:
            self.log.info("session expirée : reconnexion")
            self.connecte = False
            self._assurer_connexion()
            offres = self.a.lister_offres()

        try:   # état des candidatures, pour /statut et la politique
            self.etat_candidatures = self.a.resume_candidatures()
            if self.p != "action_logement":
                self.actif_cache = not self.etat_candidatures.startswith("aucune")
        except SessionExpiree:
            raise
        except Exception as e:
            self.log.warning("lecture des candidatures impossible : %s", e)
        premier = not self.m.initialise
        nouvelles, info = self.m.filtrer_nouvelles(offres)
        self.dernier_passage = maintenant()
        self.log.info("%d offres lues, %d nouvelles", len(offres), len(nouvelles))
        if info.ligne_de_base:
            self.notif.envoyer(self.p, f"▶️ Démarrage : {info.nb_ignorees} offres déjà en ligne ignorées "
                                       f"(jamais de candidature ni d'alerte sur celles-ci).")
        elif premier is False and info.anomalie:
            self.notif.envoyer(self.p, f"🔴🔴 ⚠️ GARDE-FOU :{info.nb_anomalie} « nouvelles » offres d'un coup "
                                       f"(seuil {self.m.seuil}). Marquées vues sans action. Vérifie le site.")
        if info.anciennes_par_date:
            self.log.info("%d offres publiées avant le premier démarrage ignorées", info.anciennes_par_date)

        # meilleures d'abord : prioritaires (Pantin/Montreuil) puis loyer croissant
        evaluees = [(o, self.classer(o), time.time()) for o in nouvelles]
        evaluees.sort(key=lambda x: (not x[1].ok, not x[1].prioritaire, x[0].loyer or 9e9))
        for o, verdict, t_detect in evaluees:
            try:
                self.traiter(o, verdict, t_detect)
            except Exception as e:
                self.log.exception("traitement de %s", o.id)
                self.notif.envoyer(self.p, f"🔴🔴 ⚠️ ERREUR sur l'offre{echapper(o.id)} : {echapper(str(e)[:300])}",
                                   photo=self.a.capture(f"erreur-{o.id}"))
            finally:
                if not self.m.deja_vue(o.id):
                    self.m.marquer_vue(o)
        return nouvelles

    def classer(self, o: Offre):
        return self.criteres.classer(o, self.resolveur) if self.criteres else classer(o)

    def _candidature_active(self) -> bool:
        if self.actif_cache is None:
            self.actif_cache = bool(self.a.candidature_active())
        return self.actif_cache

    def traiter(self, o: Offre, verdict, t_detect: float) -> None:
        if not verdict:
            self.log.info("rejetée %s : %s — %s", o.id, verdict.raison, o.resume())
            self.m.marquer_vue(o, "rejetee")
            self._notifier_hors_criteres(o, verdict)
            return
        try:
            self.a.enrichir(o)
        except SessionExpiree:
            raise
        except Exception as e:
            self.log.warning("fiche de %s illisible : %s", o.id, e)
        verdict = self.classer(o)     # la fiche peut préciser loyer/surface
        if not verdict:
            self.log.info("rejetée après lecture de la fiche %s : %s", o.id, verdict.raison)
            self.m.marquer_vue(o, "rejetee")
            self._notifier_hors_criteres(o, verdict)
            return
        self.log.info("CORRESPOND %s : %s — %s", o.id, verdict.raison, o.resume())
        prio = " ⭐ prioritaire" if verdict.prioritaire else ""
        lien = f'<a href="{echapper(o.url)}">{echapper(o.url)}</a>' if o.url else "(lien inconnu)"
        decision = decider(o, verdict, self._candidature_active() if self.p != "action_logement" else False, self.p)

        if decision.action == ALERTER:
            texte = (f"🔴🔴 🚨 NOUVELLE OFFRE — À TOI DE POSTULER{prio} 🚨\n"
                     f"<b>{echapper(o.ville)} {echapper(o.code_postal)}</b>\n"
                     f"{echapper(o.resume())}\n{echapper(o.titre)}\nDétectée à {_h(t_detect)}\n{lien}\n"
                     f"Pas de candidature auto : <b>{echapper(decision.raison)}</b>\n"
                     f"<i>Les dossiers complets sont classés par heure de dépôt.</i>")
            photo = self.a.capture_offre(o)
            if self.alertes:
                self.alertes.nouvelle(self.p, texte, photo)
            else:
                self.notif.envoyer(self.p, texte, photo=photo)
            self.m.marquer_vue(o, "alertee")
            return

        if decision.action == CANDIDATER:
            # re-vérification juste avant l'envoi (nombre de candidats, candidature active)
            motif_txt, source_motif = None, None
            if self.a.besoin_motif:
                if not self.motifs:
                    raise RuntimeError("motif requis mais aucun générateur configuré")
                mo = self.motifs.generer(o, self.a.motif_max or 1000)
                motif_txt, source_motif = mo.texte, mo.source
                self._sauver_motif(o, mo)
            nb = self.a.nombre_candidats(o)
            if nb is not None:
                o.nb_candidats = nb
            self.actif_cache = None
            decision = decider(o, verdict, self._candidature_active(), self.p)
            if decision.action == CANDIDATER:
                res = self.a.candidater(o, motif_txt, self.dry_run)
                t_cand = time.time()
                if res.ok:
                    self.actif_cache = True    # une seule candidature à la fois
                    self.m.noter_candidature(o, self.dry_run, {"t_detection": _h(t_detect),
                                                               "delai_s": round(t_cand - t_detect, 1)})
                    self.m.marquer_vue(o, "candidature_dry_run" if self.dry_run else "candidature")
                    tag = "🧪 DRY-RUN (rien envoyé) — " if self.dry_run else ""
                    txt = (f"🔴🔴 ✅ {tag}CANDIDATURE {'PRÉPARÉE' if self.dry_run else 'ENVOYÉE'}{prio}\n"
                           f"{echapper(o.resume())}\n{lien}\n"
                           f"Détection {_h(t_detect)} → candidature {_h(t_cand)} "
                           f"(<b>{t_cand - t_detect:.1f} s</b>)\n{echapper(res.message)}")
                    if motif_txt:
                        txt += f"\n\n<b>Motif ({source_motif}) :</b>\n{echapper(motif_txt)}"
                    self.notif.envoyer(self.p, txt, photo=res.capture)
                else:
                    self.m.marquer_vue(o, "echec")
                    self.notif.envoyer(self.p, f"🔴🔴 ⚠️ ÉCHEC DE CANDIDATURE :{echapper(res.message)}\n"
                                               f"{echapper(o.resume())}\n{lien}", photo=res.capture)
                return

        if decision.action == NOTIFIER:
            self.notif.envoyer(self.p, f"🔴🔴 🏠 OFFRE CORRESPONDANTE NON CANDIDATÉE{prio} : "
                                       f"<b>{echapper(decision.raison)}</b>\n{echapper(o.resume())}\n"
                                       f"Détectée à {_h(t_detect)}\n{lien}", photo=self.a.capture_offre(o))
            self.m.marquer_vue(o, "notifiee")
        elif decision.action == IGNORER:
            self.m.marquer_vue(o, "rejetee")

    def _notifier_hors_criteres(self, o: Offre, verdict) -> None:
        """Message simple (sans 🔴) pour toute nouvelle offre hors critères (option NOTIF_HORS_CRITERES)."""
        if not self.notif_hors_criteres:
            return
        lien = f'\n<a href="{echapper(o.url)}">voir l\'offre</a>' if o.url else ""
        self.notif.envoyer(self.p, f"ℹ️ Nouvelle offre hors critères — <i>{echapper(verdict.raison)}</i>\n"
                                   f"{echapper(o.resume())}{lien}")

    def _sauver_motif(self, o: Offre, mo) -> None:
        d = self.a.dossier / "motifs"
        d.mkdir(exist_ok=True)
        nom = f"{time.strftime('%Y%m%d-%H%M%S')}-{''.join(c if c.isalnum() else '_' for c in o.id)}.txt"
        (d / nom).write_text(f"# {o.resume()}\n# {o.url}\n# source : {mo.source} {mo.raison_secours}\n\n{mo.texte}\n",
                             encoding="utf-8")

    # ------------------------------------------------------------------
    def boucle(self, arret: threading.Event, une_fois: bool = False) -> None:
        self.log.info("démarrage (dry_run=%s, intervalle=%ss)", self.dry_run, self.intervalle)
        while not arret.is_set():
            if self.pause.is_set():
                arret.wait(5)
                continue
            debut = time.time()
            try:
                if self.limiteur:
                    with self.limiteur:
                        try:
                            self.passage()
                        except Exception:
                            self._capture_echec = self.a.capture("ko")   # avant de fermer le navigateur
                            raise
                        finally:
                            if self.fermer_apres:
                                self.a.fermer()
                                self.connecte = False
                else:
                    self.passage()
                self.derniere_activite = time.time()
                if self.echecs >= ECHECS_AVANT_KO:
                    self.notif.envoyer(self.p, "✅ Agent rétabli, la surveillance reprend.")
                self.echecs, self.ko_signale_a = 0, 0.0
                self.m.noter_erreur(None)
            except ConnexionImpossible as e:
                self._echec(e, fatal=True)
            except Exception as e:
                self._echec(e)
            self.derniere_activite = time.time()  # un passage terminé, même en échec : le fil n'est pas bloqué
            if une_fois:
                break
            # intervalle ± 20 % (pas de rafales régulières), plus long après des échecs
            attente = min(self.intervalle * random.uniform(0.8, 1.2) * (1 + min(self.echecs, 2)), 1800)
            # attente en temps réel (horloge murale) : après une veille, la lecture repart dès le réveil ;
            # `forcer` (réveil programmé) déclenche une lecture immédiate
            fin = debut + max(5.0, attente)
            while not arret.is_set() and not self.forcer.is_set() and time.time() < fin:
                arret.wait(2)
            self.forcer.clear()
        self.a.fermer()

    def _echec(self, e: Exception, fatal: bool = False) -> None:
        self.echecs += 1
        self.connecte = False
        msg = f"{type(e).__name__}: {e}"
        self.log.error("passage en échec (%d) : %s", self.echecs, msg)
        self.log.debug(traceback.format_exc())
        self.m.noter_erreur(msg[:500])
        capture = getattr(self, "_capture_echec", None) or self.a.capture("ko")
        self._capture_echec = None
        try:
            self.a.fermer()
        except Exception:
            pass
        now = time.time()
        if isinstance(e, OSError) and e.errno == 28:      # disque plein : l'état ne peut plus être enregistré
            fatal = True
            msg = "💾 DISQUE C: PLEIN — l'agent ne peut plus enregistrer ce qu'il voit. Libère de la place " \
                  "(Paramètres → Système → Stockage → Fichiers temporaires)."
        if (fatal or self.echecs >= ECHECS_AVANT_KO) and now - self.ko_signale_a > RAPPEL_KO_S:
            self.ko_signale_a = now
            self.notif.envoyer(self.p, f"🔴🔴 🛑 AGENT KO ({self.echecs} échecs) : {echapper(msg[:400])}"
                                       + ("\n➡️ Intervention requise (connexion manuelle ?)." if fatal else ""),
                               photo=capture)
