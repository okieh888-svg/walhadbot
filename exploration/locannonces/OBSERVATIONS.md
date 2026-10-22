# LOC'Annonces (Ville de Paris) — observations du 22/10/2026

- Application Lutece rendue côté serveur (pas d'API JSON). Connexion par le SSO **Mon Paris**
  (Keycloak `v70-auth.paris.fr`, realm `paris`) : champs `#username` (e-mail), `#password`, bouton
  « Continuer ». Pas de captcha ni de 2FA (confirmé par l'utilisateur). Bannière cookies tarteaucitron →
  « Tout refuser » (`#tarteaucitronAllDenied`).
- Session : la page d'accueil fait un aller-retour OIDC `prompt=none` ; attendre la fin des redirections.

## Liste (`/locannonces/`, connecté) — « Appartements adaptés à ma situation »
Cartes `#logts ul li a[href^="logement/"]` : `p.dateLimite[title]` (« … jusqu'au JJ/MM/AAAA »),
`span.loyer` (« 817 € CCC » = **charges collectives comprises**), `span.loc` (« PARIS 20e » ou
« LA CELLE ST CLOUD 78 » : pas de code postal), `span.surface`, `span.pieces`.
3 annonces le 22/10, pas de pagination observée. La synthèse indique les catégories éligibles
(« C (PLS), D (PLI) ») : la sélection est déjà filtrée par le site sur le profil.

## Détail (`/locannonces/logement/<id>`)
- `dl` : Pièces, Surface, Loyer (HC), Charges, Parking, Étage.
- `p.nbCand strong` : **nombre de personnes ayant déjà postulé** (239 sur l'annonce 18750).
- `p.dateLimCand[title]` : date limite. `section.adeq` : plafond (« plafond D (PLI) »), taux d'effort ≤ 33 %, taille du foyer.
- Réservataire : variable Matomo `PARTENAIRE`.
- Bouton : `<form method="POST" action="logement/<id>/candidature.html"><button id="linkPostuler">Candidater</button>`.
  **POST direct, étape suivante inconnue** (non cliqué : peut enregistrer la candidature). Selon la FAQ, si une
  candidature existe déjà, une fenêtre propose de la remplacer : l'agent ne doit jamais accepter.

## Mes candidatures (`/locannonces/candidature.html`)
Bloc « CANDIDATURE EN COURS » ; texte « Vous n'avez pas de candidature en cours » quand il n'y en a pas.

## Règles du site (CGU art. 5.3.1 / 5.4, page « Comment postuler »)
- À l'expiration de l'annonce, la Ville propose **3 dossiers** au bailleur après traitement automatisé (cotation),
  « le nombre de candidatures pour un logement n'influe pas » → l'utilisateur a choisi de garder malgré tout la règle « 0 à 2 candidats ».
- Une candidature à la fois (par réservataire), annulable jusqu'à la date limite.
- CGU : pas de clause explicite interdisant l'automatisation (seulement la reproduction du contenu).
