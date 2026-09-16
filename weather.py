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
from datetime import datetime
from zoneinfo import ZoneInfo

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


def report_progress(fraction, label):
    callback = getattr(config, "progress_callback", None)
    if callback:
        callback(fraction, f"{config.project} · {label}")


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
    if "elevation" not in df.columns:
        df["elevation"] = None

    # Les lignes "planning" servent uniquement à nommer précisément les
    # lieux de passage. Leur météo est celle du point principal le plus proche.
    df = df[df["role"] != "planning"].sort_values("distance_km").reset_index(drop=True)
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


def active_window_start_utc():
    """Début UTC de la fenêtre météo active côté affichage."""
    now = pd.Timestamp(datetime.now(ZoneInfo("Europe/Paris"))).floor("h")
    sample_hours = sorted({int(hour) % 24 for hour in config.sample_hours})
    active_hour = max((hour for hour in sample_hours if hour <= now.hour), default=sample_hours[-1])
    active_day = now.normalize()
    if active_hour > now.hour:
        active_day -= pd.Timedelta(days=1)
    return (active_day + pd.Timedelta(hours=active_hour)).tz_convert("UTC")


def get_forecast_for_point(client, lat, lon, elevation=None, forecast_days=16,
                           model="meteofrance_seamless"):
    """Recupere la prevision horaire pour un point donne, aussi loin que
    possible dans le temps (jusqu'a forecast_days, 16 jours max chez
    Open-Meteo).

    Le modèle Météo-France fusionné (AROME + ARPEGE) est utilisé à courte
    échéance. ``best_match`` n'est demandé qu'en repli lorsqu'il manque des
    données avant la bascule vers l'ensemble ECMWF."""
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
        "models": model,
        "timezone": "auto",
    }
    if elevation is not None and pd.notna(elevation):
        params["elevation"] = float(elevation)

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
    # jour courant. On garde la fenêtre d'échantillonnage active (ex. 14h
    # entre 14h et 16h), sinon le slider démarre sur la fenêtre suivante.
    df = df[df["time"] >= active_window_start_utc()].reset_index(drop=True)

    return df


def get_short_range_forecast(client, lat, lon, elevation, forecast_days,
                             ensemble_after_days):
    """Prévision Météo-France complétée par best_match uniquement si nécessaire."""
    target_end = (
        pd.Timestamp.now(tz="UTC").normalize()
        + pd.Timedelta(days=min(forecast_days, ensemble_after_days))
    )
    try:
        meteofrance = get_forecast_for_point(
            client, lat, lon, elevation, min(forecast_days, 4),
            model="meteofrance_seamless",
        )
        required = ["temperature", "weather_code", "wind_speed", "wind_gusts"]
        meteofrance = meteofrance.dropna(subset=required).copy()
        meteofrance["data_source"] = "meteofrance_seamless"
    except Exception as error:
        print(f"     Météo-France indisponible, repli best_match : {error}")
        meteofrance = pd.DataFrame()

    # Le modèle Météo-France s'arrête vers J+4 tandis que la bascule ECMWF
    # est normalement à J+5. best_match ne sert qu'à combler cet éventuel
    # jour de transition (ou toute la fenêtre hors zone de couverture).
    last_time = meteofrance["time"].max() if not meteofrance.empty else None
    needs_fallback = last_time is None or last_time < target_end - pd.Timedelta(hours=1)
    if not needs_fallback:
        return meteofrance

    fallback = get_forecast_for_point(
        client, lat, lon, elevation,
        min(forecast_days, ensemble_after_days), model="best_match",
    )
    fallback["data_source"] = "best_match"
    if meteofrance.empty:
        return fallback
    return (
        pd.concat([fallback, meteofrance], ignore_index=True)
        .sort_values("time").drop_duplicates("time", keep="last")
        .reset_index(drop=True)
    )


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
    # certaines variables sont absentes. Elles ne constituent pas une
    # prévision affichable : sinon la dernière fenêtre du slider produit des
    # pictogrammes inconnus.
    valid = (
        np.isfinite(temperature).any(axis=1)
        & np.isfinite(weather_codes).any(axis=1)
        & np.isfinite(wind).any(axis=1)
        & np.isfinite(gusts).any(axis=1)
    )
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
        report_progress(
            i / max(1, len(towns)),
            f"météo {i + 1}/{len(towns)} · {town['name']}",
        )
        print(
            f"  -> Ville {i + 1}/{len(towns)} : {town['name']} "
            f"({town['role']}, km {town['distance_km']}) "
            f"lat={town['lat']:.4f}, lon={town['lon']:.4f}"
        )
        deterministic = get_short_range_forecast(
            client, town["lat"], town["lon"], town.get("elevation"),
            forecast_days, ensemble_after_days,
        )
        deterministic["temperature_low"] = deterministic["temperature"]
        deterministic["temperature_high"] = deterministic["temperature"]
        deterministic["precipitation_probability"] = np.where(
            deterministic["precipitation"] >= .1, 100.0, 0.0
        )
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
    report_progress(.99, "assemblage des prévisions")
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# 3. Programme principal
# ---------------------------------------------------------------------------

def main():
    print(f"Lecture des villes selectionnees : {config.towns_csv_path}")
    towns = load_towns(config.towns_csv_path)
    print(f"  -> {len(towns)} villes ({', '.join(t['name'] for t in towns)})")

    print(f"\nRecuperation des previsions meteo (Météo-France puis ECMWF, "
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
