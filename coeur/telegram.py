"""Notifications Telegram + commandes (/statut, /pause, /reprise) + bouton « ✅ Vu ».

Toutes les notifications sont sonores (disable_notification=false).
"""
from __future__ import annotations

import html
import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import requests

log = logging.getLogger("telegram")

PREFIXES = {"aljt": "[ALJT]", "locannonces": "[Paris]", "action_logement": "[AL]", None: "[Agent]"}
LIMITE_TEXTE = 4096
LIMITE_LEGENDE = 1024


def echapper(t: str) -> str:
    return html.escape(str(t or ""), quote=False)


class Notifieur:
    """Interface commune ; la version réelle parle à l'API Bot, la factice enregistre."""

    def envoyer(self, plateforme: Optional[str], texte: str, photo: Optional[Path] = None,
                boutons: Optional[list[tuple[str, str]]] = None) -> Optional[int]:
        raise NotImplementedError

    def retirer_boutons(self, message_id: int) -> None:
        pass


class NotifieurFactice(Notifieur):
    def __init__(self):
        self.messages: list[dict] = []
        self._n = 0
        self._verrou = threading.Lock()

    def envoyer(self, plateforme, texte, photo=None, boutons=None):
        with self._verrou:
            self._n += 1
            self.messages.append({"id": self._n, "plateforme": plateforme,
                                  "texte": f"{PREFIXES.get(plateforme)} {texte}",
                                  "photo": str(photo) if photo else None, "boutons": boutons})
            return self._n

    def retirer_boutons(self, message_id):
        self.messages.append({"retire": message_id})

    def textes(self) -> list[str]:
        return [m["texte"] for m in self.messages if "texte" in m]


class Telegram(Notifieur):
    def __init__(self, token: str, chat_id: str, api: str = "https://api.telegram.org"):
        self.token, self.chat_id = token, str(chat_id)
        self.base = f"{api}/bot{token}"
        self.session = requests.Session()
        self._verrou = threading.Lock()
        self.actif = bool(token and chat_id)
        if not self.actif:
            log.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID absents : notifications seulement dans le journal")

    def _post(self, methode: str, data: dict, files=None, essais: int = 3):
        for i in range(essais):
            try:
                if files:
                    for f in files.values():
                        f.seek(0)
                with self._verrou:
                    r = self.session.post(f"{self.base}/{methode}", data=data, files=files, timeout=30)
                j = r.json()
                if j.get("ok"):
                    return j.get("result")
                if r.status_code == 429:
                    time.sleep(int(j.get("parameters", {}).get("retry_after", 3)))
                    continue
                log.error("Telegram %s : %s", methode, j.get("description"))
                return None
            except (requests.RequestException, ValueError) as e:
                log.warning("Telegram %s indisponible (%s), essai %d", methode, type(e).__name__, i + 1)
                time.sleep(2 * (i + 1))
        return None

    def envoyer(self, plateforme, texte, photo=None, boutons=None):
        texte = f"<b>{PREFIXES.get(plateforme)}</b> {texte}"
        log.info("→ Telegram : %s", texte.replace("\n", " | ")[:400])
        if not self.actif:
            return None
        data = {"chat_id": self.chat_id, "parse_mode": "HTML", "disable_notification": "false"}
        if boutons:
            data["reply_markup"] = json.dumps(
                {"inline_keyboard": [[{"text": t, "callback_data": c} for t, c in boutons]]})
        if photo and Path(photo).exists():
            with open(photo, "rb") as f:
                if len(texte) <= LIMITE_LEGENDE:
                    res = self._post("sendPhoto", {**data, "caption": texte}, files={"photo": f})
                    if res:
                        return res["message_id"]
                else:   # légende trop longue : photo d'abord, puis le texte (qui porte les boutons)
                    self._post("sendPhoto", {"chat_id": self.chat_id, "disable_notification": "false"},
                               files={"photo": f})
        res = self._post("sendMessage", {**data, "text": texte[:LIMITE_TEXTE], "disable_web_page_preview": "true"})
        return res and res["message_id"]

    def retirer_boutons(self, message_id):
        if self.actif and message_id:
            self._post("editMessageReplyMarkup", {"chat_id": self.chat_id, "message_id": message_id,
                                                  "reply_markup": json.dumps({"inline_keyboard": []})}, essais=1)

    def _repondre(self, sur_question: Callable[[str], str], txt: str) -> None:
        try:
            rep = sur_question(txt)
        except Exception as e:
            log.exception("question Telegram")
            rep = f"Je n'ai pas pu répondre ({type(e).__name__}). Essaie /statut."
        for i in range(0, len(rep), LIMITE_TEXTE):      # pas de HTML : texte libre du LLM
            self._post("sendMessage", {"chat_id": self.chat_id, "text": rep[i:i + LIMITE_TEXTE]})

    def ecouter(self, sur_commande: Callable[[str], str], sur_callback: Callable[[str], str],
                arret: threading.Event, sur_question: Optional[Callable[[str], str]] = None) -> None:
        """Boucle bloquante (thread dédié). N'accepte que les messages de TELEGRAM_CHAT_ID."""
        if not self.actif:
            return
        offset = None
        while not arret.is_set():
            try:
                params = {"timeout": 25, "allowed_updates": json.dumps(["message", "callback_query"])}
                if offset:
                    params["offset"] = offset
                r = self.session.get(f"{self.base}/getUpdates", params=params, timeout=35).json()
                for u in r.get("result", []):
                    offset = u["update_id"] + 1
                    if "callback_query" in u:
                        cq = u["callback_query"]
                        if str(cq.get("message", {}).get("chat", {}).get("id")) != self.chat_id:
                            continue
                        rep = sur_callback(cq.get("data", ""))
                        self._post("answerCallbackQuery", {"callback_query_id": cq["id"], "text": rep}, essais=1)
                    elif "message" in u:
                        m = u["message"]
                        if str(m.get("chat", {}).get("id")) != self.chat_id:
                            continue
                        txt = (m.get("text") or "").strip()
                        if txt.startswith("/"):
                            rep = sur_commande(txt.split()[0].split("@")[0].lower())
                            if rep:
                                self._post("sendMessage", {"chat_id": self.chat_id, "text": rep,
                                                           "parse_mode": "HTML"})
                        elif txt and sur_question:
                            # question libre : réponse dans un fil à part (le LLM peut prendre quelques secondes)
                            self._post("sendChatAction", {"chat_id": self.chat_id, "action": "typing"}, essais=1)
                            threading.Thread(target=self._repondre, args=(sur_question, txt), daemon=True).start()
            except Exception as e:   # réseau coupé, veille…
                log.debug("getUpdates : %s", e)
                arret.wait(5)
