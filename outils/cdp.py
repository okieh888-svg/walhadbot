"""Petits outils d'inspection d'une session ouverte par navigateur_exploration.py.
python outils/cdp.py etat|texte|liens|formulaires|capture <fichier>|goto <url>|eval <js>
Ne clique jamais sur un bouton de soumission."""
import json, sys
from playwright.sync_api import sync_playwright

cmd, args = sys.argv[1], sys.argv[2:]
with sync_playwright() as pw:
    b = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
    ctx = b.contexts[0]
    page = ctx.pages[-1]
    if cmd == "etat":
        for pg in ctx.pages:
            print(pg.url, "|", pg.title())
    elif cmd == "goto":
        page.goto(args[0]); page.wait_for_load_state("networkidle", timeout=30000); print(page.url)
    elif cmd == "texte":
        print(page.inner_text("body")[: int(args[0]) if args else 8000])
    elif cmd == "liens":
        for a in page.eval_on_selector_all("a", "els=>els.map(e=>[e.innerText.trim().slice(0,60),e.href])"):
            if a[1]: print(a)
    elif cmd == "formulaires":
        print(json.dumps(page.eval_on_selector_all("input,select,textarea,button",
            "els=>els.map(e=>({tag:e.tagName,type:e.type,name:e.name,id:e.id,max:e.maxLength,txt:(e.innerText||e.value||'').trim().slice(0,50),aria:e.getAttribute('aria-label')}))"),
            ensure_ascii=False, indent=0))
    elif cmd == "capture":
        page.screenshot(path=args[0], full_page=True); print("ok")
    elif cmd == "html":
        print(page.content()[: int(args[0]) if args else 20000])
    elif cmd == "ecoute":
        # ecoute <secondes> [clic:<texte>|reload|js:<code>] ; sortie dans data/<fichier> si 3e arg
        res = []
        def on(r):
            if r.request.resource_type in ("xhr", "fetch", "document"):
                try:
                    corps = r.text() if "json" in r.headers.get("content-type", "") or "javascript" in r.headers.get("content-type", "") else ""
                except Exception:
                    corps = ""
                res.append({"m": r.request.method, "url": r.url, "st": r.status, "ct": r.headers.get("content-type", ""),
                            "post": r.request.post_data, "corps": corps})
        page.on("response", on)
        act = args[1] if len(args) > 1 else ""
        if act == "reload":
            page.reload()
        elif act.startswith("clic:"):
            page.get_by_text(act[5:], exact=True).first.click()
        elif act.startswith("js:"):
            page.evaluate(act[3:])
        page.wait_for_timeout(int(args[0]) * 1000)
        if len(args) > 2:
            open(args[2], "w", encoding="utf-8").write(json.dumps(res, ensure_ascii=False, indent=1))
        for x in res:
            print(x["m"], x["st"], x["url"][:160], "|", (x["post"] or "")[:300], "|", (x["corps"] or "")[:400].replace(chr(10), " "))
    elif cmd == "eval":
        print(json.dumps(page.evaluate(args[0]), ensure_ascii=False, indent=1)[:20000])
