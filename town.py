#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
town.py
-------

Identifie, le long d'une trace GPX :
  - la ville de depart
  - la ville d'arrivee
  - les villes-etapes intermediaires

Pour les extremites, le nom vient de la commune administrative qui contient
le point GPX (reverse geocoding Nominatim) : aucune grande ville voisine ne
peut ainsi remplacer la vraie commune de depart ou d'arrivee. Les villes-etapes
intermediaires sont choisies dans le corridor de la trace.

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
  - noms des villes de depart/arrivee : Nominatim (reverse geocoding)

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
    """Retourne la commune administrative contenant exactement le point.

    Le reverse geocoding est prioritaire aux extremites : choisir la plus
    grande ville dans un rayon peut remonter une autre zone urbaine.
    """
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
            or addr.get("municipality")
        )

        if not name:
            print(f"  (nom introuvable pour {label} : reponse brute = {loc.raw})")
        return name
    except Exception as e:
        print(f"  (reverse-geocoding indisponible pour {label} : {e})")
    return None


def get_nearest_endpoint_place(lat, lon, towns, radius_km):
    """Repli local strict lorsque Nominatim est indisponible."""
    candidates = [
        (float(haversine_km(lat, lon, town["lat"], town["lon"])), town)
        for town in towns
    ]
    if not candidates:
        return None
    distance, town = min(candidates, key=lambda item: item[0])
    return town["name"] if distance <= radius_km else None


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
    TOUTES les communes (place=city, town ou village) le long du trajet, avec
    leur population si renseignee dans OSM. Aucun filtre de population
    ici : le tri par taille se fait plus tard, au moment de choisir les
    etapes ou la ville de depart/arrivee."""
    bboxes = build_coarse_bboxes(track_lats, track_lons, track_cum_km, buffer_km, segment_km)
    clauses = "\n".join(
        f'  node["place"~"^(city|town|village)$"]({s:.5f},{w:.5f},{n:.5f},{e:.5f});'
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
# 4. Selection des villes-etapes
# ---------------------------------------------------------------------------


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


def select_planning_towns(towns_on_track, total_distance_km, interval_km):
    """Construit un maillage régulier de communes pour les heures de passage.

    À chaque multiple de ``interval_km``, conserve la commune dont la position
    le long de la trace est la plus proche. Les villages très voisins ne sont
    donc pas tous interrogés, mais aucun grand vide artificiel ne subsiste.
    """
    selected = []
    used_names = set()
    targets = np.arange(0, total_distance_km + interval_km, interval_km)
    for target in targets:
        candidates = [town for town in towns_on_track if town["name"] not in used_names]
        if not candidates:
            break
        best = min(candidates, key=lambda town: (
            abs(town["track_km"] - target), town["dist_to_track_km"]
        ))
        if abs(best["track_km"] - target) <= interval_km:
            selected.append(best)
            used_names.add(best["name"])
    selected.sort(key=lambda town: town["track_km"])
    return selected


def assign_stage_towns(targets, towns_on_track, max_deviation_km,
                       minimum_distance_km=0, fixed_points=(),
                       max_assignments=None):
    """Optimise globalement les villes principales de la trace.

    Le score privilégie successivement le nombre de villes compatibles,
    l'équilibre entre aller et retour, la population, puis la proximité de
    la trace et de l'intervalle cible. Une grande ville structurante comme
    Girona ne peut donc pas être évincée par une petite commune équivalente.
    """
    candidate_sets = []
    for target_km, _ in targets:
        candidates = []
        for town in towns_on_track:
            if abs(town["track_km"] - target_km) > max_deviation_km:
                continue
            if any(haversine_km(town["lat"], town["lon"], lat, lon)
                   < minimum_distance_km for lat, lon in fixed_points):
                continue
            candidates.append(town)
        candidate_sets.append(candidates)

    midpoint = (targets[0][0] + targets[-1][0]) / 2 if targets else 0
    best_score = None
    best_assignment = [None] * len(targets)

    def search(index, selected, assignment):
        nonlocal best_score, best_assignment
        if index == len(targets):
            early = sum(town["track_km"] < midpoint for town in selected)
            late = len(selected) - early
            quality = sum(
                town["dist_to_track_km"] * 100
                + abs(town["track_km"] - targets[position][0])
                for position, town in enumerate(assignment) if town is not None
            )
            population = sum(float(town.get("population") or 0) for town in selected)
            score = (len(selected), min(early, late), -abs(early - late),
                     population, -quality)
            if best_score is None or score > best_score:
                best_score, best_assignment = score, list(assignment)
            return
        if best_score is not None and len(selected) + len(targets) - index < best_score[0]:
            return
        if max_assignments is None or len(selected) < max_assignments:
            for town in candidate_sets[index]:
                if any(
                    town["name"] == other["name"]
                    or haversine_km(town["lat"], town["lon"], other["lat"], other["lon"])
                    < minimum_distance_km
                    for other in selected
                ):
                    continue
                search(index + 1, selected + [town], assignment + [town])
        search(index + 1, selected, assignment + [None])

    search(0, [], [])
    return {target: town for target, town in zip(targets, best_assignment)}


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
    route_legs = max(config.trip_days - 1, 1)
    ideal_stage_km = total_distance_km / route_legs
    minimum_city_distance_km = total_distance_km / max(
        getattr(config, "city_spacing_divisor", 12), 1
    )
    # Il y a au maximum une ville entre deux troncons : avec 8 jours,
    # 7 troncons donnent donc au plus 6 villes-etapes.
    n_stages = max(route_legs - 1, 0)
    print(f"\nEtape ideale estimee : {ideal_stage_km:.1f} km "
          f"(distance totale {total_distance_km:.1f} km / "
          f"({config.trip_days}-1) jours) -> {n_stages} etape(s) intermediaire(s)")

    # Toutes les communes situees a moins de radius_km de la trace, OU
    # QU'ELLES SOIENT le long du trajet (pas seulement pile a un
    # intervalle ideal) : c'est la clef du correctif -- on cherche dans
    # tout le corridor du trajet, pas a un seul point precis. UNIQUEMENT
    # pour les etapes intermediaires : le depart et l'arrivee, eux, ne
    # doivent JAMAIS se deplacer le long de la trace (pas d'avance au
    # depart, pas de recul a l'arrivee) -- ils utilisent la commune
    # administrative du point exact via Nominatim (voir plus bas).
    planner_towns_on_track = project_towns_on_track(
        raw_towns, track_lats, track_lons, track_cum_km, radius_km
    )
    print(f"  -> {len(planner_towns_on_track)} communes situees a moins de {radius_km} km "
          "de la trace (corridor des etapes uniquement)")

    # Fusion des communes d'une meme agglomeration (ex: Chamalieres /
    # Clermont-Ferrand) : on ne garde que la plus grande de chaque groupe,
    # sinon une "etape" peut re-selectionner un quartier/une banlieue de la
    # grande ville juste choisie a l'intervalle precedent.
    urban_cluster_radius_km = getattr(config, "urban_cluster_radius_km", 8)
    towns_on_track = dedupe_urban_clusters(
        planner_towns_on_track, urban_cluster_radius_km
    )
    print(f"  -> {len(towns_on_track)} communes apres fusion des agglomerations "
          f"(rayon {urban_cluster_radius_km} km, on garde la plus grande de "
          f"chaque groupe)")

    # Ecart maximum tolere entre la position reelle d'une ville-etape et son
    # intervalle ideal : une ville plus eloignee de son intervalle que la
    # moitie d'une etape appartient plutot a l'etape voisine.
    max_deviation_km = ideal_stage_km / 2.0
    endpoint_points = [
        track_point_at_km(0, track_lats, track_lons, track_cum_km),
        track_point_at_km(total_distance_km, track_lats, track_lons, track_cum_km),
    ]
    quarter = total_distance_km / 4
    anchor_candidates = [
        town for town in towns_on_track
        if quarter <= town["track_km"] <= total_distance_km - quarter
        and all(haversine_km(town["lat"], town["lon"], lat, lon)
                >= minimum_city_distance_km for lat, lon in endpoint_points)
    ]
    anchor = max(anchor_candidates, key=lambda town: (
        float(town.get("population") or 0), -town["dist_to_track_km"]
    )) if anchor_candidates else None

    if anchor is not None and n_stages:
        remaining = n_stages - 1
        left_slots = remaining // 2
        right_slots = remaining - left_slots
        # Une cible supplémentaire de chaque côté donne plus de choix à
        # l'optimiseur avant qu'il ne ramène le total à n_stages.
        left_candidates = left_slots + 1
        right_candidates = right_slots + 1
        left_targets = [
            (anchor["track_km"] * i / (left_candidates + 1), "etape")
            for i in range(1, left_candidates + 1)
        ]
        right_targets = [
            (anchor["track_km"] + (total_distance_km - anchor["track_km"])
             * i / (right_candidates + 1), "etape")
            for i in range(1, right_candidates + 1)
        ]
        other_targets = left_targets + right_targets
        stage_assignments = assign_stage_towns(
            other_targets, towns_on_track, max_deviation_km,
            minimum_distance_km=minimum_city_distance_km,
            fixed_points=endpoint_points + [(anchor["lat"], anchor["lon"])],
            max_assignments=remaining,
        )
        anchor_target = (anchor["track_km"], "etape")
        stage_targets = sorted(other_targets + [anchor_target])
        stage_assignments[anchor_target] = anchor
        print(f"  -> Ville-ancre : {anchor['name']} ({int(anchor.get('population') or 0)} "
              f"habitants, km {anchor['track_km']:.1f}) ; {left_candidates} cible(s) "
              f"avant et {right_candidates} apres, puis {remaining} retenue(s)")
    else:
        stage_targets = [(ideal_stage_km * i, "etape") for i in range(1, n_stages + 1)]
        stage_assignments = assign_stage_towns(
            stage_targets, towns_on_track, max_deviation_km,
            minimum_distance_km=minimum_city_distance_km,
            fixed_points=endpoint_points,
        )

    geolocator = Nominatim(user_agent="town_tcrouzet")
    endpoint_cache = {}
    rows = []
    used_names = set()

    print("\nDepart/arrivee : commune administrative contenant exactement "
          "le point GPX (aucune recherche de ville dans un rayon). "
          f"Etapes intermediaires : ville la plus proche de la trace dans "
          f"un rayon de {radius_km} km, avec un ecart max de "
          f"{max_deviation_km:.1f} km par rapport a l'intervalle ideal et "
          f"au moins {minimum_city_distance_km:.1f} km a vol d'oiseau "
          "entre villes...")

    targets = [(0.0, "depart")] + stage_targets + [(total_distance_km, "arrivee")]

    for target_km, role in targets:
        lat, lon = track_point_at_km(target_km, track_lats, track_lons, track_cum_km)

        if role in ("depart", "arrivee"):
            # Une boucle reutilise la reponse du depart si ses extremites
            # sont a moins d'une centaine de metres l'une de l'autre.
            endpoint_key = (round(float(lat), 3), round(float(lon), 3))
            name = endpoint_cache.get(endpoint_key)
            if name is None:
                name = get_endpoint_city(lat, lon, geolocator, label=role)
                if name is None:
                    name = get_nearest_endpoint_place(
                        lat, lon, raw_towns,
                        getattr(config, "endpoint_fallback_radius_km", 2),
                    )
                if name:
                    endpoint_cache[endpoint_key] = name
                time.sleep(1.1)
            town = ({"name": name, "lat": lat, "lon": lon, "population": None}
                    if name else None)
        else:
            town = stage_assignments[(target_km, role)]
            if town is not None and town["name"] in used_names:
                # Deja pris comme depart (rare, mais possible si le depart
                # tombe dans la fenetre d'une etape) : on ne le duplique pas.
                town = None

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

    # Une boucle dont les extrémités sont dans la même zone ne doit pas
    # afficher deux marqueurs superposés. Un seul point représente alors
    # simultanément le départ et l'arrivée.
    endpoint_rows = [row for row in rows if row["role"] in ("depart", "arrivee")]
    if len(endpoint_rows) == 2 and haversine_km(
        endpoint_rows[0]["lat"], endpoint_rows[0]["lon"],
        endpoint_rows[1]["lat"], endpoint_rows[1]["lon"],
    ) < minimum_city_distance_km:
        departure, arrival = endpoint_rows
        departure["role"] = "depart/arrivee"
        if departure["name"] != arrival["name"]:
            departure["name"] = f'{departure["name"]} / {arrival["name"]}'
        rows.remove(arrival)

    # Les communes secondaires alimentent le calcul du planning mais ne sont
    # pas affichées comme marqueurs sur la carte principale.
    selected_names = {row["name"] for row in rows}
    planning_towns = select_planning_towns(
        planner_towns_on_track, total_distance_km,
        getattr(config, "planning_city_interval_km", 25),
    )
    weather_interval = getattr(config, "planning_weather_interval_km", 100)
    weather_checkpoint_names = {
        min(planner_towns_on_track, key=lambda town: abs(town["track_km"] - target))["name"]
        for target in np.arange(weather_interval, total_distance_km, weather_interval)
    }
    planning_by_name = {town["name"]: town for town in planning_towns}
    for town in planner_towns_on_track:
        if town["name"] in weather_checkpoint_names:
            planning_by_name[town["name"]] = town
    planning_towns = sorted(planning_by_name.values(), key=lambda town: town["track_km"])
    for town in planning_towns:
        if town["name"] in selected_names:
            continue
        rows.append({
            "name": town["name"], "lat": town["lat"], "lon": town["lon"],
            "population": town.get("population"),
            "distance_km": round(float(town["track_km"]), 1),
            "role": ("meteo" if town["name"] in weather_checkpoint_names
                     else "planning"),
        })

    df = pd.DataFrame(rows).sort_values("distance_km").reset_index(drop=True)

    os.makedirs(config.outdir, exist_ok=True)
    df.to_csv(config.towns_csv_path, index=False)

    print(f"\nResultat :\n{df.to_string(index=False)}")
    print(f"\nCSV sauvegarde : {config.towns_csv_path}")


if __name__ == "__main__":
    main()
