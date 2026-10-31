"""Interface web locale : http://127.0.0.1:8080 (accessible uniquement depuis ce PC).

Ajouter / modifier des personnes, leurs plateformes (identifiant + mot de passe chiffré), leurs critères
(codes postaux, pièces, surface, loyer max), relier leur Telegram, voir l'état de leur agent.
Toute modification enregistrée relance l'agent (≈ 10 s) pour qu'il prenne en compte les changements.
"""
from __future__ import annotations

import json
import logging
import secrets
import shutil
import threading
from pathlib import Path

from flask import Flask, abort, redirect, render_template_string, request, url_for

from coeur.personnes import (PLATEFORMES, Annuaire, Personne, ReglagePlateforme, Zone, codes_depuis_texte,
                             identifiant_depuis_nom, nouveau_code)

log = logging.getLogger("interface")
HOTE, PORT = "127.0.0.1", 8080
JETON = secrets.token_urlsafe(24)           # anti-CSRF : change à chaque démarrage
NB_ZONES = 6

NOTES_PLATEFORMES = {
    "aljt": "Réservé aux résidents ALJT. Candidature auto si < 5 candidats et aucune en cours.",
    "locannonces": "Ville de Paris (compte Mon Paris). Candidature auto avec lettre si 0 à 2 candidats et aucune en cours.",
    "action_logement": "Candidatures NON annulables (5 max) : l'agent en garde toujours une pour la personne.",
    "alin": "Une seule candidature à la fois (annulable avant transmission au bailleur).",
}

CSS = """
:root{--fond:#f6f7f9;--carte:#fff;--texte:#1d2330;--doux:#5d6677;--ligne:#e3e6ec;--accent:#c62828;--ok:#2e7d32;--avert:#b26a00}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--fond);color:var(--texte)}
header{background:#1d2330;color:#fff;padding:14px 24px;display:flex;align-items:center;gap:16px}
header a{color:#fff;text-decoration:none}header h1{font-size:18px;margin:0}
main{max-width:980px;margin:0 auto;padding:24px 16px}
.carte{background:var(--carte);border:1px solid var(--ligne);border-radius:10px;padding:18px 20px;margin-bottom:16px}
.carte h2{margin:0 0 6px;font-size:18px}.doux{color:var(--doux);font-size:13px}
.ligne{display:flex;flex-wrap:wrap;gap:12px;align-items:center}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--ligne);vertical-align:top}
.pastille{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:600}
.prod{background:#fde7e7;color:var(--accent)}.test{background:#eef1f6;color:var(--doux)}.okp{background:#e6f4ea;color:var(--ok)}.av{background:#fff3e0;color:var(--avert)}
label{display:block;font-weight:600;margin:12px 0 4px}input[type=text],input[type=number],input[type=password],input[type=email],textarea{width:100%;padding:8px 10px;border:1px solid #c9ced8;border-radius:7px;font:inherit}
textarea{min-height:120px}.grille{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.bouton{display:inline-block;background:#1d2330;color:#fff;border:0;border-radius:7px;padding:9px 16px;font:inherit;font-weight:600;cursor:pointer;text-decoration:none}
.bouton.second{background:#fff;color:var(--texte);border:1px solid #c9ced8}.bouton.danger{background:var(--accent)}
.plate{border:1px solid var(--ligne);border-radius:8px;padding:12px 14px;margin:10px 0}
.case{display:inline-flex;gap:6px;align-items:center;font-weight:500;margin-right:16px}
.alerte{background:#fff3e0;border:1px solid #ffd59a;border-radius:8px;padding:10px 12px;margin:10px 0}
code{background:#eef1f6;padding:2px 6px;border-radius:5px}
@media (max-width:600px){header{padding:12px 16px}th:nth-child(4),td:nth-child(4){display:none}}
"""

BASE = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent logement</title><style>{{ css }}</style></head><body>
<header><a href="{{ url_for('accueil') }}"><h1>🏠 Agent logement</h1></a><span class="doux" style="color:#aab">{{ sous_titre }}</span></header>
<main>{% if message %}<div class="alerte">{{ message }}</div>{% endif %}{{ contenu|safe }}</main></body></html>"""

ACCUEIL = """
<div class="ligne" style="justify-content:space-between;margin-bottom:12px">
  <div><b>{{ personnes|length }}</b> personne(s) — les modifications relancent l'agent (≈ 10 s).</div>
  <a class="bouton" href="{{ url_for('fiche', ident='nouvelle') }}">+ Ajouter une personne</a></div>
{% for p in personnes %}
<div class="carte">
  <div class="ligne" style="justify-content:space-between">
    <div><h2>{{ p.nom }} {% if not p.actif %}<span class="pastille test">inactive</span>{% endif %}
      {% if pauses.get(p.id) %}<span class="pastille av">en pause</span>{% endif %}</h2>
      <div class="doux">{{ p.pieces_min }} pièces min · {{ p.surface_min|round|int }} m² min ·
        {% for z in p.zones %}{{ z.codes_postaux|join(', ') }} ≤ {{ z.loyer_max|round|int }} €{% if not loop.last %} · {% endif %}{% endfor %}</div>
      <div class="doux">Telegram : {% if p.telegram_chat_id %}<span class="pastille okp">relié</span>{% else %}<span class="pastille av">non relié</span>{% endif %}</div></div>
    <div class="ligne"><a class="bouton second" href="{{ url_for('fiche', ident=p.id) }}">Modifier</a>
      <form method="post" action="{{ url_for('basculer_pause', ident=p.id) }}"><input type="hidden" name="jeton" value="{{ jeton }}">
        <button class="bouton second">{{ 'Reprendre' if pauses.get(p.id) else 'Pause' }}</button></form></div>
  </div>
  <table><tr><th>Plateforme</th><th>Mode</th><th>Dernière lecture</th><th>Mes candidatures / erreur</th></tr>
  {% for code, nom in plateformes.items() %}{% set r = p.plateformes.get(code) %}{% if r and r.actif %}{% set e = etats[p.id][code] %}
    <tr><td>{{ nom }}</td><td>{% if r.production %}<span class="pastille prod">PRODUCTION</span>{% else %}<span class="pastille test">test</span>{% endif %}</td>
      <td>{{ e.lecture or 'jamais' }}</td><td>{% if e.erreur %}<span style="color:var(--accent)">⚠️ {{ e.erreur }}</span>{% else %}{{ e.candidatures or '—' }}{% endif %}</td></tr>
  {% endif %}{% endfor %}</table>
</div>
{% else %}<div class="carte">Aucune personne. Commence par « Ajouter une personne ».</div>{% endfor %}
"""

FICHE = """
<form method="post"><input type="hidden" name="jeton" value="{{ jeton }}">
<div class="carte"><h2>{{ 'Nouvelle personne' if nouvelle else p.nom }}</h2>
  <div class="grille"><div><label>Nom</label><input type="text" name="nom" value="{{ p.nom }}" required></div>
    <div><label>&nbsp;</label><span class="case"><input type="checkbox" name="actif" {{ 'checked' if p.actif }}> Surveillance active</span></div></div>
  <p class="doux">Chaque personne doit être d'accord pour que l'agent postule en son nom, et saisir elle-même ses mots de passe.</p></div>

<div class="carte"><h2>Critères</h2>
  <div class="grille">
    <div><label>Nombre de pièces minimum</label><input type="number" name="pieces_min" min="1" max="8" value="{{ p.pieces_min }}"></div>
    <div><label>Surface minimum (m²)</label><input type="number" name="surface_min" min="0" step="1" value="{{ p.surface_min|round|int }}"></div></div>
  <label>Zones : codes postaux et loyer maximum charges comprises</label>
  <p class="doux">Un code à 2 chiffres couvre tout le département (« 75 » = tout Paris). Séparer les codes par des virgules.
     L'ordre compte : la première zone est prioritaire.</p>
  <table><tr><th style="width:70%">Codes postaux</th><th>Loyer max (€)</th></tr>
  {% for i in range(nb_zones) %}{% set z = p.zones[i] if i < p.zones|length else none %}
    <tr><td><input type="text" name="zone_cp_{{ i }}" value="{{ z.codes_postaux|join(', ') if z else '' }}" placeholder="ex. 75, 93100, 93500"></td>
        <td><input type="number" name="zone_loyer_{{ i }}" min="0" step="1" value="{{ z.loyer_max|round|int if z else '' }}"></td></tr>
  {% endfor %}</table>
  <span class="case" style="margin-top:10px"><input type="checkbox" name="notif_hors_criteres" {{ 'checked' if p.notif_hors_criteres }}>
    Recevoir aussi un message simple pour les nouvelles offres hors critères</span></div>

<div class="carte"><h2>Plateformes</h2>
  <p class="doux">Le mot de passe est chiffré sur ce PC (protection Windows) ; laisser vide pour garder celui déjà enregistré.</p>
  {% for code, nom in plateformes.items() %}{% set r = p.plateformes.get(code) %}
  <div class="plate"><div class="ligne" style="justify-content:space-between"><b>{{ nom }}</b>
      <span><span class="case"><input type="checkbox" name="{{ code }}_actif" {{ 'checked' if r and r.actif }}> Surveiller</span>
            <span class="case"><input type="checkbox" name="{{ code }}_production" {{ 'checked' if r and r.production }}> Candidatures réelles</span></span></div>
    <div class="doux">{{ notes[code] }}</div>
    <div class="grille"><div><label>Identifiant / e-mail</label><input type="text" name="{{ code }}_identifiant" value="{{ r.identifiant if r else '' }}" autocomplete="off"></div>
      <div><label>Mot de passe {% if mdp.get(code) %}<span class="pastille okp">enregistré ✔</span>{% endif %}</label>
        <input type="password" name="{{ code }}_mdp" autocomplete="new-password" placeholder="{{ 'inchangé' if mdp.get(code) else '' }}"></div>
      <div><label>Loyer max propre (optionnel)</label><input type="number" name="{{ code }}_loyer_max" min="0" value="{{ r.loyer_max|round|int if r and r.loyer_max else '' }}"></div></div>
    {% if mdp.get(code) %}<span class="case doux"><input type="checkbox" name="{{ code }}_effacer"> effacer le mot de passe enregistré</span>{% endif %}
  </div>{% endfor %}</div>

<div class="carte"><h2>Lettre de motivation (LOC'Annonces)</h2>
  <p class="doux">Faits utilisés pour les motifs, une ligne « - Clé : valeur » par information. L'agent n'invente rien d'autre.</p>
  <textarea name="profil">{{ profil }}</textarea></div>

<div class="ligne"><button class="bouton">Enregistrer</button><a class="bouton second" href="{{ url_for('accueil') }}">Annuler</a></div>
</form>
{% if not nouvelle %}
<div class="carte" style="margin-top:16px"><h2>Telegram</h2>
  {% if p.telegram_chat_id %}<p><span class="pastille okp">relié</span> Les notifications de {{ p.nom }} arrivent sur son Telegram.</p>{% endif %}
  {% if p.code_liaison %}<p>Sur Telegram, ouvrir <b>@{{ bot }}</b>, appuyer sur Démarrer, puis envoyer : <code>/lier {{ p.code_liaison }}</code></p>{% endif %}
  <form method="post" action="{{ url_for('code_liaison', ident=p.id) }}"><input type="hidden" name="jeton" value="{{ jeton }}">
    <button class="bouton second">{{ 'Nouveau code de liaison' if p.code_liaison or p.telegram_chat_id else 'Obtenir un code de liaison' }}</button></form></div>
<div class="carte"><h2>Supprimer</h2>
  <form method="post" action="{{ url_for('supprimer', ident=p.id) }}"><input type="hidden" name="jeton" value="{{ jeton }}">
    <p class="doux">Supprime la personne, ses mots de passe chiffrés et son historique. Taper son nom pour confirmer.</p>
    <div class="ligne"><input type="text" name="confirmation" placeholder="{{ p.nom }}" style="max-width:280px">
      <button class="bouton danger">Supprimer définitivement</button></div></form></div>
{% endif %}
"""


def _etat(personne: Personne, p: str) -> dict:
    f = personne.dossier / p / "etat.json"
    try:
        e = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        e = {}
    return {"lecture": (e.get("derniere_lecture") or "")[:16].replace("T", " "),
            "erreur": (e.get("derniere_erreur") or "")[:120], "candidatures": ""}


def creer_app(agent=None, annuaire: Annuaire | None = None) -> Flask:
    app = Flask(__name__)
    annuaire = annuaire or Annuaire()
    nom_bot = {"v": None}

    def page(contenu_tpl: str, message: str = "", sous_titre: str = "", **ctx):
        contenu = render_template_string(contenu_tpl, jeton=JETON, plateformes=PLATEFORMES, **ctx)
        return render_template_string(BASE, css=CSS, contenu=contenu, message=message, sous_titre=sous_titre)

    def verifier_jeton():
        if request.form.get("jeton") != JETON:
            abort(403)

    def bot():
        if nom_bot["v"] is None:
            try:
                import requests
                tok = agent.cfg.telegram_token if agent else ""
                nom_bot["v"] = requests.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=5).json()["result"]["username"]
            except Exception:
                nom_bot["v"] = "ton_bot"
        return nom_bot["v"]

    @app.get("/")
    def accueil():
        personnes = sorted(annuaire.charger(), key=lambda x: x.nom.lower())
        etats = {p.id: {c: _etat(p, c) for c in PLATEFORMES} for p in personnes}
        pauses = {}
        if agent:
            for pid, esp in agent.espaces.items():
                pauses[pid] = esp.pause.is_set()
                for c, m in esp.moteurs.items():
                    etats[pid][c]["candidatures"] = m.etat_candidatures
        return page(ACCUEIL, request.args.get("message", ""), "personnes surveillées", personnes=personnes,
                    etats=etats, pauses=pauses)

    @app.get("/personne/<ident>")
    def fiche(ident):
        nouvelle = ident == "nouvelle"
        p = Personne(id="", nom="", zones=[Zone(["75"], 900)]) if nouvelle else annuaire.obtenir(ident)
        if not p:
            abort(404)
        profil_f = p.dossier / "profil_candidat.md" if not nouvelle else None
        profil = profil_f.read_text(encoding="utf-8") if profil_f and profil_f.exists() else \
            "- Situation professionnelle : ...\n- Revenus nets mensuels : ...\n- Composition du foyer : ...\n" \
            "- Logement actuel : ...\n- Lien avec le quartier : ...\n"
        mdp = {c: p.coffre.present(c) for c in PLATEFORMES} if not nouvelle else {}
        return page(FICHE, request.args.get("message", ""), p.nom or "nouvelle personne", p=p, nouvelle=nouvelle,
                    nb_zones=NB_ZONES, notes=NOTES_PLATEFORMES, mdp=mdp, profil=profil, bot=bot())

    @app.post("/personne/<ident>")
    def enregistrer(ident):
        verifier_jeton()
        f = request.form
        nouvelle = ident == "nouvelle"
        existants = {x.id for x in annuaire.charger()}
        nom = (f.get("nom") or "").strip()[:60] or "Sans nom"
        if nouvelle:
            p = Personne(id=identifiant_depuis_nom(nom, existants), nom=nom, code_liaison=nouveau_code())
        else:
            p = annuaire.obtenir(ident) or abort(404)
        p.nom, p.actif = nom, bool(f.get("actif"))
        p.pieces_min = max(1, int(f.get("pieces_min") or 1))
        p.surface_min = max(0.0, float(f.get("surface_min") or 0))
        p.notif_hors_criteres = bool(f.get("notif_hors_criteres"))
        zones = []
        for i in range(NB_ZONES):
            codes = codes_depuis_texte(f.get(f"zone_cp_{i}", ""))
            loyer = f.get(f"zone_loyer_{i}", "").strip()
            if codes and loyer:
                zones.append(Zone(codes, float(loyer)))
        p.zones = zones
        for code in PLATEFORMES:
            r = p.plateformes.get(code) or ReglagePlateforme()
            r.actif, r.production = bool(f.get(f"{code}_actif")), bool(f.get(f"{code}_production"))
            r.identifiant = (f.get(f"{code}_identifiant") or "").strip()
            lm = (f.get(f"{code}_loyer_max") or "").strip()
            r.loyer_max = float(lm) if lm else None
            p.plateformes[code] = r
            mdp = f.get(f"{code}_mdp") or ""
            if mdp:
                p.coffre.enregistrer(code, mdp)
            elif f.get(f"{code}_effacer"):
                p.coffre.effacer(code)
        p.dossier.mkdir(parents=True, exist_ok=True)
        (p.dossier / "profil_candidat.md").write_text((f.get("profil") or "").replace("\r\n", "\n"), encoding="utf-8")
        avert = []
        if not zones:
            avert.append("aucune zone valide : la personne ne correspondra à aucune offre")
        for code, r in p.plateformes.items():
            if r.actif and r.identifiant and not p.coffre.present(code):
                avert.append(f"{PLATEFORMES[code]} : mot de passe manquant")
        annuaire.enregistrer(p)
        log.info("personne %s enregistrée via l'interface", p.id)
        msg = "Enregistré. L'agent redémarre pour prendre en compte les changements (≈ 10 s)."
        if avert:
            msg += " Attention : " + " ; ".join(avert) + "."
        return redirect(url_for("fiche", ident=p.id, message=msg))

    @app.post("/personne/<ident>/code")
    def code_liaison(ident):
        verifier_jeton()
        p = annuaire.obtenir(ident) or abort(404)
        p.code_liaison = nouveau_code()
        annuaire.enregistrer(p)
        return redirect(url_for("fiche", ident=ident, message="Nouveau code de liaison créé (valable jusqu'à son utilisation)."))

    @app.post("/personne/<ident>/pause")
    def basculer_pause(ident):
        verifier_jeton()
        esp = agent.espaces.get(ident) if agent else None
        if esp:
            (esp.pause.clear if esp.pause.is_set() else esp.pause.set)()
        return redirect(url_for("accueil"))

    @app.post("/personne/<ident>/supprimer")
    def supprimer(ident):
        verifier_jeton()
        p = annuaire.obtenir(ident) or abort(404)
        if (request.form.get("confirmation") or "").strip() != p.nom:
            return redirect(url_for("fiche", ident=ident, message="Nom de confirmation incorrect : rien n'a été supprimé."))
        annuaire.supprimer(ident)
        shutil.rmtree(p.dossier / "secrets", ignore_errors=True)
        log.info("personne %s supprimée via l'interface", ident)
        return redirect(url_for("accueil", message=f"{p.nom} supprimé(e)."))

    return app


def demarrer_interface(agent=None) -> None:
    app = creer_app(agent)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    t = threading.Thread(target=lambda: app.run(host=HOTE, port=PORT, debug=False, use_reloader=False),
                         daemon=True, name="interface")
    t.start()
    log.info("interface : http://%s:%d", HOTE, PORT)
