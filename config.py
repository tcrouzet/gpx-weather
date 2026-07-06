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
import re
import unicodedata
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Publication GitHub Pages declenchee par app.py apres une generation locale.
github_repository = "tcrouzet/gpx-weather"
github_pages_base_url = "https://tcrouzet.github.io/gpx-weather"
gpx_dir = os.path.join(BASE_DIR, "gpx")

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


# Heures de prévision (24h correspond à 0h le lendemain).
sample_hours = [0, 6, 10, 14, 16, 20]

# Cadence de lecture du slider HTML.
speed = 0.5


# Duree estimee du voyage, en jours (a ajuster selon le parcours)
trip_days = 8

# Rayon de recherche des villes autour de la trace (km) : une ville est
# consideree "sur le trajet" si elle se trouve a moins de cette distance
# perpendiculaire de la trace GPX
town_search_buffer_km = 15

output_root = os.path.join(BASE_DIR, "_output")


def list_gpx_files():
    """Retourne tous les parcours GPX, dans un ordre stable."""
    return sorted(str(path) for path in Path(gpx_dir).glob("*.gpx"))


def route_slug_for(path):
    text = unicodedata.normalize("NFKD", Path(path).stem).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def route_title_for(path):
    return Path(path).stem.replace("-", " ").replace("_", " ").title()


def configure_route(path):
    """Configure les chemins derives pour un parcours donne."""
    global project, route_slug, gpx_file, outdir, csv_path
    global weather_cache_meta_path, html_path, towns_csv_path
    global all_towns_csv_path, github_pages_url

    gpx_file = os.path.abspath(path)
    route_slug = route_slug_for(path)
    project = route_title_for(path)
    outdir = os.path.join(output_root, route_slug)
    csv_path = os.path.join(outdir, "previsions_brutes.csv")
    weather_cache_meta_path = os.path.join(outdir, "previsions_brutes.meta.json")
    html_path = os.path.join(outdir, "index.html")
    towns_csv_path = os.path.join(gpx_dir, f"{route_slug}.villes.csv")
    all_towns_csv_path = os.path.join(outdir, "all_towns.csv")
    github_pages_url = f"{github_pages_base_url}/{route_slug}/"


_routes = list_gpx_files()
if _routes:
    configure_route(_routes[0])
