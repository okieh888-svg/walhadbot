"""Démonstration DRY-RUN sur une offre existante (ne touche pas à la mémoire, n'envoie JAMAIS rien).

  python outils/demo_dry_run.py aljt [id_offre]
  python outils/demo_dry_run.py locannonces [id_offre]
  python outils/demo_dry_run.py action_logement [id_offre]

Sans id : prend la première offre de la liste. Affiche les champs, le verdict, la décision
de la politique, le motif (LOC'Annonces) et le chemin de la capture prise avant le clic final.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coeur import journal  # noqa: E402
from coeur.config import DATA, Config  # noqa: E402
from coeur.criteres import classer  # noqa: E402
from coeur.motif import GenerateurMotif  # noqa: E402
from coeur.politiques import decider  # noqa: E402
from plateformes import ADAPTATEURS  # noqa: E402

manuel = "--manuel" in sys.argv
args = [x for x in sys.argv[1:] if x != "--manuel"]
p = args[0]
cible = args[1] if len(args) > 1 else None
cfg = Config.charger()
journal.configurer(debug=True)
a = ADAPTATEURS[p](DATA / p, identifiants=cfg.identifiants[p], headless=cfg.headless_par.get(p, True) and not manuel, canal=cfg.canal_navigateur, debug=True)
a.connexion_auto = False          # jamais de saisie d'identifiants ici
a.reduire = not manuel
a.demarrer()
try:
    if manuel and not a.est_connecte():
        a.attendre_connexion_manuelle()
    a.connexion()
    offres = a.lister_offres()
    print(f"\n=== {len(offres)} offres lues sur {p} ===")
    for o in offres:
        print(f"  {o.id:>8} | {classer(o).ok and 'OK ' or '   '} | {o.resume()}")
    o = next((x for x in offres if x.id == cible), offres[0] if offres else None)
    if not o:
        sys.exit("aucune offre")
    a.enrichir(o)
    v = classer(o)
    active = a.candidature_active()
    print(f"\n=== Offre {o.id} ===\n{json.dumps(o.to_dict(), ensure_ascii=False, indent=1)}")
    print(f"Verdict critères : {v.ok} ({v.raison})")
    print(f"Candidature active sur le compte : {active}")
    print(f"Décision politique (si nouvelle) : {decider(o, v, active, p)}")
    if p == "action_logement":
        print("Action Logement : alerte seulement. Capture de la fiche :", a.capture_offre(o))
    else:
        motif = None
        if a.besoin_motif:
            m = GenerateurMotif(cfg.ollama_url, cfg.ollama_modele, cfg.llm_timeout).generer(o, a.motif_max or 1000)
            motif = m.texte
            print(f"\n--- Motif ({m.source}{' : ' + m.raison_secours if m.raison_secours else ''}, {len(m.texte)} car.) ---\n{m.texte}\n")
        r = a.candidater(o, motif, dry_run=True)     # DRY-RUN imposé
        assert not r.envoye
        print(f"Résultat dry-run : {r.message}\nCapture avant le clic final : {r.capture}")
finally:
    a.fermer()
