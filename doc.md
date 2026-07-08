python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements-pages.txt


Le projet : à partir d'un gpx, créer des cartes météo sur les 16 prochains jours.

1. Les parcours GPX sont placés dans `gpx/` ; chaque fichier produit sa propre page.
2. config.py définit les paramètres
3. town.py repère les villes de départ, d'arrivée et les villes intermédiaires.
4. weather.py récupère les prévisions pour chacune des villes.
5. carto.py génère `_output/<parcours>/index.html` avec Leaflet.

La carte HTML utilise Leaflet et OpenStreetMap. Les prévisions sont conservées dans `previsions_brutes.csv` pendant
`config.weather_cache_hours` heures ; chaque actualisation inscrit aussi
`fetched_at_utc` dans le CSV.

Le cache est remplacé atomiquement et décrit par `previsions_brutes.meta.json` ;
en cas d'échec réseau, le dernier CSV valide reste utilisable. La page propose
une navigation par jour et une fiche météo détaillée au clic. La trace GPX est simplifiée avant son
intégration afin d'alléger le HTML sans modifier le fichier source.

cd /Users/thierrycrouzet/Documents/python/gpxWeather/
codex resume 019f334f-df36-7730-ab8a-a2613c08203b
/permissions
