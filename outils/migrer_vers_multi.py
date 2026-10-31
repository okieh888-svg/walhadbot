"""Migration unique : configuration mono-personne (.env + data/<plateforme>) → personne n°1.

- Crée data/personnes.json avec une personne reprenant les critères historiques.
- Déplace data/<plateforme>/ (offres vues, sessions navigateur, captures, motifs) vers
  data/personnes/<id>/<plateforme>/ : aucune offre déjà vue n'est retraitée.
- Chiffre (DPAPI) les mots de passe présents dans .env ; ils ne sont jamais affichés.
À lancer agent ARRÊTÉ :  python outils/migrer_vers_multi.py "Ismail"
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coeur.config import DATA, RACINE, Config  # noqa: E402
from coeur.personnes import Annuaire, Personne, ReglagePlateforme, Zone, identifiant_depuis_nom  # noqa: E402

nom = sys.argv[1] if len(sys.argv) > 1 else "Moi"
annuaire = Annuaire()
if annuaire.charger():
    sys.exit("data/personnes.json existe déjà : migration déjà faite.")
cfg = Config.charger()
p = Personne(id=identifiant_depuis_nom(nom, set()), nom=nom,
             zones=[Zone(["75"], 880.0),
                    Zone(["93400", "93200", "93210", "93380", "93300", "93500", "93310", "93260", "93170", "93100"], 800.0)],
             pieces_min=2, surface_min=40.0, notif_hors_criteres=cfg.notif_hors_criteres,
             telegram_chat_id=str(cfg.telegram_chat_id or ""))
for code in ("aljt", "locannonces", "action_logement"):
    ident, mdp = cfg.identifiants.get(code, ("", ""))
    p.plateformes[code] = ReglagePlateforme(actif=cfg.actives.get(code, False), production=cfg.production.get(code, False),
                                            identifiant=ident, loyer_max=850.0 if code == "locannonces" else None)
    if mdp:
        p.coffre.enregistrer(code, mdp)
p.plateformes["alin"] = ReglagePlateforme(actif=False)

p.dossier.mkdir(parents=True, exist_ok=True)
for code in ("aljt", "locannonces", "action_logement"):
    src = DATA / code
    if src.exists():
        shutil.move(str(src), str(p.dossier / code))
        print(f"déplacé : data/{code} → data/personnes/{p.id}/{code}")
alertes = p.dossier / "action_logement" / "alertes.json"
if alertes.exists():
    shutil.move(str(alertes), str(p.dossier / "alertes.json"))
profil = RACINE / "profil_candidat.md"
if profil.exists():
    shutil.copy2(profil, p.dossier / "profil_candidat.md")
annuaire.enregistrer(p)
print(f"personne « {p.nom} » ({p.id}) créée ; mots de passe chiffrés : "
      f"{[c for c in p.plateformes if p.coffre.present(c)]}")
print("Tu peux maintenant retirer les lignes *_EMAIL / *_PASSWORD / PARIS_LOGIN de .env.")
