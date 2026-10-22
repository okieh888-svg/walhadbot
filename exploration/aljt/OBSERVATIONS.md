# Pass Logement (ALJT) — observations du 22/10/2026

- Application ExtJS 4, session PHP (`PHPSESSID`, expiration ~30 min glissantes).
- Connexion : `https://offres.passlogement.com/account`, champs `#username-inputEl`, `#password-inputEl`,
  bouton « S'identifier ». Pas de captcha ni de 2FA observés.
- Session expirée : les API JSON répondent `_status.code == 1` (l'appli ouvre alors `Gdc.Box.Login`).

## API (toutes en JSON, cookie de session)
| Usage | Requête |
|---|---|
| Liste des offres | `GET /account/offer/listing/json?page=1&start=0&limit=50&sort=[{"property":"dateCreated","direction":"DESC"}]` → `{totalCount, offer:[…]}` |
| Détail | `POST /account/offer/view/json` `offId=<id>` |
| **Candidater** | `POST /account/application/register/json` `offId=<id>` → `{success, message}` ou `{_status:{message}}` — aucune confirmation intermédiaire |
| Mes candidatures | `GET /account/application/listing/json?page=1&start=0&limit=50&sort=[{"property":"app_date_updated","direction":"DESC"}]` → `{totalCount, candidate:[…]}` |

## Champs d'une offre
`id` (numérique, stable), `specialId` (référence bailleur), `accommodationTypeLabel` (T1/T2…),
`surface`, `rentalPrice` (**charges comprises** : la fiche affiche « Loyer (charges comprises) »),
`zipcode`, `city` (abrégé : « ST DENIS », « ST OUEN »), `dateCreated`, `dateValidity` (visible jusqu'au),
`numberCandidatesOnOffer` (**nombre de candidatures en cours — visible**), `incomeCeilingLabel` (PLS/PLI/LLI…),
`description`, `partnerLabel`.

## Mes candidatures
Statut : `ast_label`. Statuts terminés observés : « Désisté », « Logement attribué à un autre résident »,
« Logement plus disponible », « Non transmis en commission ». Tout autre statut est considéré comme
**actif** par prudence (une seule candidature à la fois).
