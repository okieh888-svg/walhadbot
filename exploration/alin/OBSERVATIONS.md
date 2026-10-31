# AL'in (Action Logement, logement social) — https://al-in.fr — observations du 31/10/2026

- SPA Angular, routes en `#/` : `HomeSalarie` (accueil + candidatures), `home-logements` (offres),
  `fiche-logement/:id` (fiche). La navigation directe par l'URL est refusée : passer par l'interface ou l'API.
- Connexion : formulaire « Se connecter » → `POST https://api.be-ys.com/als-back/v1/accounts/authenticate`
  `{login, password}` → `POST https://api.al-in.fr/api/token_exchange/als_hermes_salarie` → jeton JWT stocké dans
  `localStorage.id_token`. **Durée de vie : 15 min, pas de jeton de renouvellement** → reconnexion régulière
  (mot de passe chiffré). Bannière CookieFirst → « Refuser tous les cookies ».
- API : `https://api.al-in.fr/api`, en-tête `Authorization: Bearer <id_token>` (format JSON:API `data/attributes`).

| Usage | Requête |
|---|---|
| Ma demande | `GET /dmo/housing_requests/last_request` → `data.id`, `active_application_id`, `dormant_application_ids`, `need_to_be_renewed` |
| Offres éligibles | `GET /dmo/housing_requests/<als_id>/eligible_offers?per_page=30&page=N&eligibility_type=seeked\|bordering\|other_from_department&rent_with_charges[$gte]=…&rent_with_charges[$lte]=…` |
| Nombre de candidats | `GET /dmo/housing_offers/with_recount_eligible_requests_nb?housing_offer_ids[]=<id>` → `applicated_nb` |
| Fiche | `GET /dmo/housing_offers/<id>` |
| Mes candidatures | `GET /dmo/housing_applications?is_active=true&housing_request_id=<id>` |
| **Candidater** | `POST /dmo/housing_applications` `{"data":{"attributes":{"status":"EN ATTENTE","housing_request_id":…,"housing_offer_id":…}}}` |
| Annuler | `PATCH /dmo/housing_applications/<id>` |

## Champs d'une offre (`data[].attributes`)
`typology` (T2…), `surface`, `rent_with_charges` (**charges comprises**), `rent_amount`, `rental_charges`,
`postal_code`, `district` (ville), `insee_code`, `floor`, `date_publication_start`, `publication_end_date`,
`external_ref`, `residence_title`, `description`, `main_picture.full_size_absolute`.

## Règles (CGU 11.22–11.26, messages du site)
- **Une seule candidature à la fois** ; pour postuler ailleurs il faut annuler la candidature en cours
  (sauf candidature classée rang 2 ou 3 en CALEOL). Candidature annulable tant qu'elle n'est pas transmise au bailleur.
- Le site vérifie avant l'envoi : pas de candidature active, dormantes < maximum, demande non radiée / à renouveler.
- Offres publiées une semaine au plus ; le site affiche le nombre de candidatures déposées (`applicated_nb`).
- Bouton « Je postule » : jamais cliqué pendant l'exploration.
