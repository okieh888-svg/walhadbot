"""Chargement de la configuration (.env) — aucun secret dans le code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

RACINE = Path(__file__).resolve().parent.parent
DATA = RACINE / "data"

SECRETS = ("ALJT_PASSWORD", "PARIS_PASSWORD", "AL_PASSWORD", "TELEGRAM_BOT_TOKEN",
           "ANTHROPIC_API_KEY", "ALJT_EMAIL", "PARIS_LOGIN", "AL_EMAIL")


def _bool(v: str | None, defaut: bool = False) -> bool:
    if v is None or v == "":
        return defaut
    return v.strip().lower() in ("1", "true", "oui", "yes", "on")


@dataclass
class Config:
    telegram_token: str = ""
    telegram_chat_id: str = ""
    actives: dict[str, bool] = field(default_factory=dict)
    intervalles: dict[str, int] = field(default_factory=dict)
    production: dict[str, bool] = field(default_factory=dict)   # False => dry-run forcé
    seuil_anomalie: int = 15
    headless: bool = True
    headless_par: dict[str, bool] = field(default_factory=dict)
    canal_navigateur: str = "chrome"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_modele: str = "qwen2.5:7b-instruct"
    llm_timeout: int = 40
    al_relance_s: int = 120
    al_relances_max: int = 10
    garder_eveil: bool = False
    identifiants: dict[str, tuple[str, str]] = field(default_factory=dict)

    @classmethod
    def charger(cls, fichier: Path | None = None) -> "Config":
        load_dotenv(fichier or RACINE / ".env", override=False)
        e = os.environ.get
        return cls(
            telegram_token=e("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=e("TELEGRAM_CHAT_ID", ""),
            actives={"aljt": _bool(e("ALJT_ACTIF"), True),
                     "locannonces": _bool(e("PARIS_ACTIF"), True),
                     "action_logement": _bool(e("AL_ACTIF"), True)},
            intervalles={"aljt": int(e("ALJT_INTERVALLE", "600")),
                         "locannonces": int(e("PARIS_INTERVALLE", "600")),
                         "action_logement": int(e("AL_INTERVALLE", "600"))},
            production={"aljt": _bool(e("ALJT_PRODUCTION")),
                        "locannonces": _bool(e("PARIS_PRODUCTION")),
                        "action_logement": _bool(e("AL_PRODUCTION"))},
            seuil_anomalie=int(e("SEUIL_ANOMALIE", "15")),
            headless=_bool(e("HEADLESS"), True),
            # Cloudflare bloque Chrome headless sur Action Logement : vraie fenêtre (réduite) par défaut
            headless_par={"aljt": _bool(e("ALJT_HEADLESS"), True), "locannonces": _bool(e("PARIS_HEADLESS"), True),
                          "action_logement": _bool(e("AL_HEADLESS"), False)},
            canal_navigateur=e("NAVIGATEUR_CANAL", "chrome"),
            ollama_url=e("OLLAMA_URL", "http://127.0.0.1:11434"),
            ollama_modele=e("OLLAMA_MODELE", "qwen2.5:7b-instruct"),
            llm_timeout=int(e("LLM_TIMEOUT", "40")),
            al_relance_s=int(e("AL_RELANCE_SECONDES", "120")),
            al_relances_max=int(e("AL_RELANCES_MAX", "10")),
            garder_eveil=_bool(e("GARDER_EVEIL"), False),
            identifiants={"aljt": (e("ALJT_EMAIL", ""), e("ALJT_PASSWORD", "")),
                          "locannonces": (e("PARIS_LOGIN", ""), e("PARIS_PASSWORD", "")),
                          "action_logement": (e("AL_EMAIL", ""), e("AL_PASSWORD", ""))},
        )


def valeurs_secretes() -> list[str]:
    return [v for k in SECRETS if (v := os.environ.get(k)) and len(v) >= 4]
