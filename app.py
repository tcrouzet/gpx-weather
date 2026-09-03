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

import config
from gpx_export import export_simplified_gpx
from navigation import NAVIGATION_CSS, NAVIGATION_SCRIPT, render_navigation, render_route_links


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
        # Une modification de la liste des villes change les coordonnées des
        # prévisions : l'ancien cache météo ne doit alors jamais être réutilisé.
        if os.path.exists(config.towns_csv_path):
            towns_modified = datetime.fromtimestamp(
                os.path.getmtime(config.towns_csv_path), tz=timezone.utc
            )
            if towns_modified > fetched:
                return False
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
    """Crée l'accueil Pages avec l'aide et la liste des parcours."""
    os.makedirs(config.output_root, exist_ok=True)
    for asset in ("manifest.webmanifest", "sw.js", "icon-192.png", "icon-512.png", "apple-touch-icon.png"):
        shutil.copy2(os.path.join(config.BASE_DIR, "webapp", asset), config.output_root)
    route_links = render_route_links(routes)
    navigation_html = render_navigation("GPX Weather", routes, "./")
    html = f'''<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>GPX Weather</title>
<meta name="theme-color" content="#18295c"><meta name="apple-mobile-web-app-capable" content="yes">
<link rel="manifest" href="manifest.webmanifest"><link rel="icon" href="icon-192.png"><link rel="apple-touch-icon" href="apple-touch-icon.png">
<style>*{{box-sizing:border-box}}body{{margin:0;background:#f4f6fa;color:#17234d;font-family:system-ui,sans-serif}}
{NAVIGATION_CSS}
.content{{width:min(720px,100%);margin:auto;padding:24px 18px 40px}}h1{{font-size:25px;margin:0 0 16px}}h2{{font-size:19px;margin:28px 0 10px}}
.routes{{display:grid;gap:8px}}.routes a{{display:block;padding:13px 15px;background:#fff;border-radius:10px;color:#315bb5;font-weight:750;text-decoration:none;box-shadow:0 1px 4px #17234d18}}
li{{margin:.55rem 0;line-height:1.4}}</style></head><body>
{navigation_html}
<main class="content"><h1>Prévisions disponibles</h1><div class="routes">{route_links}</div>
<h2>Aide</h2><ul><li>Choisissez un parcours dans la liste ou dans le menu.</li>
<li>Faites glisser les frises du jour et de l’heure pour changer la prévision affichée.</li>
<li>Touchez une icône météo sur la carte pour ouvrir les prévisions détaillées de ce point.</li>
<li>Le bouton de lecture sur la carte fait défiler automatiquement les prévisions.</li>
<li>Le sélecteur en haut à droite de la carte permet de changer le fond de carte.</li></ul></main>
<script>{NAVIGATION_SCRIPT}
if('serviceWorker' in navigator)navigator.serviceWorker.register('sw.js');</script></body></html>'''
    with open(os.path.join(config.output_root, "index.html"), "w", encoding="utf-8") as handle:
        handle.write(html)


def process_route(gpx_path):
    config.configure_route(gpx_path)
    print(f"\n##### Parcours : {config.project} ({config.route_slug}) #####")

    export_simplified_gpx(
        config.gpx_file, config.production_gpx_path,
        interval_km=getattr(config, "production_gpx_interval_km", 1),
        name=config.project,
    )

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
    shutil.copy2(
        config.production_gpx_path,
        os.path.join(config.outdir, "trace.gpx"),
    )


def main():
    gpx_files = config.list_gpx_files()
    if not gpx_files:
        raise FileNotFoundError(
            f"Aucun fichier .gpx dans {config.source_gpx_dir} "
            f"ni dans {config.public_gpx_dir}"
        )
    routes = []
    for gpx_path in gpx_files:
        process_route(gpx_path)
        routes.append((config.route_slug, config.project))
    write_routes_index(routes)
    publish_pages()
    print("\nPipeline terminé.")


if __name__ == "__main__":
    main()
