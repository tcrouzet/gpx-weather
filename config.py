# -*- coding: utf-8 -*-
"""
config.py
---------

Configuration centrale du systeme de cartographie meteo.
Toutes les valeurs sont en dur ici : modifie-les selon tes besoins,
les deux scripts (meteo_cartographie.py et meteo_carto.py) les
utilisent directement, plus besoin d'arguments en ligne de commande.
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Publication GitHub Pages declenchee par app.py apres une generation locale.
github_repository = "tcrouzet/gpx-weather"
github_pages_url = "https://tcrouzet.github.io/gpx-weather/"

# Nom
project ="Tourmagne"

# Fichier GPX source
gpx_file = "source.gpx"

town_search_buffer_km = 15
endpoint_search_radius_km = 15

# Nombre de jours de prevision a recuperer (max ~16 chez Open-Meteo)
forecast_days = 16

# Jusqu'a J+4 : modele local haute resolution Open-Meteo best_match.
# A partir de J+5 : mediane et incertitude des 51 membres ECMWF IFS ENS.
ensemble_after_days = 5

# Le CSV de previsions est reutilise pendant cette duree. Au-dela, les API
# sont rappelees et le fichier est remplace avec un nouvel horodatage UTC.
weather_cache_hours = 3

# Simplification de la trace envoyee au navigateur (~50 m en latitude).
gpx_simplify_degrees = 0.0005


# Heures de prévision
sample_hours = [0, 6, 12, 18]

# Style du fond de carte pour meteo_carto.py
# Choix possibles : "osm", "positron", "voyager", "terrain"
basemap = "voyager"

# Multiplicateur global de la taille des polices (titre du fond de carte,
# date/heure et temperatures dans carto.py). 1.0 = taille par defaut,
# 1.5 = 50% plus gros, etc.
typo_size = 3
background_color = "black"
title_color = "white"

# Qualite du fond WebP (0-100) et cadence de lecture du slider HTML.
webp_quality = 86
speed = 0.5


# Duree estimee du voyage, en jours (a ajuster selon le parcours)
trip_days = 8

# Rayon de recherche des villes autour de la trace (km) : une ville est
# consideree "sur le trajet" si elle se trouve a moins de cette distance
# perpendiculaire de la trace GPX
town_search_buffer_km = 15

# --- Chemins derives, ne pas modifier ---
outdir = os.path.join(BASE_DIR, "_output")
csv_path = os.path.join(outdir, "previsions_brutes.csv")
weather_cache_meta_path = os.path.join(outdir, "previsions_brutes.meta.json")
html_path = os.path.join(outdir, "index.html")
towns_csv_path = os.path.join(BASE_DIR, "villes.csv")


# Cache de TOUTES les communes trouvees le long de la trace (une seule
# requete Overpass). Si ce fichier existe deja, town.py ne refait pas
# la requete Overpass et le relit directement : supprime-le si tu changes
# de GPX ou si tu veux forcer une nouvelle requete.
all_towns_csv_path = os.path.join(outdir, "all_towns.csv")
