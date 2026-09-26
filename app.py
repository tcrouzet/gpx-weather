#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestrateur du pipeline meteo.

Exécute successivement town.py, weather.py avec cache résilient, puis
carto.py pour produire la visualisation Leaflet interactive.
"""

import os
import csv
import contextlib
import json
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import config
from gpx_export import export_simplified_gpx
from navigation import NAVIGATION_CSS, NAVIGATION_SCRIPT, render_navigation, render_route_links


class PipelineProgress:
    """Une barre unique, animée même pendant une requête réseau bloquante."""

    def __init__(self, total):
        self.total = max(1, total)
        self.completed = 0
        self.fraction = 0.0
        self.label = "Initialisation"
        self.started = time.monotonic()
        self.running = True
        self.tty = sys.stderr.isatty()
        self.stream = sys.__stderr__
        self.thread = None
        if self.tty:
            self.thread = threading.Thread(target=self._animate, daemon=True)
            self.thread.start()

    def _animate(self):
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        index = 0
        while self.running:
            width = 24
            current = self.completed + self.fraction
            filled = round(width * current / self.total)
            elapsed = int(time.monotonic() - self.started)
            line = (f"\r{frames[index % len(frames)]} [{'█' * filled}{'·' * (width-filled)}] "
                    f"{current:.1f}/{self.total} · {self.label} · {elapsed}s")
            self.stream.write(line[: max(40, shutil.get_terminal_size((100, 20)).columns - 1)])
            self.stream.write("\033[K")
            self.stream.flush()
            index += 1
            time.sleep(.15)

    def start(self, label):
        self.label = label
        self.fraction = 0.0
        if not self.tty:
            print(f"[{self.completed}/{self.total}] {label}", flush=True)

    def update(self, fraction, label=None):
        self.fraction = max(0.0, min(.99, float(fraction)))
        if label:
            self.label = label

    def done(self):
        self.completed = min(self.total, self.completed + 1)
        self.fraction = 0.0

    def close(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=.3)
            self.stream.write("\r" + " " * 120 + "\r")
            self.stream.flush()


PIPELINE_PROGRESS = None


def run_step(module_name, label):
    PIPELINE_PROGRESS.start(label)
    config.progress_callback = PIPELINE_PROGRESS.update
    module = __import__(module_name)
    os.makedirs(config.output_root, exist_ok=True)
    log_path = os.path.join(config.output_root, "pipeline.log")
    try:
        with open(log_path, "a", encoding="utf-8") as log, \
             contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            print(f"\n=== {label} ===", flush=True)
            module.main()
    except Exception:
        PIPELINE_PROGRESS.label = f"Échec : {label}"
        print(f"\nÉchec pendant : {label}\nDernières lignes de {log_path} :", file=sys.stderr)
        try:
            with open(log_path, encoding="utf-8") as log:
                print("".join(log.readlines()[-25:]), file=sys.stderr)
        except OSError:
            pass
        raise
    PIPELINE_PROGRESS.done()


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
        with open(config.csv_path, "r", encoding="utf-8", newline="") as handle:
            columns = next(csv.reader(handle), [])
            if not {"cloud_cover", "apparent_temperature"}.issubset(columns):
                return False
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


def build_version():
    return str(int(time.time()))


def write_service_worker(output_dir, root_path, version):
    """Génère un Service Worker propre à ce build, réellement network-first."""
    template = """const CACHE = 'gpx-weather-{version}';
const ROOT = '{root}';
const SHELL = [ROOT, `${{ROOT}}manifest.webmanifest`, `${{ROOT}}icon-192.png`, `${{ROOT}}icon-512.png`];

self.addEventListener('install', event => {{
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
  self.skipWaiting();
}});

self.addEventListener('activate', event => {{
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key !== CACHE).map(key => caches.delete(key))
  )));
  self.clients.claim();
}});

self.addEventListener('fetch', event => {{
  if (event.request.method !== 'GET' || new URL(event.request.url).origin !== self.location.origin) return;
  event.respondWith(fetch(event.request, {{cache: 'no-store'}}).then(response => {{
    const copy = response.clone();
    caches.open(CACHE).then(cache => cache.put(event.request, copy));
    return response;
  }}).catch(() => caches.match(event.request).then(response => response || caches.match(ROOT))));
}});
"""
    content = template.format(version=version, root=root_path)
    with open(os.path.join(output_dir, "sw.js"), "w", encoding="utf-8") as handle:
        handle.write(content)


def write_routes_index(routes):
    """Crée l'accueil Pages avec l'aide et la liste des parcours."""
    os.makedirs(config.output_root, exist_ok=True)
    for asset in ("manifest.webmanifest", "icon-192.png", "icon-512.png", "apple-touch-icon.png"):
        shutil.copy2(os.path.join(config.BASE_DIR, "webapp", asset), config.output_root)
    root_path = urlparse(config.github_pages_base_url).path.rstrip("/") + "/"
    write_service_worker(config.output_root, root_path, build_version())
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
if(new URLSearchParams(location.search).get('home')!=='1'){{const last=localStorage.getItem('gpx-weather-last-view');if(last&&last!==location.pathname)location.replace(last)}}
if('serviceWorker' in navigator)navigator.serviceWorker.register('sw.js');</script></body></html>'''
    with open(os.path.join(config.output_root, "index.html"), "w", encoding="utf-8") as handle:
        handle.write(html)

    # GitHub Pages renvoie ce document pour les URL dynamiques de planning.
    # Il recharge la page de la trace avec un paramètre transitoire ; carto.py
    # restaure ensuite l'URL /forecast/... dans la barre d'adresse.
    base_path = root_path
    slugs = json.dumps([slug for slug, _ in routes], ensure_ascii=False)
    fallback = f'''<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>GPX Weather</title></head>
<body><p>Chargement…</p><script>
const base={json.dumps(base_path)},slugs={slugs},relative=location.pathname.startsWith(base)?location.pathname.slice(base.length):'',parts=relative.split('/').filter(Boolean),slug=parts[0];
if(slugs.includes(slug)&&['forecast','forecast_details'].includes(parts[1])){{const token=parts[2]||'1',key=parts[1]==='forecast_details'?'forecast_details':'forecast';location.replace(`${{base}}${{slug}}/?${{key}}=${{encodeURIComponent(token)}}`)}}else location.replace(base+'?home=1');
</script></body></html>'''
    with open(os.path.join(config.output_root, "404.html"), "w", encoding="utf-8") as handle:
        handle.write(fallback)


def process_route(gpx_path):
    config.configure_route(gpx_path)
    PIPELINE_PROGRESS.start(f"{config.project} · simplification GPX")
    with open(os.path.join(config.output_root, "pipeline.log"), "a", encoding="utf-8") as log, \
         contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        export_simplified_gpx(
            config.gpx_file, config.production_gpx_path,
            interval_km=getattr(config, "production_gpx_interval_km", 1),
            name=config.project,
            elevation_interval_km=getattr(
                config, "production_gpx_elevation_interval_km", .5
            ),
        )
    PIPELINE_PROGRESS.done()

    towns_schema_current = False
    towns_code_is_newer = False
    if os.path.exists(config.towns_csv_path):
        towns_mtime = os.path.getmtime(config.towns_csv_path)
        towns_code_is_newer = any(
            os.path.getmtime(os.path.join(config.BASE_DIR, filename)) > towns_mtime
            for filename in ("town.py", "config.py")
        )
        with open(config.towns_csv_path, encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = set(reader.fieldnames or [])
            rows = list(reader)
            source_columns = {
                "wunderground_url", "meteociel_url", "lachainemeteo_url",
            }
            country_paths = {
                "FR": "/meteo-france/", "ES": "/meteo-espagne/",
                "IT": "/meteo-italie/", "CH": "/meteo-suisse/",
                "DE": "/meteo-allemagne/", "BE": "/meteo-belgique/",
                "PT": "/meteo-portugal/", "AD": "/meteo-andorre/",
                "GB": "/meteo-royaume-uni/", "SE": "/meteo-suede/",
            }
            def lachainemeteo_cache_valid(row):
                value = (row.get("lachainemeteo_url") or "").strip()
                if value == "-":
                    return True
                expected = country_paths.get((row.get("country_code") or "").upper())
                return bool(expected and expected in urlparse(value).path.casefold())
            towns_schema_current = {
                "elevation", "wunderground_url", "meteociel_url",
                "lachainemeteo_url", "country_code",
            } <= columns and all(
                (row.get(column) or "").strip()
                for row in rows for column in source_columns
            ) and all(lachainemeteo_cache_valid(row) for row in rows)
    if not towns_schema_current or towns_code_is_newer:
        run_step("town", f"{config.project} · sélection des villes")
    else:
        PIPELINE_PROGRESS.start(f"{config.project} · villes déjà à jour")
        PIPELINE_PROGRESS.done()

    cache_hours = getattr(config, "weather_cache_hours", 3)
    if not weather_cache_is_fresh(cache_hours):
        try:
            run_step("weather", f"{config.project} · prévisions météo")
        except Exception as exc:
            if not os.path.exists(config.csv_path):
                raise
            PIPELINE_PROGRESS.done()
            PIPELINE_PROGRESS.label = f"{config.project} · ancien cache météo conservé"
    else:
        PIPELINE_PROGRESS.start(f"{config.project} · météo déjà à jour")
        PIPELINE_PROGRESS.done()

    run_step("carto", f"{config.project} · génération de la carte")
    shutil.copy2(
        config.production_gpx_path,
        os.path.join(config.outdir, "trace.gpx"),
    )


def main():
    global PIPELINE_PROGRESS
    gpx_files = config.list_gpx_files()
    if not gpx_files:
        raise FileNotFoundError(
            f"Aucun fichier .gpx dans {config.source_gpx_dir} "
            f"ni dans {config.public_gpx_dir}"
        )
    os.makedirs(config.output_root, exist_ok=True)
    with open(os.path.join(config.output_root, "pipeline.log"), "w", encoding="utf-8"):
        pass
    PIPELINE_PROGRESS = PipelineProgress(len(gpx_files) * 4 + 1)
    routes = []
    try:
        for gpx_path in gpx_files:
            process_route(gpx_path)
            routes.append((config.route_slug, config.project))
        PIPELINE_PROGRESS.start("Finalisation du site")
        write_routes_index(routes)
        publish_pages()
        PIPELINE_PROGRESS.done()
    finally:
        PIPELINE_PROGRESS.close()
    print(f"Pipeline terminé · journal : {os.path.join(config.output_root, 'pipeline.log')}")


def render_web_only():
    """Reconstruit uniquement le site depuis les GPX et CSV déjà présents."""
    import carto

    gpx_files = config.list_gpx_files()
    if not gpx_files:
        raise FileNotFoundError(
            f"Aucun fichier .gpx dans {config.source_gpx_dir} "
            f"ni dans {config.public_gpx_dir}"
        )
    routes = []
    for index, gpx_path in enumerate(gpx_files, 1):
        config.configure_route(gpx_path)
        if not os.path.exists(config.csv_path):
            raise FileNotFoundError(
                f"Prévisions locales absentes pour {config.project} : "
                f"lancez ./run.sh une première fois."
            )
        print(f"[{index}/{len(gpx_files)}] Rendu web · {config.project}", flush=True)
        carto.main()
        routes.append((config.route_slug, config.project))
    write_routes_index(routes)
    print(f"Rendu web terminé : {config.output_root}")


if __name__ == "__main__":
    if "--web-only" in sys.argv[1:]:
        render_web_only()
    else:
        main()
