"""Agent logement multi-personnes, multi-plateformes — point d'entrée.

  python main.py                     # agent + interface web (http://127.0.0.1:8080)
  python main.py --dry-run --debug   # force le mode test partout
  python main.py --once              # un seul passage par personne et plateforme
  python main.py --connexion ismail:alin   # connexion MANUELLE (captcha, code…) pour une personne

Les personnes, leurs critères et leurs plateformes se règlent dans l'interface web (data/personnes.json) ;
les mots de passe sont chiffrés à part (DPAPI). .env ne garde que les réglages globaux et le bot Telegram.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime

from coeur import assistant, eveil, journal
from coeur.alertes import GestionAlertes
from coeur.config import DATA, Config
from coeur.criteres import Criteres, ResolveurCP
from coeur.memoire import Memoire
from coeur.moteur import Moteur
from coeur.motif import GenerateurMotif
from coeur.personnes import PLATEFORMES, Annuaire, Personne
from coeur.telegram import Notifieur, NotifieurFactice, Telegram, echapper
from plateformes import ADAPTATEURS

log = logging.getLogger("agent")
NOMS = PLATEFORMES
NAVIGATEURS_SIMULTANES = 3


class NotifieurPersonne(Notifieur):
    """Envoie au Telegram de la personne ; tant qu'il n'est pas relié, à l'administrateur avec son nom."""

    def __init__(self, base: Notifieur, personne: Personne, admin: Notifieur):
        self.base, self.personne, self.admin = base, personne, admin

    def _cible(self):
        return self.base if self.personne.telegram_chat_id else self.admin

    def envoyer(self, plateforme, texte, photo=None, boutons=None):
        if not self.personne.telegram_chat_id:
            texte = f"👤 <b>{echapper(self.personne.nom)}</b> (Telegram non relié)\n{texte}"
        return self._cible().envoyer(plateforme, texte, photo=photo, boutons=boutons)

    def retirer_boutons(self, message_id):
        self._cible().retirer_boutons(message_id)


class Espace:
    """Tout ce qui concerne une personne : moteurs, alertes, notifications, pause."""

    def __init__(self, agent: "Agent", personne: Personne):
        self.agent, self.personne = agent, personne
        cfg, args = agent.cfg, agent.args
        self.pause = threading.Event()
        if args.telegram_factice:
            base = NotifieurFactice()
        else:
            base = Telegram(cfg.telegram_token, personne.telegram_chat_id or cfg.telegram_chat_id)
        self.notif = NotifieurPersonne(base, personne, agent.notif_admin)
        self.alertes = GestionAlertes(self.notif, personne.dossier / "alertes.json", cfg.al_relance_s, cfg.al_relances_max)
        self.moteurs: dict[str, Moteur] = {}
        nb_total = sum(1 for p in agent.annuaire.charger() if p.actif for r in p.plateformes.values() if r.actif)
        for p, reglage in personne.plateformes.items():
            if not reglage.actif or p not in ADAPTATEURS:
                continue
            if args.plateformes and p not in args.plateformes.split(","):
                continue
            dossier = personne.dossier / p
            identifiants = (reglage.identifiant, personne.coffre.lire(p) if reglage.identifiant else "")
            a = ADAPTATEURS[p](dossier, identifiants=identifiants,
                               headless=cfg.headless and cfg.headless_par.get(p, True) and not args.visible,
                               canal=cfg.canal_navigateur, debug=args.debug)
            a.connexion_auto = not args.sans_connexion_auto
            a.reduire = not args.visible
            m = Moteur(a, Memoire(dossier, cfg.seuil_anomalie), self.notif,
                       dry_run=args.dry_run or not reglage.production, intervalle=cfg.intervalles.get(p, 300),
                       motifs=GenerateurMotif(cfg.ollama_url, cfg.ollama_modele, cfg.llm_timeout,
                                              profil=personne.dossier / "profil_candidat.md")
                       if p == "locannonces" else None,
                       alertes=self.alertes if p == "action_logement" else None, pause=self.pause)
            m.criteres = Criteres.depuis_personne(personne, p)
            m.resolveur = agent.resolveur
            m.notif_hors_criteres = personne.notif_hors_criteres
            m.limiteur = agent.limiteur
            # Les navigateurs invisibles sont fermés entre deux lectures (mémoire) ; les fenêtres visibles
            # (Action Logement) restent ouvertes, sinon elles s'ouvrent et se ferment toutes les 5 min.
            m.fermer_apres = nb_total > NAVIGATEURS_SIMULTANES and a.headless
            m.etiquette = personne.nom
            m.log = logging.getLogger(f"moteur.{personne.id}.{p}")
            self.moteurs[p] = m

    # ---------------------------------------------------------------- Telegram
    def statut(self) -> str:
        etat_pause = "⏸️ EN PAUSE" if self.pause.is_set() else "▶️ actif"
        lignes = [f"<b>Agent logement — {echapper(self.personne.nom)}</b> — {etat_pause}"]
        if not self.moteurs:
            lignes.append("Aucune plateforme active : règle-les dans l'interface (http://127.0.0.1:8080).")
        for p, m in self.moteurs.items():
            e = m.m.etat
            cands = [c for c in e["candidatures"] if not c.get("dry_run")]
            derniere = cands[-1] if cands else None
            lignes.append(
                f"\n<b>{NOMS.get(p, p)}</b> ({'DRY-RUN' if m.dry_run else 'PRODUCTION'})\n"
                f"• dernière lecture : {e.get('derniere_lecture') or 'jamais'}\n"
                f"• offres connues : {len(e['vues'])}\n"
                f"• mes candidatures : {echapper(m.etat_candidatures)}"
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
            return "⏸️ Ta surveillance est en pause (/reprise pour relancer)."
        if cmd == "/reprise":
            self.pause.clear()
            return "▶️ Ta surveillance reprend."
        return ("Commandes : /statut, /test, /pause, /reprise.\n"
                "Tu peux aussi m'écrire normalement, par ex. « qu'est-ce que tu as fait aujourd'hui ? ».")

    def question(self, texte: str) -> str:
        return assistant.repondre(texte, self.statut(), self.moteurs, DATA / "agent.log",
                                  self.agent.cfg.ollama_url, self.agent.cfg.ollama_modele, timeout=90)

    def callback(self, data: str) -> str:
        if data.startswith("vu:"):
            return "✅ Noté, plus de relance." if self.alertes.marquer_vu(data[3:]) else "Déjà noté."
        return ""

    def notifs_de_test(self) -> None:
        t = "🧪 <b>TEST</b> — exemple, aucun vrai logement\n"
        self.notif.envoyer("aljt", "🔴🔴 ✅ CANDIDATURE ENVOYÉE\n" + t + "Montreuil (93100) · 780 € CC · 45 m² · T2")
        self.notif.envoyer("locannonces", "🔴🔴 🏠 OFFRE CORRESPONDANTE NON CANDIDATÉE : <b>déjà 3 candidatures</b>\n"
                           + t + "Paris (75020) · 840 € CC · 42 m² · T2")
        self.alertes.nouvelle("action_logement", "🔴🔴 🚨 NOUVELLE OFFRE — À TOI DE POSTULER 🚨\n" + t +
                              "Pantin (93500) · 760 € CC · 48 m² · T2\nAppuie sur « ✅ Vu » pour arrêter les relances.")
        self.notif.envoyer(None, "ℹ️ Nouvelle offre hors critères — <i>loyer 955 &gt; 880</i>\n" + t)


class Agent:
    def __init__(self, cfg: Config, args, annuaire: Annuaire | None = None):
        self.cfg, self.args = cfg, args
        self.arret = threading.Event()
        self.annuaire = annuaire or Annuaire()
        self.resolveur = ResolveurCP(DATA / "cache_communes.json")
        self.limiteur = threading.Semaphore(NAVIGATEURS_SIMULTANES)
        self.bot = Telegram(cfg.telegram_token, cfg.telegram_chat_id) if not args.telegram_factice else NotifieurFactice()
        self.notif_admin = self.bot
        self.battement = eveil.Battement(DATA / "battement.txt")
        self.espaces: dict[str, Espace] = {}
        for personne in self.annuaire.charger():
            if personne.actif and (not args.personne or personne.id == args.personne):
                self.espaces[personne.id] = Espace(self, personne)
        self._mtime_annuaire = self._mtime()

    def tous_moteurs(self):
        for esp in self.espaces.values():
            for p, m in esp.moteurs.items():
                yield esp, p, m

    def _mtime(self) -> float:
        try:
            return self.annuaire.fichier.stat().st_mtime
        except OSError:
            return 0.0

    # ---------------------------------------------------------------- Telegram (un seul bot, aiguillé par chat)
    def recevoir(self, chat_id: str, genre: str, contenu: str) -> str | None:
        personne = self.annuaire.par_chat(chat_id)
        if genre == "commande" and contenu.startswith("/lier"):
            code = contenu.split(maxsplit=1)[1] if " " in contenu else ""
            p = self.annuaire.lier_telegram(code, chat_id)
            if not p:
                return "Code inconnu. Ouvre l'interface de l'agent, fiche de ta personne, et recopie le code « /lier XXXXXX »."
            return (f"✅ Telegram relié à <b>{echapper(p.nom)}</b>. L'agent redémarre dans quelques secondes "
                    "pour t'envoyer tes notifications ici.")
        if not personne:
            if str(chat_id) == str(self.cfg.telegram_chat_id):     # administrateur sans profil relié
                return "\n\n".join(e.statut() for e in self.espaces.values()) or "Aucune personne configurée."
            return ("Bonjour ! Ce bot sert à l'agent logement. Pour recevoir tes notifications, demande à "
                    "l'administrateur ton code, puis envoie « /lier CODE ».")
        # l'administrateur (TELEGRAM_CHAT_ID) voit toutes les personnes dans /statut, même s'il a sa propre fiche
        if (genre == "commande" and str(chat_id) == str(self.cfg.telegram_chat_id)
                and contenu.split()[0].split("@")[0].lower() in ("/statut", "/status", "/tous")):
            return "\n\n━━━━━━━━━━\n\n".join(e.statut() for e in self.espaces.values()) or "Aucune personne configurée."
        esp = self.espaces.get(personne.id)
        if not esp:
            return "Ton profil est inactif ou en cours de rechargement. Réessaie dans une minute."
        if genre == "commande":
            return esp.commande(contenu.split()[0].split("@")[0].lower())
        if genre == "callback":
            return esp.callback(contenu)
        return esp.question(contenu)

    # ---------------------------------------------------------------- boucles communes
    def _relances(self):
        while not self.arret.wait(10):
            for esp in list(self.espaces.values()):
                try:
                    esp.alertes.tick()
                except Exception:
                    log.exception("relances AL (%s)", esp.personne.id)

    def _veille(self):
        avant, secteur_avant = time.time(), None
        while True:
            secteur = eveil.sur_secteur()
            eveil.maintenir_eveil(secteur and self.cfg.garder_eveil)
            if secteur and secteur_avant is False:
                self.notif_admin.envoyer(None, "🔌 PC rebranché, surveillance continue.")
            if not secteur and time.time() - getattr(self, "_alerte_batterie", 0) > 1800:
                # au démarrage, au débranchement, puis toutes les 30 min tant que le PC reste sur batterie
                self._alerte_batterie = time.time()
                self.notif_admin.envoyer(None, "🔴🔴 🔋 PC sur BATTERIE : il va se mettre en veille puis en veille "
                                               "prolongée, et l'agent ne verra plus rien. Rebranche-le.")
            secteur_avant = secteur
            self.battement.battre()
            self._verifier_reveil()
            self._chien_de_garde()
            self._surveiller_annuaire()
            if self.arret.wait(5):
                break
            now = time.time()
            if now - avant > 120:
                d, f = datetime.fromtimestamp(avant), datetime.fromtimestamp(now)
                self.notif_admin.envoyer(None, f"😴 La machine était en veille (ou figée) de {d:%H:%M} à {f:%H:%M} "
                                               f"— {int((now - avant) / 60)} min sans surveillance. Reprise en cours.")
            avant = now
        eveil.maintenir_eveil(False)

    def _surveiller_annuaire(self):
        """Personnes modifiées dans l'interface : on relance proprement l'agent pour tout recharger."""
        if self._mtime() != self._mtime_annuaire and not self.args.once:
            log.info("personnes modifiées dans l'interface : redémarrage de l'agent")
            time.sleep(2)
            os._exit(4)

    def _chien_de_garde(self):
        maintenant = time.time()
        for esp, p, m in self.tous_moteurs():
            if esp.pause.is_set() or m.arrete:      # arrêté exprès (identifiants refusés) : pas un blocage
                continue
            # avec le limiteur, une lecture peut attendre son tour : marge proportionnelle au nombre de moteurs
            limite = max(3 * m.intervalle, 900) + 1800 + 60 * sum(1 for _ in self.tous_moteurs())
            if maintenant - m.derniere_activite > limite:
                minutes = int((maintenant - m.derniere_activite) / 60)
                log.error("[%s/%s] bloqué depuis %d min : redémarrage complet", esp.personne.id, p, minutes)
                self.notif_admin.envoyer(p, f"🔴🔴 🛑 {esp.personne.nom} / {NOMS.get(p, p)} BLOQUÉ depuis {minutes} min : "
                                            f"je redémarre l'agent.")
                time.sleep(3)
                os._exit(3)

    def _verifier_reveil(self):
        f = DATA / "reveil.flag"
        try:
            t = f.stat().st_mtime
        except OSError:
            return
        if t > getattr(self, "_reveil_vu", 0):
            if getattr(self, "_reveil_vu", 0):
                log.info("réveil programmé : lecture immédiate")
                for _, _, m in self.tous_moteurs():
                    m.forcer.set()
            self._reveil_vu = t

    def _moteur(self, esp: Espace, p: str, m: Moteur):
        while not self.arret.is_set():
            try:
                m.boucle(self.arret, une_fois=self.args.once)
                return
            except Exception:
                log.exception("[%s/%s] boucle interrompue, redémarrage dans 30 s", esp.personne.id, p)
                self.arret.wait(30)

    def lancer(self, avec_interface: bool = True) -> None:
        if avec_interface and not self.args.once:
            from interface.serveur import demarrer_interface
            demarrer_interface(self)
        resume = "; ".join(f"{e.personne.nom} : " + (", ".join(f"{NOMS.get(p, p)} {'(test)' if m.dry_run else '(PROD)'}"
                                                                 for p, m in e.moteurs.items()) or "aucune plateforme")
                           for e in self.espaces.values()) or "aucune personne configurée"
        self.notif_admin.envoyer(None, f"🤖 Agent démarré — {resume}")
        arret = eveil.Battement.message_arret(self.battement.dernier(), time.time())
        if arret and not self.args.once:
            self.notif_admin.envoyer(None, "🔴🔴 " + arret)
        fils = [threading.Thread(target=self._moteur, args=(e, p, m), name=f"{e.personne.id}/{p}", daemon=True)
                for e, p, m in self.tous_moteurs()]
        noms_moteurs = {f.name for f in fils}
        if not self.args.once:
            fils += [threading.Thread(target=self._relances, daemon=True, name="relances"),
                     threading.Thread(target=self._veille, daemon=True, name="veille")]
            if isinstance(self.bot, Telegram):
                fils.append(threading.Thread(target=self.bot.ecouter_tous, args=(self.recevoir, self.arret),
                                             daemon=True, name="telegram"))
        for f in fils:
            f.start()
        try:
            while not self.arret.is_set():
                if self.args.once and not any(f.is_alive() for f in fils if f.name in noms_moteurs):
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        self.arret.set()
        for f in fils:
            if f.name in noms_moteurs:
                f.join(timeout=20)


def connexion_manuelle(cible: str, cfg: Config) -> None:
    ident, _, p = cible.partition(":")
    personne = Annuaire().obtenir(ident)
    if not personne or p not in ADAPTATEURS:
        sys.exit("usage : --connexion <personne>:<plateforme>  (ex. ismail:alin)")
    a = ADAPTATEURS[p](personne.dossier / p, headless=False, canal=cfg.canal_navigateur)
    a.demarrer()
    a.page.goto(a.url_base + ("/account" if p == "aljt" else "/"))
    print(f"Connecte-toi dans la fenêtre ouverte ({NOMS.get(p, p)}), puis ferme-la. La session sera conservée.")
    while a.ctx.pages:
        try:
            a.ctx.pages[0].wait_for_timeout(1000)
        except Exception:
            break
    a.fermer()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Agent logement multi-personnes")
    ap.add_argument("--dry-run", action="store_true", help="aucune candidature réelle")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--once", action="store_true", help="un seul passage")
    ap.add_argument("--personne", help="limiter à une personne (identifiant)")
    ap.add_argument("--plateformes", help="limiter à ces plateformes (aljt,locannonces,action_logement,alin)")
    ap.add_argument("--visible", action="store_true", help="navigateurs visibles")
    ap.add_argument("--connexion", help="connexion manuelle : <personne>:<plateforme>")
    ap.add_argument("--sans-connexion-auto", action="store_true", help="ne jamais saisir de mot de passe")
    ap.add_argument("--sans-interface", action="store_true", help="ne pas démarrer l'interface web")
    ap.add_argument("--telegram-factice", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    cfg = Config.charger()
    journal.configurer(args.debug)
    if args.connexion:
        connexion_manuelle(args.connexion, cfg)
        return 0
    agent = Agent(cfg, args)
    signal.signal(signal.SIGTERM, lambda *_: agent.arret.set())
    agent.lancer(avec_interface=not args.sans_interface)
    return 0


if __name__ == "__main__":
    sys.exit(main())
