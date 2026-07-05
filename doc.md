python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt


Le projet : à partir d'un gpx, créer des cartes météo sur les 16 prochains jours.

1. config.py défini les paramètres
2. town.py repère les villes de départ et d'arrivée et des villes intermédiaires, générant villes.csv.
3. weather.py récupères les prévisions pour chacune des villes.
4. background.py génère un unique fond de carte WebP avec le titre, la trace GPX, les noms des villes, leur distance et les carrés de température.
5. carto.py génère `_output/index.html`. Le slider change en HTML la date, les températures et les symboles météo superposés au fond WebP, sans produire une image par échéance.

La carte HTML utilise Leaflet et OpenStreetMap. Les prévisions sont conservées dans `previsions_brutes.csv` pendant
`config.weather_cache_hours` heures ; chaque actualisation inscrit aussi
`fetched_at_utc` dans le CSV.

Le cache est remplacé atomiquement et décrit par `previsions_brutes.meta.json` ;
en cas d'échec réseau, le dernier CSV valide reste utilisable. La page propose
une légende d'incertitude, une navigation par jour et
une fiche météo détaillée au clic. La trace GPX est simplifiée avant son
intégration afin d'alléger le HTML sans modifier le fichier source.

cd /Users/thierrycrouzet/Documents/python/gpxWeather/
codex
