# GPX Weather

Carte météo interactive d'un parcours GPX, mise à jour automatiquement et
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

La carte est écrite dans `_output/index.html`.
Le répertoire `_output` est entièrement local et n'est jamais versionné.

En dehors de GitHub Actions, `python app.py` déclenche ensuite automatiquement
le workflow Pages. La nouvelle version publique apparaît généralement moins
d'une minute plus tard sur <https://tcrouzet.github.io/gpx-weather/>.
