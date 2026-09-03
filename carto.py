#!/usr/bin/env python3
"""Génère la visualisation météo interactive Leaflet/OSM."""

import json
import math
import os
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

import gpxpy
import pandas as pd
from shapely.geometry import LineString

import config
from navigation import NAVIGATION_CSS, NAVIGATION_SCRIPT, render_navigation


FR_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def weather_category(code):
    if code is None or (isinstance(code, float) and math.isnan(code)): return "unknown"
    code = int(code)
    if code == 0: return "clear"
    if code in (1, 2): return "partly_cloudy"
    if code == 3: return "cloudy"
    if code in (45, 48): return "fog"
    if code in (51, 53, 55, 56, 57): return "drizzle"
    if code in (61, 63, 65, 66, 67, 80, 81, 82): return "rain"
    if code in (71, 73, 75, 77, 85, 86): return "snow"
    if code in (95, 96, 99): return "storm"
    return "unknown"


def format_date_fr(ts):
    return f"{FR_JOURS[ts.weekday()]} {ts.day} {ts.hour}h"


def wind_direction_label(value):
    if value is None or pd.isna(value):
        return "indéterminée"
    labels = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]
    return labels[int((float(value) + 22.5) // 45) % 8]


def load_track(path, simplify=True):
    with open(path, "r", encoding="utf-8") as handle:
        gpx = gpxpy.parse(handle)
    points = [p for track in gpx.tracks for segment in track.segments for p in segment.points]
    if not points:
        points = [p for route in gpx.routes for p in route.points]
    exact_coordinates = [(p.latitude, p.longitude) for p in points]
    elevations = pd.Series([p.elevation for p in points], dtype="float64")
    elevations = elevations.interpolate(limit_direction="both").rolling(
        getattr(config, "elevation_smoothing_points", 5),
        center=True, min_periods=1
    ).median()
    cumulative_distance = 0.0
    cumulative_effort = 0.0
    profile = [{"distance": 0.0, "effort": 0.0}]
    climb_factor = getattr(config, "planning_climb_km_per_100m", 1.0)
    last_profile_distance = 0.0
    for index, (a, b) in enumerate(zip(exact_coordinates, exact_coordinates[1:]), 1):
        segment_distance = 2 * 6371.0088 * math.asin(math.sqrt(
            math.sin(math.radians(b[0] - a[0]) / 2) ** 2
            + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0]))
            * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
        ))
        ascent = max(0.0, float(elevations.iloc[index] - elevations.iloc[index - 1]))
        cumulative_distance += segment_distance
        cumulative_effort += segment_distance + ascent / 100 * climb_factor
        if cumulative_distance - last_profile_distance >= 1 or index == len(points) - 1:
            profile.append({"distance": round(cumulative_distance, 2),
                            "effort": round(cumulative_effort, 2)})
            last_profile_distance = cumulative_distance
    coordinates = [(p.longitude, p.latitude) for p in points]
    if simplify:
        tolerance = getattr(config, "gpx_simplify_degrees", .0003)
        coordinates = LineString(coordinates).simplify(
            tolerance, preserve_topology=False
        ).coords
    return [[lat, lon] for lon, lat in coordinates], cumulative_distance, profile


def load_data():
    forecasts = pd.read_csv(config.csv_path, parse_dates=["time"])
    forecasts["time"] = forecasts["time"].dt.tz_convert("Europe/Paris")
    return forecasts


def active_window_start():
    """Début de la fenêtre horaire actuellement affichable.

    À 15h, la prévision active est 14h, pas 16h. On prend donc la dernière
    heure d'échantillonnage inférieure ou égale à l'heure courante.
    """
    now = pd.Timestamp(datetime.now(ZoneInfo("Europe/Paris"))).floor("h")
    sample_hours = sorted({int(hour) % 24 for hour in config.sample_hours})
    active_hour = max((hour for hour in sample_hours if hour <= now.hour), default=sample_hours[-1])
    active_day = now.normalize()
    if active_hour > now.hour:
        active_day -= pd.Timedelta(days=1)
    return active_day + pd.Timedelta(hours=active_hour)


def complete_forecast_times(forecasts):
    point_count = forecasts["point_index"].nunique()
    required = ["temperature", "weather_code", "wind_speed", "wind_gusts", "precipitation"]
    complete = []
    for ts, rows in forecasts.groupby("time", sort=True):
        if len(rows) != point_count:
            continue
        if any(pd.to_numeric(rows[column], errors="coerce").isna().any() for column in required if column in rows):
            continue
        complete.append(pd.Timestamp(ts))
    return set(complete)


def selected_times(forecasts):
    hours = {int(hour) % 24 for hour in config.sample_hours}
    current_window = active_window_start()
    complete_times = complete_forecast_times(forecasts)
    return [pd.Timestamp(t) for t in sorted(forecasts["time"].unique())
            if pd.Timestamp(t) >= current_window
            and pd.Timestamp(t) in complete_times
            and pd.Timestamp(t).hour in hours and pd.Timestamp(t).minute == 0]


def make_payload(forecasts, route, route_distance_km, route_profile):
    current_window = active_window_start()
    forecasts = forecasts[forecasts["time"] >= current_window].copy()
    towns_frame = (forecasts.sort_values("time").drop_duplicates("point_index")
                   .sort_values("point_index"))
    day_abbreviations = ["LU", "MA", "ME", "JE", "VE", "SA", "DI"]
    weather_priority = {
        "unknown": 0, "clear": 1, "partly_cloudy": 2, "cloudy": 3,
        "fog": 4, "drizzle": 5, "rain": 6, "snow": 7, "storm": 8,
    }
    towns = []
    for _, town_row in towns_frame.iterrows():
        town_id = int(town_row.point_index)
        town_forecasts = forecasts[forecasts["point_index"] == town_id].copy()
        town_forecasts["date"] = town_forecasts["time"].dt.date
        daily = []
        for date, rows in town_forecasts.groupby("date", sort=True):
            categories = [weather_category(code) for code in rows["weather_code"]]
            category = max(categories, key=lambda value: weather_priority[value])
            wind_values = pd.to_numeric(rows["wind_speed"], errors="coerce")
            wind_index = wind_values.idxmax() if wind_values.notna().any() else rows.index[0]
            direction = rows.loc[wind_index].get("wind_direction")
            probability = pd.to_numeric(rows.get("precipitation_probability"), errors="coerce")
            def conditions_at(hour):
                matches = rows[rows["time"].dt.hour == hour]
                if matches.empty:
                    return None
                value = matches.iloc[0]
                rain_probability = value.get("precipitation_probability")
                return {
                    "temperature": round(float(value["temperature"])),
                    "weather": weather_category(value.get("weather_code")),
                    "wind": round(float(value.get("wind_speed", 0))),
                    "wind_direction": wind_direction_label(value.get("wind_direction")),
                    "rain_probability": (round(float(rain_probability))
                                         if pd.notna(rain_probability) else None),
                    "precipitation": round(float(value.get("precipitation", 0)), 1),
                }
            daily.append({
                "date": date.isoformat(),
                "weekday": day_abbreviations[date.weekday()],
                "day": date.day,
                "weather": category,
                "temperature_max": round(float(rows["temperature"].max())),
                "temperature_min": round(float(rows["temperature"].min())),
                "noon": conditions_at(12),
                "evening": conditions_at(20),
                "hourly": {str(hour): value for hour in range(24)
                           if (value := conditions_at(hour)) is not None},
                "wind": round(float(wind_values.max())) if wind_values.notna().any() else 0,
                "gusts": round(float(pd.to_numeric(rows["wind_gusts"], errors="coerce").max())),
                "wind_direction": wind_direction_label(direction),
                "wind_degrees": round(float(direction)) if pd.notna(direction) else 0,
                "rain_probability": round(float(probability.max())) if probability.notna().any() else None,
                "precipitation": round(float(pd.to_numeric(rows["precipitation"], errors="coerce").sum()), 1),
                "ensemble": bool((rows.get("data_source") == "ecmwf_ifs_ensemble").any()),
            })
        towns.append({
            "id": str(town_id), "name": town_row["name"],
            "lat": float(town_row.lat), "lon": float(town_row.lon),
            "distance_km": float(town_row.distance_km), "role": town_row["role"],
            "daily": daily,
        })
    frames = []
    for ts in selected_times(forecasts):
        values = {}
        for _, row in forecasts[forecasts["time"] == ts].iterrows():
            temperature = float(row.temperature)
            low, high = row.get("temperature_low", temperature), row.get("temperature_high", temperature)
            probability = row.get("precipitation_probability", None)
            values[str(int(row.point_index))] = {
                "temperature": round(temperature),
                "low": round(float(low)) if pd.notna(low) else round(temperature),
                "high": round(float(high)) if pd.notna(high) else round(temperature),
                "probability": round(float(probability)) if pd.notna(probability) else None,
                "precipitation": round(float(row.get("precipitation", 0)), 1),
                "wind": round(float(row.get("wind_speed", 0))),
                "gusts": round(float(row.get("wind_gusts", 0))),
                "wind_direction": wind_direction_label(row.get("wind_direction")),
                "weather": weather_category(row.get("weather_code")),
                "ensemble": row.get("data_source", "best_match") == "ecmwf_ifs_ensemble",
            }
        frames.append({
            "label": format_date_fr(ts),
            "iso": ts.isoformat(),
            "day": ts.strftime("%Y-%m-%d"),
            "day_label": f"{FR_JOURS[ts.weekday()][:3]} {ts.day}",
            "hour": ts.hour,
            "hour_label": f"{ts.hour}h",
            "values": values,
        })
    visible_towns = [town for town in towns if town["role"] not in ("planning", "meteo")]
    places = pd.read_csv(config.towns_csv_path).sort_values("distance_km")
    planner_towns = []
    for _, place in places.iterrows():
        weather_town = min(
            towns,
            key=lambda town: 2 * 6371.0088 * math.asin(math.sqrt(
                math.sin(math.radians(town["lat"] - float(place.lat)) / 2) ** 2
                + math.cos(math.radians(float(place.lat)))
                * math.cos(math.radians(town["lat"]))
                * math.sin(math.radians(town["lon"] - float(place.lon)) / 2) ** 2
            )),
        )
        planner_towns.append({
            "name": place["name"], "lat": float(place.lat), "lon": float(place.lon),
            "distance_km": float(place.distance_km), "role": place["role"],
            "weather_town_id": weather_town["id"],
        })
    return {"route": route, "route_distance_km": round(route_distance_km, 1),
            "route_profile": route_profile,
            "planning_daily_riding_hours": getattr(config, "planning_daily_riding_hours", 12),
            "planning_climb_km_per_100m": getattr(config, "planning_climb_km_per_100m", 1),
            "towns": visible_towns, "weather_towns": towns,
            "planner_towns": planner_towns, "frames": frames}


def render_panel_header(button_id, button_label, title="", title_id=None):
    """En-tête commun aux panneaux plein écran, indépendant de leur contenu."""
    id_attribute = f' id="{escape(title_id, quote=True)}"' if title_id else ""
    return (
        '<div class="panel-head">'
        f'<button id="{escape(button_id, quote=True)}" class="panel-back">'
        f'{escape(button_label)}</button>'
        f'<h2{id_attribute} class="panel-title">{escape(title)}</h2>'
        '</div>'
    )


def build_html(payload):
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    title = escape(getattr(config, "project", "Prévisions météo"))
    page_url = escape(config.github_pages_url, quote=True)
    preview_url = escape(f"{config.github_pages_url}preview.png", quote=True)
    routes = [(config.route_slug_for(path), config.route_title_for(path))
              for path in config.list_gpx_files()]
    navigation_html = render_navigation(
        config.project, routes, "../", "../", settings=True, title_href="./"
    )
    planner_header = render_panel_header(
        "planner-close", "Carte", "Prévisions du voyage"
    )
    details_header = render_panel_header(
        "close-details", "Fermer", title_id="details-title"
    )
    speed = max(100, int(getattr(config, "speed", .5) * 1000))
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<meta name="description" content="Prévisions météo interactives du parcours {title}">
<meta property="og:type" content="website"><meta property="og:locale" content="fr_FR">
<meta property="og:title" content="{title} — prévisions météo">
<meta property="og:description" content="Carte météo interactive du parcours, actualisée automatiquement.">
<meta property="og:url" content="{page_url}">
<meta property="og:image" content="{preview_url}">
<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Aperçu des prévisions météo du parcours {title}">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#18295c"><meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="{escape(config.github_pages_base_url)}/manifest.webmanifest">
<link rel="icon" href="{escape(config.github_pages_base_url)}/icon-192.png">
<link rel="apple-touch-icon" href="{escape(config.github_pages_base_url)}/apple-touch-icon.png">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
*{{box-sizing:border-box}} html,body{{height:100%;margin:0;overflow:hidden;font-family:system-ui,sans-serif}}
main{{position:relative;height:100dvh;display:flex;flex-direction:column;background:#171717}} #map{{min-height:0;flex:1}}
{NAVIGATION_CSS}
.leaflet-overlay-pane svg{{z-index:450}}
.meteo-marker{{width:1px!important;height:1px!important;text-align:center;line-height:1;pointer-events:auto}}
.temperature,.weather{{position:absolute;left:0;top:0}}
.temperature{{color:#111;font-size:18px;font-weight:900;white-space:nowrap;
text-shadow:-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,1px 1px 0 #fff}}
.weather{{transform:translate(-50%,-50%);font-size:42px;filter:drop-shadow(0 1px 2px white)}}
.details{{flex:1;min-height:0;overflow:hidden;background:#fff;color:#17234d;line-height:1.2}}
.details[hidden]{{display:none}} main.details-open #map,main.details-open .controls{{display:none}}
.details-shell{{width:min(920px,100%);height:100%;margin:auto;padding:8px 14px;display:flex;flex-direction:column;overflow:hidden}}
.panel-head{{position:relative;width:100%;height:36px;min-height:36px;flex:0 0 36px;margin-bottom:4px}}
.panel-title{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:0 72px;margin:0;text-align:center;font-size:20px;color:#111;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none}}
.panel-back{{position:absolute;z-index:1;left:0;top:50%;transform:translateY(-50%);border:1px solid #ddd;background:#f6f6f8;border-radius:18px;padding:6px 12px;font-size:13px;font-weight:700;color:#17234d;cursor:pointer}}
.daily-strip{{flex:0 0 70px;display:flex;gap:0;overflow:hidden;padding:3px 0;border-bottom:1px solid #ddd;background:#fff}}
.daily-choice{{flex:1 1 0;min-width:0;border:0;border-right:3px solid #fff;background:#eef0f4;border-radius:0;padding:2px 0;
display:grid;grid-template-columns:1fr;grid-template-rows:28px 14px 20px;place-items:center;color:#70768c;cursor:pointer}}
.daily-choice:last-child{{border-right:0}}
.daily-choice .daily-icon{{font-size:21px;line-height:1}} .daily-choice .daily-weekday{{font-size:9px;line-height:1}} .daily-choice .daily-number{{font-size:14px;line-height:1;font-weight:800;color:#315bb5}}
.daily-choice.active{{background:#f6a800;color:#fff}} .daily-choice.active .daily-number{{color:#fff}}
#details-content{{flex:1;min-height:0;display:flex;flex-direction:column;overflow:hidden}}
.forecast-chart{{flex:1 1 0;min-height:0;background:#fff;overflow:hidden}}
.forecast-chart svg{{display:block;width:100%;height:100%}}
.metric-grid{{flex:0 0 auto;display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:7px}}
.metric{{min-height:58px;background:#ececf2;border-radius:9px;padding:7px 9px}} .metric-label{{display:block;font-size:10px;color:#68708c;margin-bottom:3px}}
.metric-value{{display:block;font-size:15px;font-weight:800;color:#17234d}} .metric.temperature-card{{background:#ff4b4f}} .metric.wind-card{{background:#55c94c}}
.metric.temperature-card *,.metric.wind-card *{{color:#fff}} .metric.rain-card{{background:#e8edf8}}
.metric.precipitation-card{{background:#4da3ff}}.metric.precipitation-card *{{color:#fff}}
.detail-source{{flex:0 0 auto;margin:5px 2px 0;text-align:center;font-size:10px;color:#68708c}} .detail-source a{{color:#315bb5;font-weight:700}}
.trip-planner{{flex:1;min-height:0;overflow-y:auto;background:#fff;color:#17234d;padding:14px}}
.trip-planner[hidden]{{display:none}} main.planner-open #map,main.planner-open .controls,main.planner-open .details{{display:none}}
.planner-shell{{width:min(760px,100%);margin:auto}}.planner-shell>.panel-head{{margin-bottom:14px}}
.planner-form{{display:grid;grid-template-columns:1.4fr .8fr .8fr;gap:10px;margin-bottom:14px}}.planner-form label{{font-size:12px;font-weight:750}}
.planner-form input{{display:block;width:100%;margin-top:4px;border:1px solid #cbd1df;border-radius:8px;background:#fff;padding:9px;font:inherit;color:#17234d}}
.trip-days{{display:grid;gap:0}}.trip-day{{background:#fff;padding:9px}}.trip-day-head{{display:flex;justify-content:space-between;gap:8px;margin-bottom:7px}}.trip-day-head span{{font-size:11px;color:#68708c}}
.trip-metrics{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}}.trip-stop{{min-width:0;background:#f1f3f8;border-radius:8px;padding:7px;text-align:center}}.trip-stop strong{{display:block;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.trip-stop-value{{display:block;font-size:18px;font-weight:850;margin:3px 0}}.trip-weather-icon{{font-size:22px;vertical-align:middle;margin-right:3px}}.trip-stop small{{display:block;font-size:9px;color:#68708c;line-height:1.25}}
.trip-unavailable{{color:#8b90a0;font-size:13px}}
@media(max-width:600px){{.details-shell{{padding:7px 7px}}.panel-title{{font-size:18px}}.metric-grid{{grid-template-columns:repeat(2,1fr);gap:5px}}
.metric{{min-height:50px;padding:5px 7px}}.metric-value{{font-size:14px}}.trip-planner{{padding:9px 7px}}.planner-form{{grid-template-columns:1.3fr .7fr .8fr;gap:6px}}.planner-form input{{padding:8px 5px}}.trip-day{{padding:7px}}.trip-metrics{{gap:4px}}.trip-stop{{padding:6px 3px}}.trip-stop-value{{font-size:16px}}}}
@media(max-height:680px){{.daily-strip{{flex-basis:62px}}.daily-choice{{grid-template-rows:24px 12px 18px}}.daily-choice .daily-icon{{font-size:18px}}
.metric{{min-height:45px}}.detail-source{{margin-top:3px}}}}
.controls{{height:86px;flex:0 0 86px;background:#18295c;color:#fff;padding:2px 0}}
.timeline{{height:100%;display:grid;grid-template-rows:1fr 1fr;gap:0}}
.strip-wrap{{position:relative;min-width:0;overflow:hidden}} .strip-wrap::after{{content:"";position:absolute;z-index:0;
left:50%;top:1px;bottom:1px;width:64px;transform:translateX(-50%);border-radius:7px;background:#f6a800;pointer-events:none}}
.strip{{position:relative;z-index:1;height:100%;display:flex;align-items:stretch;gap:0;overflow-x:auto;padding:1px calc(50% - 32px);
scroll-snap-type:x mandatory;scrollbar-width:none;overscroll-behavior-x:contain;-webkit-overflow-scrolling:touch}}
.strip::-webkit-scrollbar{{display:none}} .strip button{{flex:0 0 64px;width:64px;border:0;border-radius:7px;
background:transparent;color:#fff;font-size:14px;font-weight:650;scroll-snap-align:center;cursor:pointer;padding:3px 6px}}
.strip button.active{{color:#fff;font-weight:850}}
.map-play{{width:56px;height:56px;border:0;border-radius:50%;background:#352d32e8;color:#fff;font-size:27px;
display:grid;place-items:center;cursor:pointer;box-shadow:0 2px 8px #0005}}
</style></head><body><main>{navigation_html}<div id="map"></div>
<div class="controls"><div class="timeline"><div class="strip-wrap"><div id="days" class="strip days"></div></div><div class="strip-wrap"><div id="hours" class="strip hours"></div></div></div></div>
<section id="trip-planner" class="trip-planner" hidden><div class="planner-shell">{planner_header}<div class="planner-form"><label>Date de départ<input id="trip-start" type="date"></label><label>Heure départ<input id="trip-time" type="time" step="900"></label><label>Durée du voyage<input id="trip-duration" type="number" min="1" max="16" inputmode="numeric"></label></div><div id="trip-days" class="trip-days"></div></div></section>
<aside id="details" class="details" hidden><div class="details-shell">{details_header}<div id="daily-strip" class="daily-strip"></div><div id="details-content"></div></div></aside>
</main><script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const data={data},townIds=data.towns.map(town=>town.id);
function frameComplete(frame){{return townIds.every(id=>{{const value=frame.values[id];
  return value&&Number.isFinite(value.temperature)&&value.weather&&value.weather!=='unknown';
}})}}
data.frames=data.frames.filter(frameComplete);
const now=new Date();now.setMinutes(0,0,0);
const activeIndex=data.frames.reduce((best,frame,index)=>new Date(frame.iso)<=now?index:best,-1);
if(activeIndex>0)data.frames=data.frames.slice(activeIndex);
const icons={{clear:'☀️',partly_cloudy:'🌤️',cloudy:'☁️',fog:'🌫️',drizzle:'🌦️',rain:'🌧️',snow:'🌨️',storm:'⛈️',unknown:'❔'}},
weatherNames={{clear:'Ciel dégagé',partly_cloudy:'Éclaircies',cloudy:'Nuageux',fog:'Brouillard',drizzle:'Bruine',rain:'Pluie',snow:'Neige',storm:'Orage',unknown:'Indéterminé'}};
{NAVIGATION_SCRIPT}
const planner=document.querySelector('#trip-planner'),tripStart=document.querySelector('#trip-start'),tripDuration=document.querySelector('#trip-duration'),tripTime=document.querySelector('#trip-time'),tripDays=document.querySelector('#trip-days');
const plannerKey='gpx-weather-trip-{escape(config.route_slug)}';
function localDate(value){{return new Date(`${{value}}T12:00:00`)}}
function isoDate(date){{const year=date.getFullYear(),month=String(date.getMonth()+1).padStart(2,'0'),day=String(date.getDate()).padStart(2,'0');return `${{year}}-${{month}}-${{day}}`}}
function distanceAtEffort(target){{const profile=data.route_profile;if(target<=0)return 0;const index=profile.findIndex(point=>point.effort>=target);if(index<0)return data.route_distance_km;const b=profile[index],a=profile[Math.max(0,index-1)],ratio=(target-a.effort)/Math.max(.001,b.effort-a.effort);return a.distance+(b.distance-a.distance)*ratio}}
function nearestPlannerTown(distance){{return data.planner_towns.reduce((best,town)=>Math.abs(town.distance_km-distance)<Math.abs(best.distance_km-distance)?town:best)}}
function endpointTown(role){{return data.planner_towns.find(town=>town.role.includes(role))||nearestPlannerTown(role==='depart'?0:data.route_distance_km)}}
function noonPlannerTown(distance,morningTown,eveningTown,lastDay){{const upper=lastDay?data.route_distance_km:eveningTown.distance_km,distinct=data.planner_towns.filter(town=>town.name!==morningTown.name&&town.name!==eveningTown.name),candidates=distinct.filter(town=>town.distance_km>morningTown.distance_km+1&&town.distance_km<upper-1),pool=candidates.length?candidates:distinct;return pool.length?pool.reduce((best,town)=>Math.abs(town.distance_km-distance)<Math.abs(best.distance_km-distance)?town:best):nearestPlannerTown(distance)}}
function townForecast(town,date){{const weatherTown=data.weather_towns.find(candidate=>candidate.id===town?.weather_town_id);return weatherTown?.daily.find(row=>row.date===date)}}
function conditionCard(title,town,conditions){{if(!conditions)return `<div class="trip-stop"><strong>${{title}} · ${{town?.name??'—'}}</strong><span class="trip-unavailable">Indisponible</span></div>`;const rain=conditions.rain_probability===null?'—':`${{conditions.rain_probability}} %`;return `<div class="trip-stop"><strong>${{title}} · ${{town.name}}</strong><span class="trip-stop-value"><b class="trip-weather-icon">${{icons[conditions.weather]}}</b>${{conditions.temperature}}°</span><small>Vent ${{conditions.wind}} km/h · ${{conditions.wind_direction}}</small><small>Pluie ${{rain}} · ${{conditions.precipitation}} mm</small></div>`}}
function renderPlanner(){{const duration=Math.max(1,Math.min(16,Number(tripDuration.value)||1)),start=localDate(tripStart.value),departureParts=(tripTime.value||'08:00').split(':').map(Number),departureHour=departureParts[0]+departureParts[1]/60,totalEffort=data.route_profile.at(-1).effort,dailyEffort=totalEffort/duration,rideHours=data.planning_daily_riding_hours,arrivalClock=departureHour+rideHours,arrivalHour=Math.round(arrivalClock)%24,arrivalDayOffset=Math.floor(Math.round(arrivalClock)/24);localStorage.setItem(plannerKey,JSON.stringify({{start:tripStart.value,duration,time:tripTime.value}}));
  const nightTowns=Array.from({{length:duration+1}},(_,index)=>index===0?endpointTown('depart'):index===duration?endpointTown('arrivee'):nearestPlannerTown(distanceAtEffort(dailyEffort*index)));
  tripDays.innerHTML=Array.from({{length:duration}},(_,index)=>{{const date=new Date(start);date.setDate(start.getDate()+index);const dateKey=isoDate(date),eveningDate=new Date(date);eveningDate.setDate(date.getDate()+arrivalDayOffset);const eveningDateKey=isoDate(eveningDate),label=date.toLocaleDateString('fr-FR',{{weekday:'short',day:'numeric',month:'short'}}),startEffort=dailyEffort*index,endEffort=dailyEffort*(index+1),startDistance=distanceAtEffort(startEffort),endDistance=distanceAtEffort(endEffort),noonRatio=Math.max(0,Math.min(1,(12-departureHour)/Math.max(.1,rideHours))),noonDistance=distanceAtEffort(startEffort+dailyEffort*noonRatio),morningTown=nightTowns[index],eveningTown=nightTowns[index+1],noonTown=noonPlannerTown(noonDistance,morningTown,eveningTown,index===duration-1),morningForecast=townForecast(morningTown,dateKey),noonForecast=townForecast(noonTown,dateKey),eveningForecast=townForecast(eveningTown,eveningDateKey),distance=Math.round(endDistance-startDistance),gain=Math.max(0,Math.round((dailyEffort-(endDistance-startDistance))*100/Math.max(.01,data.planning_climb_km_per_100m))),speed=(endDistance-startDistance)/rideHours;
    const morning=morningForecast?`<div class="trip-stop"><strong>Matin · ${{morningTown.name}}</strong><span class="trip-stop-value"><b class="trip-weather-icon">${{icons[morningForecast.weather]}}</b>${{morningForecast.temperature_min}}°</span><small>Température minimale</small></div>`:`<div class="trip-stop"><strong>Matin · ${{morningTown.name}}</strong><span class="trip-unavailable">Indisponible</span></div>`;
    return `<article class="trip-day"><div class="trip-day-head"><strong>${{label}}</strong><span>${{distance}} km · D+ ${{gain}} m · ${{speed.toFixed(1)}} km/h</span></div><div class="trip-metrics">${{morning}}${{conditionCard('Midi',noonTown,noonForecast?.noon)}}${{conditionCard('Soir',eveningTown,eveningForecast?.hourly?.[String(arrivalHour)])}}</div></article>`}}).join('')}}
function openPlanner(){{stop();details.hidden=true;selectedTownId=null;document.querySelector('main').classList.remove('details-open');planner.hidden=false;document.querySelector('main').classList.add('planner-open');renderPlanner()}}
function closePlanner(){{planner.hidden=true;document.querySelector('main').classList.remove('planner-open');setTimeout(()=>map.invalidateSize(),0)}}
const savedTrip=(()=>{{try{{return JSON.parse(localStorage.getItem(plannerKey))||{{}}}}catch{{return {{}}}}}})(),availableDates=data.towns.flatMap(town=>town.daily.map(row=>row.date)).sort();
tripStart.min=availableDates[0]||'';tripStart.removeAttribute('max');tripStart.value=savedTrip.start||data.frames[0]?.day||availableDates[0]||isoDate(new Date());tripDuration.value=savedTrip.duration||{int(config.trip_days)};tripTime.value=savedTrip.time||'08:00';
tripStart.onchange=renderPlanner;tripDuration.oninput=renderPlanner;tripTime.oninput=renderPlanner;document.querySelector('#settings-button').onclick=openPlanner;document.querySelector('#planner-close').onclick=closePlanner;
const map=L.map('map',{{zoomControl:true}});
const osmAttribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const osm=L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:osmAttribution}}).addTo(map);
const positron=L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}.png',{{
  subdomains:'abcd',maxZoom:20,attribution:osmAttribution+' &copy; <a href="https://carto.com/attributions">CARTO</a>'}});
const voyager=L.tileLayer('https://{{s}}.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}.png',{{
  subdomains:'abcd',maxZoom:20,attribution:osmAttribution+' &copy; <a href="https://carto.com/attributions">CARTO</a>'}});
const topo=L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png',{{subdomains:'abc',maxZoom:17,
  attribution:osmAttribution+' | &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)'}});
L.control.layers({{'OSM classique':osm,'Carte claire':positron,'Voyager':voyager,'Topographique':topo}},null,{{collapsed:true}}).addTo(map);
const route=L.polyline(data.route,{{color:'#d62728',weight:4,opacity:.9}}).addTo(map); map.fitBounds(route.getBounds(),{{padding:[35,35]}});
const routeArrows=L.layerGroup().addTo(map);
function drawRouteArrows(){{routeArrows.clearLayers();const points=data.route.map(point=>map.latLngToLayerPoint(point)),spacing=55,size=5;let next=spacing/2;
  for(let i=1;i<points.length;i++){{const a=points[i-1],b=points[i],dx=b.x-a.x,dy=b.y-a.y,length=Math.hypot(dx,dy);if(!length)continue;
    const ux=dx/length,uy=dy/length,px=-uy,py=ux;while(next<=length){{const cx=a.x+ux*next,cy=a.y+uy*next;
      const tip=L.point(cx+ux*size,cy+uy*size),back=L.point(cx-ux*size*.65,cy-uy*size*.65);
      const left=L.point(back.x+px*size*.7,back.y+py*size*.7),right=L.point(back.x-px*size*.7,back.y-py*size*.7);
      L.polyline([map.layerPointToLatLng(left),map.layerPointToLatLng(tip),map.layerPointToLatLng(right)],{{pane:'overlayPane',color:'#d62728',weight:2,opacity:.95,interactive:false,lineCap:'round',lineJoin:'round'}}).addTo(routeArrows);
      next+=spacing;
    }}next-=length;
  }}
}}
drawRouteArrows();
const markers={{}}; for(const town of data.towns){{const marker=L.marker([town.lat,town.lon],{{icon:L.divIcon({{
className:'meteo-marker',iconSize:[1,1],iconAnchor:[0,0],html:
`<span class="temperature">–</span><span class="weather"></span>`}})}}).addTo(map);
marker.on('click',()=>showDetails(town.id));markers[town.id]=marker;}}
const townById=Object.fromEntries(data.towns.map(t=>[t.id,t]));
const days=document.querySelector('#days'),hours=document.querySelector('#hours'),details=document.querySelector('#details');
let currentIndex=0,selectedTownId=null,selectedDetailDate=null,mapPlay=null;
const PlayControl=L.Control.extend({{onAdd(){{mapPlay=L.DomUtil.create('button','map-play');mapPlay.type='button';mapPlay.textContent='▶';
  mapPlay.title='Lire automatiquement les prévisions';mapPlay.setAttribute('aria-label',mapPlay.title);L.DomEvent.disableClickPropagation(mapPlay);L.DomEvent.on(mapPlay,'click',toggle);return mapPlay}}}});
new PlayControl({{position:'bottomright'}}).addTo(map);
function overlap(a,b){{return Math.max(0,Math.min(a.r,b.r)-Math.max(a.l,b.l))*Math.max(0,Math.min(a.b,b.b)-Math.max(a.t,b.t))}}
function layoutLabels(){{
  const entries=Object.values(markers).map(marker=>({{marker,point:map.latLngToContainerPoint(marker.getLatLng())}}));
  const iconsBoxes=entries.map(e=>({{l:e.point.x-25,r:e.point.x+25,t:e.point.y-25,b:e.point.y+25}})),placed=[];
  function place(element,point,candidates){{
    if(!element.textContent) return;
    const w=element.offsetWidth,h=element.offsetHeight;
    let best=null;
    for(const [x,y] of candidates){{const box={{l:point.x+x-w/2,r:point.x+x+w/2,t:point.y+y-h/2,b:point.y+y+h/2}};
      const collisions=[...iconsBoxes,...placed].reduce((sum,other)=>sum+overlap(box,other),0);
      const score=collisions*10000+Math.hypot(x,y);if(!best||score<best.score)best={{x,y,box,score}};
    }}
    element.style.transform=`translate(${{best.x}}px,${{best.y}}px) translate(-50%,-50%)`;
    placed.push(best.box);
  }}
  for(const entry of entries){{const root=entry.marker.getElement();if(!root)continue;
    place(root.querySelector('.temperature'),entry.point,[[0,-36],[38,-8],[-38,-8],[0,36]]);
  }}
}}
function escapeHtml(text){{const node=document.createElement('div');node.textContent=String(text);return node.innerHTML}}
function dailyChart(town,selectedIndex){{const rows=town.daily,columnWidth=40,width=rows.length*columnWidth,height=350,tempTop=24,tempBottom=190,barBase=300;
  const temperatures=rows.flatMap(row=>[row.temperature_min,row.temperature_max]),tempMin=Math.floor(Math.min(...temperatures)-1),tempMax=Math.ceil(Math.max(...temperatures)+1);
  const maxWind=Math.max(1,...rows.map(row=>row.wind)),maxRain=Math.max(1,...rows.map(row=>row.precipitation)),x=index=>columnWidth/2+index*columnWidth;
  const yTemp=value=>tempTop+(tempMax-value)/(tempMax-tempMin)*(tempBottom-tempTop),columnGap=3,columnBarWidth=columnWidth-columnGap,dataBarWidth=columnBarWidth/2;
  const maxPoints=rows.map((row,index)=>`${{x(index)}},${{yTemp(row.temperature_max)}}`).join(' '),minPoints=rows.map((row,index)=>`${{x(index)}},${{yTemp(row.temperature_min)}}`).join(' ');
  const columns=rows.map((row,index)=>{{const px=x(index),windHeight=row.wind/maxWind*78,rainHeight=row.precipitation/maxRain*78,selected=index===selectedIndex;
    const columnLeft=px-columnWidth/2;
    return `<rect x="${{columnLeft}}" y="18" width="${{columnBarWidth}}" height="${{barBase+6}}" fill="${{selected?'#c9cdd6':'#f4f5f8'}}" opacity="${{selected?.75:.55}}"/>
    <rect x="${{columnLeft}}" y="${{barBase-windHeight}}" width="${{dataBarWidth}}" height="${{windHeight}}" fill="#50c744"/>
    <rect x="${{columnLeft+dataBarWidth}}" y="${{barBase-rainHeight}}" width="${{dataBarWidth}}" height="${{rainHeight}}" fill="#4da3ff"/>
    <text x="${{columnLeft+dataBarWidth/2}}" y="${{barBase-windHeight-5}}" text-anchor="middle" font-size="9" fill="#389b31">${{row.wind}}</text>
    <text x="${{columnLeft+dataBarWidth*1.5}}" y="${{barBase-rainHeight-5}}" text-anchor="middle" font-size="9" fill="#287cc9">${{row.precipitation}}</text>
    <text x="${{px}}" y="${{barBase+16}}" text-anchor="middle" font-size="17" fill="#17234d" transform="rotate(${{row.wind_degrees}} ${{px}} ${{barBase+11}})">↑</text>
    <text x="${{px}}" y="${{height-7}}" text-anchor="middle" font-size="10" fill="#70768c">${{row.weekday}} ${{row.day}}</text>`}}).join('');
  const labels=rows.map((row,index)=>`<text x="${{x(index)}}" y="${{yTemp(row.temperature_max)-7}}" text-anchor="middle" font-size="10" fill="#ef4444">${{row.temperature_max}}</text>
  <text x="${{x(index)}}" y="${{yTemp(row.temperature_min)+14}}" text-anchor="middle" font-size="10" fill="#3182ce">${{row.temperature_min}}</text>`).join('');
  return `<div class="forecast-chart"><svg viewBox="0 0 ${{width}} ${{height}}" preserveAspectRatio="none" role="img">${{columns}}
  <polyline points="${{maxPoints}}" fill="none" stroke="#ef4444" stroke-width="4" stroke-linejoin="round"/> <polyline points="${{minPoints}}" fill="none" stroke="#3182ce" stroke-width="4" stroke-linejoin="round"/>
  ${{labels}}</svg></div>`}}
function showDetails(id,date=null){{stop();selectedTownId=id;const town=townById[id];if(!town?.daily.length)return;
  selectedDetailDate=date||selectedDetailDate||data.frames[currentIndex].day;let selectedIndex=town.daily.findIndex(row=>row.date===selectedDetailDate);if(selectedIndex<0)selectedIndex=0;
  const selected=town.daily[selectedIndex];selectedDetailDate=selected.date;const source=selected.ensemble?'Médiane de 51 scénarios ECMWF':'Modèle local haute résolution';
  const sourceUrl=selected.ensemble?'https://open-meteo.com/en/docs/ensemble-api':'https://open-meteo.com/en/docs';document.querySelector('#details-title').textContent=town.name;
  const dailyStrip=document.querySelector('#daily-strip');dailyStrip.innerHTML=town.daily.map((row,index)=>`<button class="daily-choice ${{index===selectedIndex?'active':''}}" data-date="${{row.date}}">
  <span class="daily-icon">${{icons[row.weather]}}</span><span class="daily-weekday">${{row.weekday}}</span><span class="daily-number">${{String(row.day).padStart(2,'0')}}</span></button>`).join('');
  dailyStrip.querySelectorAll('button').forEach(button=>button.onclick=()=>showDetails(id,button.dataset.date));
  const rainValue=selected.rain_probability===null?'—':`${{selected.rain_probability}} %`;
  document.querySelector('#details-content').innerHTML=`${{dailyChart(town,selectedIndex)}}
  <div class="metric-grid"><div class="metric temperature-card"><span class="metric-label">Températures</span><span class="metric-value">${{selected.temperature_min}}° / ${{selected.temperature_max}}°</span></div>
  <div class="metric rain-card"><span class="metric-label">Risque de pluie</span><span class="metric-value">${{rainValue}}</span></div>
  <div class="metric precipitation-card"><span class="metric-label">Précipitations</span><span class="metric-value">${{selected.precipitation}} mm</span></div>
  <div class="metric"><span class="metric-label">Conditions</span><span class="metric-value">${{icons[selected.weather]}} ${{weatherNames[selected.weather]}}</span></div>
  <div class="metric wind-card"><span class="metric-label">Vent</span><span class="metric-value">${{selected.wind}} km/h · ${{selected.wind_direction}}</span></div>
  <div class="metric gust-card"><span class="metric-label">Rafales</span><span class="metric-value">${{selected.gusts}} km/h</span></div></div>
  <p class="detail-source"><a href="${{sourceUrl}}" target="_blank" rel="noopener">${{source}}</a>${{selected.ensemble?' — incertitude croissante avec l’échéance.':''}}</p>`;
  details.hidden=false;document.querySelector('main').classList.add('details-open');
}}
function show(i,draggedStrip=null){{if(!data.frames.length)return;currentIndex=Math.max(0,Math.min(data.frames.length-1,i));const f=data.frames[currentIndex];
  for(const [id,m] of Object.entries(markers)){{const v=f.values[id],e=m.getElement();if(!e||!v)continue;const temp=e.querySelector('.temperature');
    temp.textContent=v.temperature+'°';e.querySelector('.weather').textContent=icons[v.weather];
    e.title=v.ensemble?`${{v.low}} à ${{v.high}}°C (80 % des scénarios)`:'';
  }}
  renderHours(f.day);const activeDay=days.querySelector(`[data-day="${{f.day}}"]`),activeHour=hours.querySelector(`[data-hour="${{f.hour}}"]`);
  days.querySelectorAll('button').forEach(button=>button.classList.toggle('active',button===activeDay));
  hours.querySelectorAll('button').forEach(button=>button.classList.toggle('active',button===activeHour));
  if(draggedStrip!==days)centerChoice(days,activeDay);if(draggedStrip!==hours)centerChoice(hours,activeHour);
  if(selectedTownId)showDetails(selectedTownId);requestAnimationFrame(layoutLabels);
}}
function centerChoice(strip,button){{if(button)strip.scrollTo({{left:button.offsetLeft-(strip.clientWidth-button.offsetWidth)/2,behavior:'smooth'}})}}
function indexFor(day,hour){{const candidates=data.frames.map((frame,index)=>({{frame,index}})).filter(item=>item.frame.day===day);
  if(!candidates.length)return currentIndex;
  const exact=candidates.find(item=>item.frame.hour===hour);if(exact)return exact.index;
  const before=candidates.filter(item=>item.frame.hour<=hour).at(-1);
  return (before??candidates.at(-1)).index;
}}
const seenDays=new Set();data.frames.forEach(frame=>{{if(seenDays.has(frame.day))return;seenDays.add(frame.day);const button=document.createElement('button');
  button.dataset.day=frame.day;button.textContent=frame.day_label;button.onclick=()=>{{stop();show(indexFor(frame.day,data.frames[currentIndex].hour))}};days.append(button);
}});
function renderHours(day){{const available=[...new Set(data.frames.filter(frame=>frame.day===day).map(frame=>frame.hour))].sort((a,b)=>a-b);
  const displayed=[...hours.querySelectorAll('button')].map(button=>Number(button.dataset.hour));
  if(hours.dataset.day===day&&displayed.join(',')===available.join(','))return;
  hours.dataset.day=day;hours.replaceChildren();available.forEach(hour=>{{const button=document.createElement('button');button.dataset.hour=hour;
    button.textContent=`${{String(hour).padStart(2,'0')}}:00`;button.onclick=()=>{{stop();show(indexFor(hours.dataset.day,hour))}};hours.append(button)}});
}}
function selectCentered(strip,button){{if(!button)return;stop();if(strip===days)show(indexFor(button.dataset.day,data.frames[currentIndex].hour),strip);
  else show(indexFor(hours.dataset.day,Number(button.dataset.hour)),strip);
}}
function enableSlideSelection(strip){{let pending,animationFrame,userScrolling=false;const arm=()=>{{userScrolling=true}};strip.addEventListener('pointerdown',arm);
  strip.addEventListener('touchstart',arm,{{passive:true}});strip.addEventListener('wheel',arm,{{passive:true}});
  strip.addEventListener('scroll',()=>{{if(!userScrolling)return;cancelAnimationFrame(animationFrame);animationFrame=requestAnimationFrame(()=>{{
    const center=strip.scrollLeft+strip.clientWidth/2,buttons=[...strip.querySelectorAll('button')];
    const closest=buttons.reduce((best,button)=>Math.abs(button.offsetLeft+button.offsetWidth/2-center)<Math.abs(best.offsetLeft+best.offsetWidth/2-center)?button:best,buttons[0]);
    if(closest&&!closest.classList.contains('active'))selectCentered(strip,closest);
  }});clearTimeout(pending);pending=setTimeout(()=>{{userScrolling=false;const active=strip.querySelector('button.active');centerChoice(strip,active)}},160)}});
}}
enableSlideSelection(days);enableSlideSelection(hours);
let timer=null;function stop(){{clearInterval(timer);timer=null;if(mapPlay)mapPlay.textContent='▶'}}function toggle(){{if(timer)return stop();if(currentIndex===data.frames.length-1)show(0);mapPlay.textContent='⏸';
  timer=setInterval(()=>{{if(currentIndex>=data.frames.length-1)return stop();show(currentIndex+1)}},{speed});
}}
document.querySelector('#close-details').onclick=()=>{{details.hidden=true;selectedTownId=null;selectedDetailDate=null;document.querySelector('main').classList.remove('details-open');setTimeout(()=>map.invalidateSize(),0)}};
document.addEventListener('keydown',event=>{{if(event.key==='ArrowLeft'){{stop();show(currentIndex-1)}}if(event.key==='ArrowRight'){{stop();show(currentIndex+1)}}if(event.key===' '){{event.preventDefault();toggle()}}}});
map.on('zoomend resize',drawRouteArrows);map.on('zoomend moveend resize',layoutLabels);map.whenReady(()=>show(0));
if('serviceWorker' in navigator)navigator.serviceWorker.register('{escape(config.github_pages_base_url)}/sw.js');
</script></body></html>"""


def main():
    forecasts = load_data()
    route, route_distance_km, route_profile = load_track(config.gpx_file)
    source_is_public = (
        os.path.abspath(config.gpx_file)
        == os.path.abspath(config.production_gpx_path)
    )
    if source_is_public and os.path.exists(config.production_profile_path):
        route, _, _ = load_track(config.production_gpx_path, simplify=False)
        with open(config.production_profile_path, "r", encoding="utf-8") as handle:
            stored_profile = json.load(handle)
        route_distance_km = stored_profile["distance_km"]
        route_profile = stored_profile["profile"]
    elif not source_is_public:
        # La carte en ligne affiche exactement le GPX public kilométrique ; le
        # profil d'effort reste calculé sur l'original pour préserver le D+.
        route, _, _ = load_track(config.production_gpx_path, simplify=False)
        with open(config.production_profile_path, "w", encoding="utf-8") as handle:
            json.dump(
                {"distance_km": route_distance_km, "profile": route_profile},
                handle, ensure_ascii=False, separators=(",", ":"),
            )
    payload = make_payload(forecasts, route, route_distance_km, route_profile)
    if not payload["frames"]: raise ValueError("Aucune échéance à afficher")
    with open(config.html_path, "w", encoding="utf-8") as handle:
        handle.write(build_html(payload))
    print(f"Carte Leaflet : {config.html_path} ({len(payload['frames'])} échéances)")


if __name__ == "__main__": main()
