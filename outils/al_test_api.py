"""Test lecture seule de l'API AL depuis la session ouverte (CDP)."""
import json, sys
from playwright.sync_api import sync_playwright
API = "https://api.logement-actionlogement.fr/api/v1"
corps = json.load(open(sys.argv[1], encoding="utf-8")) if len(sys.argv) > 1 else None
with sync_playwright() as pw:
    b = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
    ctx = b.contexts[0]; page = ctx.pages[-1]
    jeton = {}
    def on_req(r):
        a = r.headers.get("authorization")
        if a and "api.logement-actionlogement.fr" in r.url:
            jeton["a"] = a
    page.on("request", on_req)
    page.reload(); page.wait_for_timeout(6000)
    print("jeton capturé :", bool(jeton))
    if corps is not None:
        res = page.evaluate("""async ([a, c, api]) => { const r = await fetch(api + '/demands/offers-overview?size=50&page=0',
            {method:'POST', headers:{'Authorization': a, 'Content-Type':'application/json', 'Accept':'application/json'}, body: JSON.stringify(c)});
            return [r.status, await r.text()]; }""", [jeton["a"], corps, API])
        print(res[0]); d = json.loads(res[1])
        print({k: v for k, v in d.items() if k != "data"})
        for o in d.get("data") or []:
            l = o["lodging"]; print(o["id"], l["municipality"]["name"], l["municipality"]["postcode"], l["lodgingTypology"]["code"], l["area"], o["totalRentAmountBaseBound"], o["startDateOfPublication"], o.get("offerCategory"), (o.get("candidacy") or {}).get("statusCode"))
    sys.stdout.flush()
