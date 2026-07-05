#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
town.py
-------

Identifie, le long d'une trace GPX :
  - la ville de depart
  - la ville d'arrivee
  - les villes-etapes intermediaires

REGLE UNIQUE, appliquee EXACTEMENT de la meme facon a TOUS ces points
(depart, etapes, arrivee) : on prend la commune la PLUS GRANDE (population)
a moins de config.endpoint_search_radius_km (10 km par defaut) du point
considere. Si aucune commune n'est trouvee dans ce rayon, on ne cherche
PAS plus loin : on passe simplement au point suivant (pas de repli sur une
grande ville lointaine).

Les points-etapes intermediaires sont positionnes a intervalle regulier le
long du trajet :
    Etape ideale = distance_totale_km / (trip_days - 1)
(trip_days - 1 = nombre de trajets journaliers entre le depart et
l'arrivee ; ex. 950 km / (10 - 1) jours = ~106 km/jour)

Sortie : un CSV (config.towns_csv_path) avec le nom, les coordonnees,
la population, le role (depart/etape/arrivee) et la distance parcourue.

IMPORTANT sur les distances : tous les calculs de distance (longueur totale
de la trace, position d'une ville le long du trajet) utilisent la formule
de haversine (distance reelle a la surface de la Terre), jamais une
projection cartographique de type Web Mercator. Web Mercator deforme les
distances (facteur ~1/cos(latitude), soit environ x1.44 a 46°N) : il est
parfait pour l'AFFICHAGE d'une carte (voir carto.py) mais totalement
impropre a la MESURE de distances.

Sources de donnees :
  - trace GPX : fichier local (config.gpx_file)
  - villes/population : Overpass API (OpenStreetMap), gratuit, sans cle
  - noms des villes de depart/arrivee : Nominatim (reverse geocoding),
    utilise seulement en tout dernier recours (si rien trouve via Overpass)

Installation des dependances :
    pip install gpxpy geopy requests pandas numpy

Usage :
    python town.py
"""

import os
import sys
import time

import numpy as np
import pandas as pd

try:
    import gpxpy
except ImportError:
    sys.exit("Le module 'gpxpy' est requis : pip install gpxpy")

try:
    from geopy.distance import geodesic
    from geopy.geocoders import Nominatim
except ImportError:
    sys.exit("Le module 'geopy' est requis : pip install geopy")

try:
    import requests
except ImportError:
    sys.exit("Le module 'requests' est requis : pip install requests")

import config


# Overpass exige un User-Agent explicite depuis 2024, sinon il renvoie une
# erreur 406. On fournit aussi un miroir de secours si le serveur principal
# est temporairement indisponible ou surcharge.
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OVERPASS_HEADERS = {
    "User-Agent": "town.py/1.0 (script de cartographie meteo GPX; contact: tc@tcrouzet.com)",
}


def overpass_query(query):
    """Envoie une requete Overpass, avec repli sur un miroir en cas d'echec.

    IMPORTANT : sur une requete couvrant une trop grande zone (ex: la bbox
    rectangulaire entiere d'une trace de 950 km, qui recouvre quasiment
    toute la France), Overpass peut retourner des milliers de noeuds et
    en omettre silencieusement certains (pas d'erreur, juste des villes
    manquantes dans le resultat). C'est pour cela que ce script n'utilise
    plus de requete bbox globale unique sur toute la trace : voir
    fetch_all_towns, qui regroupe en UNE requete l'union de bbox par
    grands segments de la trace (chacune elargie d'un buffer raisonnable),
    ce qui limite fortement le nombre de resultats et donc le risque de
    troncature, tout en restant une seule requete HTTP."""
    last_error = None
    for url in OVERPASS_URLS:
        try:
            resp = requests.post(
                url, data={"data": query}, headers=OVERPASS_HEADERS, timeout=120
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"  (echec sur {url} : {e})")
            last_error = e
    raise RuntimeError(f"Impossible de contacter un serveur Overpass valide : {last_error}")


def parse_towns_from_elements(elements):
    """Transforme les elements bruts renvoyes par Overpass en une liste de
    dicts {name, lat, lon, population}. population=0 si non renseignee
    dans OpenStreetMap (aucun filtre de seuil : le tri se fait plus tard)."""
    towns = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        pop_raw = tags.get("population")
        try:
            population = int(str(pop_raw).replace(" ", "")) if pop_raw else 0
        except ValueError:
            population = 0
        towns.append({
            "name": name,
            "lat": el["lat"],
            "lon": el["lon"],
            "population": population,
        })
    return towns

JUMP_WARNING_KM = 3.0  # au-dela, deux points consecutifs sont suspects
EARTH_RADIUS_KM = 6371.0088


# ---------------------------------------------------------------------------
# 1. Lecture de la trace GPX complete
# ---------------------------------------------------------------------------

def load_gpx_track(gpx_path):
    """Charge tous les points de la trace GPX (lat, lon), sans echantillonnage.

    Affiche un diagnostic sur le nombre de tracks/segments presents dans le
    fichier : un GPX avec plusieurs <trk> ou <trkseg> peut cacher des sauts
    geographiques (ex: plusieurs etapes non contigues) qui faussent le
    calcul de distance si on les met bout a bout sans precaution."""
    with open(gpx_path, "r", encoding="utf-8") as f:
        gpx = gpxpy.parse(f)

    points = []
    n_tracks = len(gpx.tracks)
    n_segments = sum(len(t.segments) for t in gpx.tracks)
    print(f"  -> GPX : {n_tracks} track(s), {n_segments} segment(s)")

    for ti, track in enumerate(gpx.tracks):
        for si, segment in enumerate(track.segments):
            seg_points = [(p.latitude, p.longitude) for p in segment.points]
            if points and seg_points:
                gap = geodesic(points[-1], seg_points[0]).km
                if gap > JUMP_WARNING_KM:
                    print(
                        f"  !! Saut suspect de {gap:.1f} km entre la fin du "
                        f"segment precedent et le debut de track {ti} / "
                        f"segment {si} (point {seg_points[0]})"
                    )
            points.extend(seg_points)

    if not points:
        for route in gpx.routes:
            for p in route.points:
                points.append((p.latitude, p.longitude))

    if not points:
        raise ValueError("Aucun point trouve dans le GPX.")

    return points


def haversine_km(lat1, lon1, lat2, lon2):
    """Distance haversine (grand cercle) en km. Fonctionne avec des scalaires
    ou des tableaux numpy (diffusion/broadcasting automatique)."""
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def build_track_arrays(points, warn=True):
    """Construit les tableaux numpy (lat, lon) de la trace ainsi que la
    distance cumulee reelle (haversine) en km a chaque point.

    Si warn=True, signale chaque saut entre deux points consecutifs
    superieur a JUMP_WARNING_KM : un saut de plusieurs dizaines/centaines
    de km entre deux points consecutifs d'une trace GPS est anormal."""
    lats = np.array([p[0] for p in points])
    lons = np.array([p[1] for p in points])

    step_km = haversine_km(lats[:-1], lons[:-1], lats[1:], lons[1:])
    cum_km = np.concatenate([[0.0], np.cumsum(step_km)])

    if warn:
        bad_idx = np.where(step_km > JUMP_WARNING_KM)[0]
        for i in bad_idx:
            print(
                f"  !! Saut de {step_km[i]:.1f} km entre le point {i} "
                f"({lats[i]:.5f},{lons[i]:.5f}) et le point {i+1} "
                f"({lats[i+1]:.5f},{lons[i+1]:.5f})"
            )
        if len(step_km):
            worst = int(np.argmax(step_km))
            if step_km[worst] > JUMP_WARNING_KM:
                print(
                    f"  -> Plus gros saut detecte : {step_km[worst]:.1f} km "
                    f"(entre les points {worst} et {worst + 1})"
                )

    return lats, lons, cum_km


def track_point_at_km(target_km, track_lats, track_lons, track_cum_km):
    """Renvoie (lat, lon) du point de la trace le plus proche de la
    distance cumulee `target_km` (ex: target_km=150 -> point situe a
    ~150 km parcourus depuis le depart)."""
    idx = int(np.argmin(np.abs(track_cum_km - target_km)))
    return float(track_lats[idx]), float(track_lons[idx])


# ---------------------------------------------------------------------------
# 2. Ville de depart / arrivee (reverse geocoding, dernier recours)
# ---------------------------------------------------------------------------

def get_endpoint_city(lat, lon, geolocator, label=""):
    """Trouve le nom de la commune la plus proche d'un point via Nominatim.
    Utilise seulement en tout dernier recours (voir find_nearby_large_city)."""
    try:
        loc = geolocator.reverse(
            (lat, lon), language="fr", exactly_one=True, timeout=10,
            addressdetails=1, zoom=10,
        )
        if loc is None:
            print(f"  (aucun resultat Nominatim pour {label} : {lat:.4f},{lon:.4f})")
            return None

        addr = loc.raw.get("address", {})
        name = (
            addr.get("city") or addr.get("town") or addr.get("village")
            or addr.get("municipality") or addr.get("hamlet")
            or addr.get("suburb") or addr.get("county") or addr.get("state")
        )
        if not name and loc.address:
            name = loc.address.split(",")[0].strip()

        if not name:
            print(f"  (nom introuvable pour {label} : reponse brute = {loc.raw})")
        return name
    except Exception as e:
        print(f"  (reverse-geocoding indisponible pour {label} : {e})")
    return None


# ---------------------------------------------------------------------------
# 3. Recuperation de TOUTES les communes le long de la trace (Overpass)
# ---------------------------------------------------------------------------

def build_coarse_bboxes(track_lats, track_lons, track_cum_km, buffer_km, segment_km=50):
    """Decoupe la trace en grands segments consecutifs (~segment_km) et
    calcule, pour chacun, une bbox (min/max lat/lon) elargie de buffer_km
    de chaque cote. Une seule requete Overpass regroupant toutes ces bbox
    suffit a couvrir toute la trace, sans envoyer une requete par point
    (trop lent) ni une bbox globale demesuree (qui recouvrirait presque
    toute la France et risquerait de faire omettre des communes par
    Overpass)."""
    total_km = float(track_cum_km[-1])
    edges = np.arange(0.0, total_km + segment_km, segment_km)
    bboxes = []
    for i in range(len(edges) - 1):
        mask = (track_cum_km >= edges[i]) & (track_cum_km <= edges[i + 1])
        if not np.any(mask):
            continue
        seg_lats = track_lats[mask]
        seg_lons = track_lons[mask]
        mean_lat = float(np.mean(seg_lats))
        dlat_deg = buffer_km / 111.0
        dlon_deg = buffer_km / (111.0 * max(np.cos(np.radians(mean_lat)), 0.1))
        south = float(seg_lats.min()) - dlat_deg
        north = float(seg_lats.max()) + dlat_deg
        west = float(seg_lons.min()) - dlon_deg
        east = float(seg_lons.max()) + dlon_deg
        bboxes.append((south, west, north, east))
    return bboxes


def fetch_all_towns(track_lats, track_lons, track_cum_km, buffer_km, segment_km=50):
    """UNE SEULE requete Overpass regroupant, en union, les bbox de chaque
    grand segment de la trace (chacune elargie de buffer_km) : recupere
    TOUTES les communes (place=city ou place=town) le long du trajet, avec
    leur population si renseignee dans OSM. Aucun filtre de population
    ici : le tri par taille se fait plus tard, au moment de choisir les
    etapes ou la ville de depart/arrivee."""
    bboxes = build_coarse_bboxes(track_lats, track_lons, track_cum_km, buffer_km, segment_km)
    clauses = "\n".join(
        f'  node["place"~"^(city|town)$"]({s:.5f},{w:.5f},{n:.5f},{e:.5f});'
        for s, w, n, e in bboxes
    )
    query = f"""
    [out:json][timeout:180];
    (
    {clauses}
    );
    out body;
    """
    print(f"  -> Requete Overpass en cours (1 seule requete, {len(bboxes)} segments "
          f"de ~{segment_km} km +/- {buffer_km} km, peut prendre quelques dizaines "
          f"de secondes)...")
    data = overpass_query(query)
    towns = parse_towns_from_elements(data.get("elements", []))
    print(f"  -> {len(towns)} communes trouvees le long de la trace")
    return towns


def load_or_fetch_all_towns(track_lats, track_lons, track_cum_km, buffer_km, cache_path,
                             segment_km=50):
    """Charge le cache all_towns.csv s'il existe deja (aucune requete
    Overpass n'est refaite dans ce cas) ; sinon interroge Overpass une
    seule fois et sauvegarde le resultat pour les prochaines executions.
    Supprime le fichier cache si tu changes de GPX ou de buffer_km."""
    if os.path.exists(cache_path):
        print(f"  -> Cache trouve : {cache_path} (pas de nouvelle requete Overpass ; "
              f"supprime ce fichier pour en forcer une)")
        df = pd.read_csv(cache_path)
        return df.to_dict("records")

    towns = fetch_all_towns(track_lats, track_lons, track_cum_km, buffer_km, segment_km)
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    pd.DataFrame(towns).to_csv(cache_path, index=False)
    print(f"  -> Cache sauvegarde : {cache_path}")
    return towns


# ---------------------------------------------------------------------------
# 4. REGLE UNIQUE de selection d'une ville a un point donne
# ---------------------------------------------------------------------------

def find_nearby_large_city(lat, lon, towns, radius_km):
    """Cherche la plus GRANDE commune (par population) a proximite REELLE
    du point donne (rayon STRICT radius_km, en km), parmi les communes
    deja recuperees (cache/Overpass). Utilise uniquement pour le fallback
    ponctuel du depart/de l'arrivee (voir main()) ; les etapes utilisent
    desormais project_towns_on_track (recherche le long de TOUT le
    corridor, pas juste au point exact)."""
    candidates = [
        t for t in towns
        if haversine_km(lat, lon, t["lat"], t["lon"]) <= radius_km
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda t: t["population"])


def project_towns_on_track(towns, track_lats, track_lons, track_cum_km, radius_km):
    """Pour CHAQUE commune candidate, calcule sa distance perpendiculaire
    reelle a la trace entiere (distance minimale a n'importe quel point de
    la trace, pas a un seul point cible) ainsi que sa position en km le
    long du trajet (km du point de la trace le plus proche).

    Ne garde que les communes dont cette distance a la trace est
    <= radius_km : ce sont les communes "sur le corridor du trajet",
    ou qu'elles se trouvent (pile a un intervalle ideal ou entre deux).

    C'est cette liste, triee par position en km, qui sert ensuite a
    choisir la ville la plus proche de chaque intervalle ideal (voir
    assign_stage_towns), au lieu de chercher seulement au point exact de
    l'intervalle."""
    on_track = []
    for t in towns:
        dists = haversine_km(t["lat"], t["lon"], track_lats, track_lons)
        idx = int(np.argmin(dists))
        min_dist = float(dists[idx])
        if min_dist <= radius_km:
            on_track.append({
                **t,
                "track_km": float(track_cum_km[idx]),
                "dist_to_track_km": min_dist,
            })
    on_track.sort(key=lambda t: t["track_km"])
    return on_track


def dedupe_urban_clusters(towns_on_track, cluster_radius_km):
    """Fusionne les communes qui appartiennent en realite a la MEME
    agglomeration (ex: Chamalieres est un quartier/une commune limitrophe
    de Clermont-Ferrand, a quelques km a peine) : on ne garde que la PLUS
    GRANDE commune de chaque groupe de communes mutuellement proches
    (<= cluster_radius_km a vol d'oiseau), pour eviter qu'une etape
    "juste apres" une grande ville ne re-selectionne en realite un
    quartier/une banlieue de cette meme ville comme si c'etait une
    etape distincte."""
    by_pop_desc = sorted(
        towns_on_track, key=lambda t: (t["population"] or 0), reverse=True
    )
    kept = []
    for t in by_pop_desc:
        too_close_to_bigger = any(
            haversine_km(t["lat"], t["lon"], k["lat"], k["lon"]) <= cluster_radius_km
            for k in kept
        )
        if not too_close_to_bigger:
            kept.append(t)
    kept.sort(key=lambda t: t["track_km"])
    return kept


def assign_stage_towns(targets, towns_on_track, max_deviation_km):
    """Pour chaque point cible (target_km, role), choisit -- parmi les
    communes deja projetees sur le trajet (project_towns_on_track) et pas
    encore utilisees, et dont l'ecart a l'intervalle ideal est
    <= max_deviation_km -- celle qui est REELLEMENT LA PLUS PROCHE DE LA
    TRACE (plus petite distance perpendiculaire dist_to_track_km).

    C'est le critere PRINCIPAL : une ville a 0.5 km de la trace doit
    toujours etre preferee a une ville a 8 km de la trace, meme si cette
    derniere tombe un peu plus pile sur l'intervalle ideal. L'ecart a
    l'intervalle (dev) ne sert qu'en depart-egalite (deux villes aussi
    proches l'une que l'autre de la trace) et de filtre grossier
    (max_deviation_km) pour eviter qu'une etape ne "vole" une ville en
    realite destinee a l'etape voisine.

    Si rien de convenable n'est trouve dans cette fenetre, l'etape est
    consideree comme vide (voir main())."""
    used_names = set()
    assigned = {}

    for target_km, role in targets:
        best = None
        best_key = None
        for t in towns_on_track:
            if t["name"] in used_names:
                continue
            dev = abs(t["track_km"] - target_km)
            if dev > max_deviation_km:
                continue
            key = (t["dist_to_track_km"], dev)
            if best is None or key < best_key:
                best, best_key = t, key
        if best is not None:
            used_names.add(best["name"])
        assigned[(target_km, role)] = best

    return assigned


# ---------------------------------------------------------------------------
# 5. Programme principal
# ---------------------------------------------------------------------------

def main():
    print(f"Lecture du GPX : {config.gpx_file}")
    points = load_gpx_track(config.gpx_file)
    print(f"  -> {len(points)} points dans la trace")

    track_lats, track_lons, track_cum_km = build_track_arrays(points)
    total_distance_km = float(track_cum_km[-1])
    print(f"  -> Distance totale (haversine, reelle) : {total_distance_km:.1f} km")

    print("\nRecherche de toutes les communes le long du trajet "
          "(1 seule requete Overpass, ou lecture du cache)...")
    raw_towns = load_or_fetch_all_towns(
        track_lats, track_lons, track_cum_km,
        config.town_search_buffer_km, config.all_towns_csv_path,
    )

    radius_km = config.endpoint_search_radius_km
    # (trip_days - 1) = nombre de "journees de route" separant le depart de
    # l'arrivee (10 jours de voyage -> 9 trajets journaliers). Sur 950 km /
    # 9 jours, ca fait bien ~106 km/jour, pas ~250 km comme avec l'ancien
    # "-2" (qui divisait par un nombre de jours trop petit).
    ideal_stage_km = total_distance_km / (config.trip_days - 1)
    n_stages = max(int(round(total_distance_km / ideal_stage_km)) - 1, 0)
    print(f"\nEtape ideale estimee : {ideal_stage_km:.1f} km "
          f"(distance totale {total_distance_km:.1f} km / "
          f"({config.trip_days}-1) jours) -> {n_stages} etape(s) intermediaire(s)")

    # Points cibles intermediaires (etapes) le long du trajet.
    stage_targets = [(ideal_stage_km * i, "etape") for i in range(1, n_stages + 1)]

    # Toutes les communes situees a moins de radius_km de la trace, OU
    # QU'ELLES SOIENT le long du trajet (pas seulement pile a un
    # intervalle ideal) : c'est la clef du correctif -- on cherche dans
    # tout le corridor du trajet, pas a un seul point precis. UNIQUEMENT
    # pour les etapes intermediaires : le depart et l'arrivee, eux, ne
    # doivent JAMAIS se deplacer le long de la trace (pas d'avance au
    # depart, pas de recul a l'arrivee) -- ils utilisent une recherche
    # stricte au point exact (voir plus bas, find_nearby_large_city).
    towns_on_track = project_towns_on_track(
        raw_towns, track_lats, track_lons, track_cum_km, radius_km
    )
    print(f"  -> {len(towns_on_track)} communes situees a moins de {radius_km} km "
          f"de la trace (corridor des etapes intermediaires uniquement)")

    # Fusion des communes d'une meme agglomeration (ex: Chamalieres /
    # Clermont-Ferrand) : on ne garde que la plus grande de chaque groupe,
    # sinon une "etape" peut re-selectionner un quartier/une banlieue de la
    # grande ville juste choisie a l'intervalle precedent.
    urban_cluster_radius_km = getattr(config, "urban_cluster_radius_km", 8)
    towns_on_track = dedupe_urban_clusters(towns_on_track, urban_cluster_radius_km)
    print(f"  -> {len(towns_on_track)} communes apres fusion des agglomerations "
          f"(rayon {urban_cluster_radius_km} km, on garde la plus grande de "
          f"chaque groupe)")

    # Ecart maximum tolere entre la position reelle d'une ville-etape et son
    # intervalle ideal : une ville plus eloignee de son intervalle que la
    # moitie d'une etape appartient plutot a l'etape voisine.
    max_deviation_km = ideal_stage_km / 2.0
    stage_assignments = assign_stage_towns(stage_targets, towns_on_track, max_deviation_km)

    geolocator = None  # instancie seulement si necessaire (fallback Nominatim)
    rows = []
    used_names = set()

    print(f"\nDepart/arrivee : recherche STRICTE au point exact (rayon "
          f"{radius_km} km, aucun deplacement le long de la trace). "
          f"Etapes intermediaires : ville la plus proche de la trace dans "
          f"un rayon de {radius_km} km, avec un ecart max de "
          f"{max_deviation_km:.1f} km par rapport a l'intervalle ideal...")

    targets = [(0.0, "depart")] + stage_targets + [(total_distance_km, "arrivee")]

    for target_km, role in targets:
        lat, lon = track_point_at_km(target_km, track_lats, track_lons, track_cum_km)

        if role in ("depart", "arrivee"):
            # Recherche STRICTE au point exact (depart/arrivee ne doivent
            # jamais se deplacer le long de la trace, meme de quelques km).
            town = find_nearby_large_city(lat, lon, raw_towns, radius_km)
            if town is not None and town["name"] in used_names:
                town = None
        else:
            town = stage_assignments[(target_km, role)]
            if town is not None and town["name"] in used_names:
                # Deja pris comme depart (rare, mais possible si le depart
                # tombe dans la fenetre d'une etape) : on ne le duplique pas.
                town = None

        if town is None and role in ("depart", "arrivee"):
            # Dernier recours pour le depart/l'arrivee uniquement (on veut
            # toujours un nom a ces deux points) : Nominatim, meme si la
            # "ville" trouvee est en realite un hameau/village minuscule.
            if geolocator is None:
                geolocator = Nominatim(user_agent="town_tcrouzet")
            name = get_endpoint_city(lat, lon, geolocator, label=role)
            time.sleep(1.1)
            if name:
                town = {"name": name, "lat": lat, "lon": lon, "population": None}

        if town is None:
            if role == "etape":
                print(f"  (rien a moins de {radius_km} km du km {target_km:.0f} : "
                      f"etape ignoree, on continue)")
                continue
            # depart/arrivee : vraiment rien trouve, meme via Nominatim
            town = {"name": "Depart" if role == "depart" else "Arrivee",
                    "lat": lat, "lon": lon, "population": None}

        used_names.add(town["name"])

        # Distance parcourue = position REELLE de la ville trouvee le long
        # de la trace (point de la trace le plus proche de la ville), pas
        # le point cible utilise pour la chercher : plus precis pour la
        # meteo/l'affichage.
        dists_to_track = haversine_km(town["lat"], town["lon"], track_lats, track_lons)
        nearest_idx = int(np.argmin(dists_to_track))
        actual_km = 0.0 if role == "depart" else (
            total_distance_km if role == "arrivee" else float(track_cum_km[nearest_idx])
        )

        rows.append({
            "name": town["name"],
            "lat": town["lat"],
            "lon": town["lon"],
            "population": town.get("population"),
            "distance_km": round(actual_km, 1),
            "role": role,
        })

    df = pd.DataFrame(rows).sort_values("distance_km").reset_index(drop=True)

    os.makedirs(config.outdir, exist_ok=True)
    df.to_csv(config.towns_csv_path, index=False)

    print(f"\nResultat :\n{df.to_string(index=False)}")
    print(f"\nCSV sauvegarde : {config.towns_csv_path}")


if __name__ == "__main__":
    main()
