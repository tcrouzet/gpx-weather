#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestrateur du pipeline meteo.

Exécute successivement town.py, weather.py avec cache résilient, puis
carto.py pour produire la visualisation Leaflet interactive.
"""

import os
import json
import shutil
import subprocess
from datetime import datetime, timezone
from html import escape

import config


def run_step(module_name, label):
    print(f"\n=== {label} ===")
    module = __import__(module_name)
    module.main()


def cache_is_fresh(path, max_age_hours):
    if not os.path.exists(path):
        return False
    modified = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - modified).total_seconds() / 3600
    return age_hours < max_age_hours


def weather_cache_is_fresh(max_age_hours):
    if not os.path.exists(config.csv_path):
        return False
    try:
        with open(config.weather_cache_meta_path, "r", encoding="utf-8") as handle:
            fetched = datetime.fromisoformat(json.load(handle)["fetched_at_utc"])
        return (datetime.now(timezone.utc) - fetched).total_seconds() < max_age_hours * 3600
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return cache_is_fresh(config.csv_path, max_age_hours)


def publish_pages():
    """Declenche la regeneration distante, sauf depuis GitHub Actions.

    Le workflow relit le depot et recupere lui-meme les donnees fraiches ;
    aucun cache local ni secret n'est envoye vers GitHub.
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return
    gh = shutil.which("gh")
    if not gh:
        raise RuntimeError("GitHub CLI (gh) est requis pour publier la carte")
    repository = getattr(config, "github_repository", "tcrouzet/gpx-weather")
    subprocess.run(
        [gh, "workflow", "run", "pages.yml", "--repo", repository],
        check=True,
    )
    print(f"Publication GitHub Pages déclenchée : {config.github_pages_base_url}/")


def write_routes_index(routes):
    """Crée l'accueil Pages listant tous les parcours disponibles."""
    os.makedirs(config.output_root, exist_ok=True)
    links = "".join(
        f'<li><a href="{escape(slug)}/">{escape(title)}</a></li>'
        for slug, title in routes
    )
    html = (
        '<!doctype html><html lang="fr"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Prévisions météo des parcours</title>'
        '<style>body{font:18px system-ui;max-width:700px;margin:3rem auto;padding:1rem}'
        'li{margin:.8rem 0}a{color:#1677d2}</style>'
        f'<h1>Prévisions météo des parcours</h1><ul>{links}</ul></html>'
    )
    with open(os.path.join(config.output_root, "index.html"), "w", encoding="utf-8") as handle:
        handle.write(html)


def process_route(gpx_path):
    config.configure_route(gpx_path)
    print(f"\n##### Parcours : {config.project} ({config.route_slug}) #####")

    if not os.path.exists(config.towns_csv_path):
        run_step("town", "Étape 1 : town")
    else:
        print(f"Skipping town.py : {config.towns_csv_path} existe déjà")

    cache_hours = getattr(config, "weather_cache_hours", 3)
    if not weather_cache_is_fresh(cache_hours):
        try:
            run_step("weather", "Étape 2 : weather")
        except Exception as exc:
            if not os.path.exists(config.csv_path):
                raise
            print(f"Échec de l'actualisation météo, ancien cache conservé : {exc}")
    else:
        modified = datetime.fromtimestamp(os.path.getmtime(config.csv_path), tz=timezone.utc)
        print(
            f"Skipping weather.py : cache valide jusqu'a {cache_hours} h "
            f"({modified.isoformat(timespec='seconds')})"
        )

    run_step("carto", "Étape 4 : carto Leaflet")


def main():
    gpx_files = config.list_gpx_files()
    if not gpx_files:
        raise FileNotFoundError(f"Aucun fichier .gpx dans {config.gpx_dir}")
    routes = []
    for gpx_path in gpx_files:
        process_route(gpx_path)
        routes.append((config.route_slug, config.project))
    write_routes_index(routes)
    publish_pages()
    print("\nPipeline terminé.")


if __name__ == "__main__":
    main()
