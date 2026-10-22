"""Ouvre un navigateur visible (profil persistant data/<plateforme>/navigateur)
avec CDP sur le port 9222, et journalise les réponses JSON/XHR dans
data/<plateforme>/exploration_reseau.jsonl (brut, non commité).
Usage : python outils/navigateur_exploration.py aljt|locannonces|action_logement
"""
import json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

URLS = {
    "aljt": "https://offres.passlogement.com/account",
    "locannonces": "https://teleservices.paris.fr/locannonces/",
    "action_logement": "https://logement-actionlogement.fr/",
}
p = sys.argv[1]
racine = Path(__file__).resolve().parent.parent
prof = racine / "data" / p / "navigateur"
prof.mkdir(parents=True, exist_ok=True)
log = (racine / "data" / p / "exploration_reseau.jsonl").open("a", encoding="utf-8")

def on_resp(r):
    try:
        rt = r.request.resource_type
        if rt not in ("xhr", "fetch", "document"):
            return
        ct = r.headers.get("content-type", "")
        corps = None
        if "json" in ct:
            try:
                corps = r.text()[:200000]
            except Exception:
                pass
        log.write(json.dumps({"t": time.time(), "m": r.request.method, "url": r.url,
                              "st": r.status, "ct": ct, "rt": rt,
                              "post": (r.request.post_data or "")[:2000] if r.request.method != "GET" else None,
                              "corps": corps}, ensure_ascii=False) + "\n")
        log.flush()
    except Exception:
        pass

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        str(prof), headless=False, channel="chrome", locale="fr-FR", viewport={"width": 1366, "height": 900},
        args=["--remote-debugging-port=9222"])
    ctx.on("response", on_resp)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(URLS[p])
    print("PRET", flush=True)
    while ctx.pages:
        ctx.pages[0].wait_for_timeout(1000)  # pompe les événements (time.sleep les bloquerait)
