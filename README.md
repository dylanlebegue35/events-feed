# events-feed

Robot qui rassemble les événements de la région de Liège dans `events.json`, chaque jour, tout seul.
L'app lit ce fichier.

- `sources.json` : les sites que l'IA lit. Pour en ajouter un, copier un bloc et changer l'adresse.
- `manual_events.json` : événements vérifiés à la main (foire, marché…).
- `build_events.py` : le robot (Spa-Francorchamps, pages web lues par l'IA, événements manuels).
- `.github/workflows/update.yml` : le déclenchement quotidien sur GitHub.

Secret à créer sur GitHub (Settings → Secrets and variables → Actions) : `ANTHROPIC_API_KEY`.
Lancer à la main : onglet Actions → « Mise à jour des événements » → Run workflow.

## Boîte de dépôt : ajouter un événement en 20 secondes

Prends une **capture d'écran** ou une **photo de l'affiche** d'un événement (bal rétro, soirée, ronde des bières…),
puis glisse-la dans le dossier [`inbox`](https://github.com/dylanlebegue35/events-feed/upload/main/inbox)
(bouton *Add file → Upload files*, puis *Commit changes*).
Le robot se lance tout seul, l'IA lit l'image, crée l'événement (titre, date, lieu, prix, public visé),
et l'affiche devient la photo de l'événement. Un fichier texte (`.txt`) avec le texte de l'annonce marche aussi.

À ne déposer que des affiches publiques, de préférence avec l'accord de l'organisateur.
