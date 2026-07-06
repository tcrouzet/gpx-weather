#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weather.py
----------

Recupere les previsions meteo (Open-Meteo, aussi loin que possible dans le
temps) UNIQUEMENT pour les villes selectionnees par town.py (config.towns_csv_path,
ex. villes.csv : depart / etapes / arrivee), au lieu d'un echantillonnage brut
tous les step_km de la trace GPX.

Sortie : le meme CSV que precedemment (config.csv_path, previsions_brutes.csv),
avec en plus les colonnes name/role issues de villes.csv, pour que
meteo_carto.py puisse afficher directement le nom de chaque ville sur la carte.

Pre-requis : avoir deja lance town.py (le fichier config.towns_csv_path doit
exister).

Toute la configuration se trouve dans config.py.

Installation des dependances :
    pip install pandas openmeteo-requests requests-cache retry-requests

Usage :
    python weather.py
"""

import os
import sys
import json

import numpy as np
import pandas as pd

try:
    import openmeteo_requests
    import requests_cache
    from retry_requests import retry
except ImportError:
    sys.exit(
        "Les modules 'openmeteo-requests', 'requests-cache' et "
        "'retry-requests' sont requis :\n"
        "pip install openmeteo-requests requests-cache retry-requests"
    )

import config


# ---------------------------------------------------------------------------
# 1. Lecture des villes selectionnees (sortie de town.py)
# ---------------------------------------------------------------------------

def load_towns(towns_csv_path):
    """Charge le CSV des villes produit par town.py (name, lat, lon,
    population, distance_km, role)."""
    if not os.path.exists(towns_csv_path):
        sys.exit(
            f"Le fichier {towns_csv_path} n'existe pas : lance d'abord "
            f"'python town.py' pour generer la liste des villes."
        )

    df = pd.read_csv(towns_csv_path)
    required = {"name", "lat", "lon", "distance_km", "role"}
    missing = required - set(df.columns)
    if missing:
        sys.exit(f"Colonnes manquantes dans {towns_csv_path} : {missing}")

    df = df.sort_values("distance_km").reset_index(drop=True)
    return df.to_dict("records")


# ---------------------------------------------------------------------------
# 2. Recuperation des previsions meteo (Open-Meteo)
# ---------------------------------------------------------------------------

def build_openmeteo_client():
    """Client Open-Meteo avec cache disque (1h) et retry automatique."""
    cache_session = requests_cache.CachedSession(".cache_meteo", expire_after=3600)
    retry_session = retry(
        cache_session,
        retries=5,
        backoff_factor=0.8,
        status_to_retry=(429, 500, 502, 503, 504),
    )
    return openmeteo_requests.Client(session=retry_session)


def build_http_session():
    """Session JSON partageant le meme cache et la meme politique de retry."""
    cache_session = requests_cache.CachedSession(".cache_meteo", expire_after=3600)
    return retry(
        cache_session,
        retries=5,
        backoff_factor=0.8,
        status_to_retry=(429, 500, 502, 503, 504),
    )


def get_forecast_for_point(client, lat, lon, forecast_days=16):
    """Recupere la prevision horaire pour un point donne, aussi loin que
    possible dans le temps (jusqu'a forecast_days, 16 jours max chez
    Open-Meteo).

    models="best_match" : Open-Meteo choisit automatiquement le meilleur
    modele disponible (AROME haute resolution a court terme, puis
    ARPEGE / GFS / ECMWF au-dela, selon la region et l'horizon)."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": [
            "temperature_2m",
            "precipitation",
            "wind_speed_10m",
            "wind_gusts_10m",
            "weather_code",
            "wind_direction_10m",
        ],
        "forecast_days": forecast_days,
        "models": "best_match",
        "timezone": "auto",
    }

    responses = client.weather_api(url, params=params)
    resp = responses[0]
    hourly = resp.Hourly()

    times = pd.date_range(
        start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
        end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
        freq=pd.Timedelta(seconds=hourly.Interval()),
        inclusive="left",
    )

    df = pd.DataFrame(
        {
            "time": times,
            "temperature": hourly.Variables(0).ValuesAsNumpy(),
            "precipitation": hourly.Variables(1).ValuesAsNumpy(),
            "wind_speed": hourly.Variables(2).ValuesAsNumpy(),
            "wind_gusts": hourly.Variables(3).ValuesAsNumpy(),
            "weather_code": hourly.Variables(4).ValuesAsNumpy(),
            "wind_direction": hourly.Variables(5).ValuesAsNumpy(),
        }
    )
    df["lat"] = lat
    df["lon"] = lon

    # Open-Meteo renvoie l'historique horaire depuis minuit (heure locale) du
    # jour courant, ce qui inclut des heures deja passees. On ne garde que
    # les echeances a venir (>= maintenant), sinon carto.py afficherait des
    # dates/heures deja ecoulees.
    now_utc = pd.Timestamp.now(tz="UTC")
    df = df[df["time"] >= now_utc].reset_index(drop=True)

    return df


def get_ecmwf_ensemble_for_point(session, lat, lon, forecast_days=15):
    """Agrège les 51 membres ECMWF IFS ENS en médiane, intervalle 10–90 %
    et probabilité de précipitations. Un scénario unique à J+10/J+15 donne
    une fausse précision ; ces statistiques rendent l'incertitude explicite.
    """
    url = "https://ensemble-api.open-meteo.com/v1/ensemble"
    variables = [
        "temperature_2m", "precipitation", "weather_code",
        "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m",
    ]
    response = session.get(
        url,
        params={
            "latitude": lat,
            "longitude": lon,
            "hourly": ",".join(variables),
            "forecast_days": min(forecast_days, 15),
            "models": "ecmwf_ifs025",
            "timezone": "UTC",
        },
        timeout=60,
    )
    response.raise_for_status()
    hourly = response.json()["hourly"]
    result = pd.DataFrame({"time": pd.to_datetime(hourly["time"], utc=True)})

    def member_matrix(variable):
        keys = [key for key in hourly if key == variable or key.startswith(variable + "_member")]
        if not keys:
            raise ValueError(f"Variable ECMWF absente : {variable}")
        return np.asarray([hourly[key] for key in keys], dtype=float).T

    temperature = member_matrix("temperature_2m")
    precipitation = member_matrix("precipitation")
    weather_codes = member_matrix("weather_code")
    wind = member_matrix("wind_speed_10m")
    gusts = member_matrix("wind_gusts_10m")
    wind_direction = member_matrix("wind_direction_10m")

    # L'API peut completer la fin de la plage demandee avec des lignes dont
    # tous les membres sont absents. Elles ne constituent pas une prevision
    # et doivent etre retirees avant les aggregations et le calcul du mode.
    valid = np.isfinite(temperature).any(axis=1)
    result = result.loc[valid].reset_index(drop=True)
    temperature = temperature[valid]
    precipitation = precipitation[valid]
    weather_codes = weather_codes[valid]
    wind = wind[valid]
    gusts = gusts[valid]
    wind_direction = wind_direction[valid]

    result["temperature"] = np.nanmedian(temperature, axis=1)
    result["temperature_low"] = np.nanquantile(temperature, .10, axis=1)
    result["temperature_high"] = np.nanquantile(temperature, .90, axis=1)
    # Pour les precipitations, la mediane vaut souvent 0 mm des que moins de
    # la moitie des membres prevoient de la pluie. La moyenne d'ensemble est
    # plus informative et reste coherente avec la probabilite affichee.
    precipitation_count = np.isfinite(precipitation).sum(axis=1)
    result["precipitation"] = np.divide(
        np.nansum(precipitation, axis=1), precipitation_count,
        out=np.zeros(len(precipitation)), where=precipitation_count > 0,
    )
    rainy_count = np.sum(np.isfinite(precipitation) & (precipitation >= .1), axis=1)
    result["precipitation_probability"] = np.divide(
        rainy_count * 100.0, precipitation_count,
        out=np.zeros(len(precipitation)), where=precipitation_count > 0,
    )

    def median_or_zero(matrix):
        return np.array([
            np.nanmedian(row) if np.isfinite(row).any() else 0.0 for row in matrix
        ])

    result["wind_speed"] = median_or_zero(wind)
    result["wind_gusts"] = median_or_zero(gusts)

    def circular_mean_degrees(matrix):
        values = []
        for row in matrix:
            row = row[np.isfinite(row)]
            if not len(row):
                values.append(np.nan)
                continue
            radians = np.radians(row)
            angle = np.degrees(np.arctan2(np.mean(np.sin(radians)), np.mean(np.cos(radians))))
            values.append(angle % 360)
        return np.array(values)

    result["wind_direction"] = circular_mean_degrees(wind_direction)
    # Code majoritaire parmi les 51 scénarios, uniquement pour le pictogramme.
    def modal_weather_code(row):
        modes = pd.Series(row).dropna().astype(int).mode()
        return modes.iloc[0] if not modes.empty else np.nan

    result["weather_code"] = [modal_weather_code(row) for row in weather_codes]
    result["data_source"] = "ecmwf_ifs_ensemble"
    result["lat"], result["lon"] = lat, lon
    return result


def fetch_all_forecasts(client, ensemble_session, towns, forecast_days=16, ensemble_after_days=5):
    """Recupere les previsions pour chaque ville selectionnee (villes.csv),
    dans l'ordre de la distance parcourue."""
    frames = []
    for i, town in enumerate(towns):
        print(
            f"  -> Ville {i + 1}/{len(towns)} : {town['name']} "
            f"({town['role']}, km {town['distance_km']}) "
            f"lat={town['lat']:.4f}, lon={town['lon']:.4f}"
        )
        deterministic = get_forecast_for_point(client, town["lat"], town["lon"], forecast_days)
        deterministic["temperature_low"] = deterministic["temperature"]
        deterministic["temperature_high"] = deterministic["temperature"]
        deterministic["precipitation_probability"] = np.where(
            deterministic["precipitation"] >= .1, 100.0, 0.0
        )
        deterministic["data_source"] = "best_match"

        ensemble = get_ecmwf_ensemble_for_point(
            ensemble_session, town["lat"], town["lon"], min(forecast_days, 15)
        )
        cutoff = pd.Timestamp.now(tz="UTC").normalize() + pd.Timedelta(days=ensemble_after_days)
        df = pd.concat(
            [deterministic[deterministic["time"] < cutoff], ensemble[ensemble["time"] >= cutoff]],
            ignore_index=True,
        ).sort_values("time").drop_duplicates("time", keep="last")
        df["point_index"] = i
        df["name"] = town["name"]
        df["role"] = town["role"]
        df["distance_km"] = town["distance_km"]
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# 3. Programme principal
# ---------------------------------------------------------------------------

def main():
    print(f"Lecture des villes selectionnees : {config.towns_csv_path}")
    towns = load_towns(config.towns_csv_path)
    print(f"  -> {len(towns)} villes ({', '.join(t['name'] for t in towns)})")

    print(f"\nRecuperation des previsions meteo (Open-Meteo, best_match, "
          f"{config.forecast_days} jours max)...")
    client = build_openmeteo_client()
    ensemble_session = build_http_session()
    all_data = fetch_all_forecasts(
        client,
        ensemble_session,
        towns,
        forecast_days=config.forecast_days,
        ensemble_after_days=getattr(config, "ensemble_after_days", 5),
    )
    all_data["fetched_at_utc"] = pd.Timestamp.now(tz="UTC").isoformat()

    os.makedirs(config.outdir, exist_ok=True)
    # Ecriture atomique : une interruption ne peut jamais detruire le dernier
    # cache valide. Le fichier de metadonnees n'est remplace qu'apres le CSV.
    csv_tmp = config.csv_path + ".tmp"
    meta_tmp = config.weather_cache_meta_path + ".tmp"
    all_data.to_csv(csv_tmp, index=False)
    os.replace(csv_tmp, config.csv_path)
    with open(meta_tmp, "w", encoding="utf-8") as handle:
        json.dump({
            "fetched_at_utc": all_data["fetched_at_utc"].iloc[0],
            "rows": len(all_data),
            "forecast_days": config.forecast_days,
            "model": "best_match puis ecmwf_ifs_ensemble",
        }, handle, ensure_ascii=False, indent=2)
    os.replace(meta_tmp, config.weather_cache_meta_path)
    print(f"\nDonnees brutes sauvegardees : {config.csv_path}")


if __name__ == "__main__":
    main()
