"""Agent logement multi-plateformes — point d'entrée.

  python main.py                       # toutes les plateformes actives (.env), dry-run sauf *_PRODUCTION=1
  python main.py --dry-run --debug     # force le dry-run partout, journal détaillé
  python main.py --once --plateformes aljt
  python main.py --connexion aljt      # ouvre le navigateur pour une connexion MANUELLE (captcha, code…)
"""
from __future__ import annotations

import argparse
import os
import logging
import signal
import sys
import threading
import time
from datetime import datetime

from coeur import assistant, eveil, journal
from coeur.alertes import GestionAlertes
from coeur.config import DATA, Config
from coeur.memoire import Memoire
from coeur.moteur import Moteur
from coeur.motif import GenerateurMotif
from coeur.telegram import NotifieurFactice, Telegram, echapper
from plateformes import ADAPTATEURS

log = logging.getLogger("agent")
NOMS = {"aljt": "Pass Logement (ALJT)", "locannonces": "LOC'Annonces", "action_logement": "Action Logement"}


def connexion_manuelle(p: str, cfg: Config) -> None:
    a = ADAPTATEURS[p](DATA / p, headless=False, canal=cfg.canal_navigateur)
    a.demarrer()
    a.page.goto(a.url_base + ("/account" if p == "aljt" else "/"))
    print(f"Connecte-toi dans la fenêtre ouverte ({NOMS[p]}), puis ferme-la. La session sera conservée.")
    while a.ctx.pages:
        try:
            a.ctx.pages[0].wait_for_timeout(1000)
        except Exception:
            break
    a.fermer()


class Agent:
    def __init__(self, cfg: Config, args):
        self.cfg, self.args = cfg, args
        self.arret = threading.Event()
        self.pause = threading.Event()
        self.notif = Telegram(cfg.telegram_token, cfg.telegram_chat_id) if not args.telegram_factice else NotifieurFactice()
        self.alertes = GestionAlertes(self.notif, DATA / "action_logement" / "alertes.json",
                                      cfg.al_relance_s, cfg.al_relances_max)
        self.battement = eveil.Battement(DATA / "battement.txt")
        self.moteurs: dict[str, Moteur] = {}
        choix = args.plateformes.split(",") if args.plateformes else [p for p, on in cfg.actives.items() if on]
        for p in choix:
            dossier = DATA / p
            dry = args.dry_run or not cfg.production.get(p, False)
            adaptateur = ADAPTATEURS[p](dossier, identifiants=cfg.identifiants[p],
                                        headless=cfg.headless and cfg.headless_par.get(p, True) and not args.visible,
                                        canal=cfg.canal_navigateur, debug=args.debug)
            adaptateur.connexion_auto = not args.sans_connexion_auto
            adaptateur.reduire = not args.visible
            self.moteurs[p] = Moteur(
                adaptateur, Memoire(dossier, cfg.seuil_anomalie), self.notif, dry_run=dry,
                intervalle=cfg.intervalles[p],
                motifs=GenerateurMotif(cfg.ollama_url, cfg.ollama_modele, cfg.llm_timeout) if p == "locannonces" else None,
                alertes=self.alertes if p == "action_logement" else None, pause=self.pause)

    # ---------------------------------------------------------------- Telegram
    def statut(self) -> str:
        lignes = [f"<b>Agent logement</b> — {'⏸️ EN PAUSE' if self.pause.is_set() else '▶️ actif'}"]
        for p, m in self.moteurs.items():
            e = m.m.etat
            cands = [c for c in e["candidatures"] if not c.get("dry_run")]
            derniere = cands[-1] if cands else None
            active = m.etat_candidatures
            lignes.append(
                f"\n<b>{NOMS[p]}</b> ({'DRY-RUN' if m.dry_run else 'PRODUCTION'})\n"
                f"• dernière lecture : {e.get('derniere_lecture') or 'jamais'}\n"
                f"• offres connues : {len(e['vues'])}\n"
                f"• mes candidatures : {echapper(active)}"
                + (f"\n• dernière candidature agent : {echapper(derniere['resume'])} ({derniere['t']})" if derniere else "")
                + (f"\n• ⚠️ erreur : {echapper(str(e.get('derniere_erreur'))[:200])}" if e.get("derniere_erreur") else ""))
        if "action_logement" in self.moteurs:
            lignes.append(f"\nAlertes AL non vues : {len(self.alertes.en_attente())}")
        return "\n".join(lignes)

    def commande(self, cmd: str) -> str:
        if cmd in ("/statut", "/status", "/start"):
            return self.statut()
        if cmd == "/test":
            threading.Thread(target=self.notifs_de_test, daemon=True).start()
            return "🧪 Envoi d'un exemple de chaque notification (aucun site touché)…"
        if cmd == "/pause":
            self.pause.set()
            return "⏸️ Surveillance en pause (/reprise pour relancer)."
        if cmd == "/reprise":
            self.pause.clear()
            return "▶️ Surveillance reprise."
        return ("Commandes : /statut, /test, /pause, /reprise.\n"
                "Tu peux aussi m'écrire normalement, par ex. « qu'est-ce que tu as fait aujourd'hui ? » "
                "ou « pourquoi tu n'as pas postulé ? ».")

    def question(self, texte: str) -> str:
        """Question libre sur Telegram : réponse fondée sur l'activité réelle de l'agent."""
        return assistant.repondre(texte, self.statut(), self.moteurs, DATA / "agent.log",
                                  self.cfg.ollama_url, self.cfg.ollama_modele, timeout=90)

    def notifs_de_test(self) -> None:
        """Exemples factices de chaque notification (rien n'est lu ni envoyé aux sites)."""
        t = "🧪 <b>TEST</b> — exemple, aucun vrai logement\n"
        self.notif.envoyer("aljt", "🔴🔴 ✅ CANDIDATURE ENVOYÉE ⭐ prioritaire\n" + t +
                           "Montreuil (93100) · 780 € CC · 45 m² · T2 · 1 candidat(s)\n"
                           "Détection 20:00:01 → candidature 20:00:03 (<b>2.1 s</b>)")
        self.notif.envoyer("locannonces", "🔴🔴 🏠 OFFRE CORRESPONDANTE NON CANDIDATÉE : "
                           "<b>déjà 3 candidatures (≥ 3)</b>\n" + t + "Paris (75020) · 840 € CC · 42 m² · T2")
        if "action_logement" in self.moteurs:
            self.alertes.nouvelle("action_logement", "🔴🔴 🚨 NOUVELLE OFFRE — À TOI DE POSTULER 🚨\n" + t +
                                  "<b>Pantin 93500</b>\nPantin (93500) · 760 € CC · 48 m² · T2 · étage 3\n"
                                  "Appuie sur « ✅ Vu » : sinon cette alerte revient toutes les 2 minutes.")
        self.notif.envoyer(None, "🔴🔴 🛑 AGENT KO (exemple)\n" + t + "Voilà à quoi ressemble un message d'erreur.")

    def callback(self, data: str) -> str:
        if data.startswith("vu:"):
            return "✅ Noté, plus de relance." if self.alertes.marquer_vu(data[3:]) else "Déjà noté."
        return ""

    # ---------------------------------------------------------------- boucles
    def _relances(self):
        while not self.arret.wait(10):
            try:
                self.alertes.tick()
            except Exception:
                log.exception("relances AL")

    def _veille(self):
        """Garde le PC éveillé sur secteur, détecte une mise en veille (saut d'horloge) et écrit le battement."""
        avant = time.time()
        secteur_avant = None
        while True:
            secteur = eveil.sur_secteur()
            eveil.maintenir_eveil(secteur and self.cfg.garder_eveil)   # option GARDER_EVEIL=1
            if secteur_avant is not None and secteur != secteur_avant:
                self.notif.envoyer(None, "🔌 PC rebranché : réveils automatiques toutes les 10 min actifs." if secteur else
                                   "🔴🔴 🔋 PC sur BATTERIE : en veille il ne se réveillera plus tout seul, "
                                   "l'agent ne verra plus rien. Rebranche-le.")
            secteur_avant = secteur
            self.battement.battre()
            self._verifier_reveil()
            self._chien_de_garde()
            if self.arret.wait(5):
                break
            now = time.time()
            if now - avant > 120:
                debut, fin = datetime.fromtimestamp(avant), datetime.fromtimestamp(now)
                self.notif.envoyer(None, f"😴 La machine était en veille (ou figée) de {debut:%H:%M} à {fin:%H:%M} "
                                         f"— {int((now - avant) / 60)} min sans surveillance. Reprise en cours.")
            avant = now
        eveil.maintenir_eveil(False)

    def _chien_de_garde(self):
        """Un fil de plateforme bloqué (appel navigateur sans fin) ne lit plus rien sans le dire :
        au-delà de 3 intervalles sans passage terminé, on prévient et on relance tout l'agent."""
        maintenant = time.time()
        for p, m in self.moteurs.items():
            if self.pause.is_set():
                return
            limite = max(3 * m.intervalle, 900) + 1800      # marge pour les relances après échecs
            if maintenant - m.derniere_activite > limite:
                minutes = int((maintenant - m.derniere_activite) / 60)
                log.error("[%s] bloqué depuis %d min : redémarrage complet de l'agent", p, minutes)
                self.notif.envoyer(p, f"🔴🔴 🛑 {NOMS[p]} BLOQUÉ depuis {minutes} min sans lecture : "
                                      f"je redémarre l'agent automatiquement.")
                time.sleep(3)
                os._exit(3)     # le superviseur (lancer_agent.ps1) relance l'agent 30 s plus tard

    def _verifier_reveil(self):
        """La tâche « AgentLogementReveil » sort le PC de veille et touche data/reveil.flag : lecture immédiate."""
        f = DATA / "reveil.flag"
        try:
            t = f.stat().st_mtime
        except OSError:
            return
        if t > getattr(self, "_reveil_vu", 0):
            if getattr(self, "_reveil_vu", 0):
                log.info("réveil programmé : lecture immédiate des plateformes")
                for m in self.moteurs.values():
                    m.forcer.set()
            self._reveil_vu = t

    def _plateforme(self, p: str, m: Moteur):
        while not self.arret.is_set():
            try:
                m.boucle(self.arret, une_fois=self.args.once)
                return
            except Exception:
                log.exception("[%s] boucle interrompue, redémarrage dans 30 s", p)
                self.arret.wait(30)

    def lancer(self) -> None:
        modes = ", ".join(f"{NOMS[p]} {'(dry-run)' if m.dry_run else '(PRODUCTION)'}" for p, m in self.moteurs.items())
        self.notif.envoyer(None, f"🤖 Agent démarré : {modes}")
        arret = eveil.Battement.message_arret(self.battement.dernier(), time.time())
        if arret and not self.args.once:
            self.notif.envoyer(None, "🔴🔴 " + arret)
        fils = [threading.Thread(target=self._plateforme, args=(p, m), name=p, daemon=True)
                for p, m in self.moteurs.items()]
        if not self.args.once:
            fils += [threading.Thread(target=self._relances, daemon=True, name="relances"),
                     threading.Thread(target=self._veille, daemon=True, name="veille")]
            if isinstance(self.notif, Telegram):
                fils.append(threading.Thread(target=self.notif.ecouter, args=(self.commande, self.callback, self.arret, self.question),
                                             daemon=True, name="telegram"))
        for f in fils:
            f.start()
        try:
            while any(f.is_alive() for f in fils if f.name in self.moteurs):
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        self.arret.set()
        for f in fils:
            if f.name in self.moteurs:
                f.join(timeout=20)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Agent logement (ALJT, LOC'Annonces, Action Logement)")
    ap.add_argument("--dry-run", action="store_true", help="aucune candidature réelle, quel que soit .env")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--once", action="store_true", help="un seul passage par plateforme")
    ap.add_argument("--plateformes", help="liste séparée par des virgules (aljt,locannonces,action_logement)")
    ap.add_argument("--visible", action="store_true", help="navigateur visible")
    ap.add_argument("--connexion", choices=list(ADAPTATEURS), help="connexion manuelle puis sortie")
    ap.add_argument("--sans-connexion-auto", action="store_true",
                    help="ne jamais saisir les identifiants de .env (session existante ou --connexion)")
    ap.add_argument("--telegram-factice", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    cfg = Config.charger()
    journal.configurer(args.debug)
    if args.connexion:
        connexion_manuelle(args.connexion, cfg)
        return 0
    agent = Agent(cfg, args)
    signal.signal(signal.SIGTERM, lambda *_: agent.arret.set())
    agent.lancer()
    return 0


if __name__ == "__main__":
    sys.exit(main())
