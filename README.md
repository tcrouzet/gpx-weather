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
