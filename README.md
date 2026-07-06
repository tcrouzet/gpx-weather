# GPX Weather

Cartes météo interactives de parcours GPX, mises à jour automatiquement et
publiée avec GitHub Pages.

La page publique est générée par `.github/workflows/pages.yml`. Elle utilise
Open-Meteo, l'ensemble ECMWF à longue échéance, Leaflet et OpenStreetMap.

## Génération locale

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements-pages.txt
python app.py
```

Place les parcours dans `gpx/`. Chaque `nom.gpx` produit
`_output/nom/index.html` et la page publique `/nom/`. Le fichier optionnel
`gpx/nom.villes.csv` évite de recalculer les villes du parcours.

Tourmagne est publié sur
<https://tcrouzet.github.io/gpx-weather/tourmagne/>.

La racine `_output/index.html` liste tous les parcours.
Le répertoire `_output` est entièrement local et n'est jamais versionné.

En dehors de GitHub Actions, `python app.py` déclenche ensuite automatiquement
le workflow Pages. La nouvelle version publique apparaît généralement moins
d'une minute plus tard sur <https://tcrouzet.github.io/gpx-weather/>.
