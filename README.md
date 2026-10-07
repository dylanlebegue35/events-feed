# events-feed

Robot qui rassemble les événements de la région de Liège dans `events.json`, chaque jour, tout seul.
L'app lit ce fichier.

- `sources.json` : les sites que l'IA lit. Pour en ajouter un, copier un bloc et changer l'adresse.
- `manual_events.json` : événements vérifiés à la main (foire, marché…).
- `build_events.py` : le robot (Spa-Francorchamps, pages web lues par l'IA, événements manuels).
- `.github/workflows/update.yml` : le déclenchement quotidien sur GitHub.

Secret à créer sur GitHub (Settings → Secrets and variables → Actions) : `ANTHROPIC_API_KEY`.
Lancer à la main : onglet Actions → « Mise à jour des événements » → Run workflow.
