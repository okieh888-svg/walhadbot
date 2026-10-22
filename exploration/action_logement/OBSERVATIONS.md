# Action Logement — logement-actionlogement.fr — observations du 22/10/2026

- SPA Angular derrière Cloudflare (un `curl` reçoit 403 : on passe uniquement par le vrai Chrome, sans contournement).
- Connexion Auth0 (`authentification.actionlogement.fr`) en 2 étapes : e-mail → « Continuer » → mot de passe.
  Pas de code ni de captcha (confirmé par l'utilisateur). Bannière CookieFirst → « Refuser tous les cookies ».
- API REST `https://api.logement-actionlogement.fr/api/v1`, en-tête `Authorization: Bearer <jeton Auth0>`
  (le front le gère et le renouvelle ; l'agent le récupère sur les requêtes du front).

| Usage | Requête |
|---|---|
| Recherche (paginée) | `POST /demands/offers-overview?size=50&page=N` corps `{"municipalities":[{"code":<INSEE>,"postcode":…}],"searchRadiusInKm":0,"productGuids":[],"typologyCodes":[]}` → `{totalPages,totalSize,data:[…]}` |
| Détail | `POST /demands/offer-details` `{"offerGuid":…}` |
| Compteurs | `GET /demands/ongoing-candidacies-counters` → BLUE (multiples) limite 5, RED (unique) limite 1 |

Paris : codes INSEE par arrondissement 75101…75120 (16e : CP 75116) ; 75056 fonctionne aussi.
La recherche enregistrée sur le site par l'utilisateur ne contient pas Paris et a un rayon de 5 km :
l'agent envoie sa propre liste (Paris + 8 communes, rayon 0).

## Champs
`id`, `guid` (URL fiche : `https://logement-actionlogement.fr/search/detail/<guid>`),
`totalRentAmountBaseBound` (**loyer charges comprises**), `lodging.area`, `lodging.lodgingTypology.code` (T1/T2…),
`lodging.municipality.{name,postcode}`, `startDateOfPublication`, `candidacy` (non nul si déjà candidaté),
`offerCategory` (BLUE/RED). Détail : `lodging.floor.label`, `endDateOfPublication` (date limite).
**Nombre de candidats : non exposé** (ni liste, ni détail).
Saint-Denis inclut désormais le CP 93380 (fusion avec Pierrefitte au 1/1/2025).
Bouton « Je candidate » sur la fiche : jamais cliqué, et jamais cliqué par l'agent.
