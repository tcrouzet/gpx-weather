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


def load_track(path):
    with open(path, "r", encoding="utf-8") as handle:
        gpx = gpxpy.parse(handle)
    points = [p for track in gpx.tracks for segment in track.segments for p in segment.points]
    if not points:
        points = [p for route in gpx.routes for p in route.points]
    coordinates = [(p.longitude, p.latitude) for p in points]
    tolerance = getattr(config, "gpx_simplify_degrees", .0003)
    simplified = LineString(coordinates).simplify(tolerance, preserve_topology=False)
    return [[lat, lon] for lon, lat in simplified.coords]


def load_data():
    forecasts = pd.read_csv(config.csv_path, parse_dates=["time"])
    forecasts["time"] = forecasts["time"].dt.tz_convert("Europe/Paris")
    return forecasts


def selected_times(forecasts):
    hours = set(config.sample_hours)
    current_hour = pd.Timestamp(datetime.now(ZoneInfo("Europe/Paris"))).floor("h")
    return [pd.Timestamp(t) for t in sorted(forecasts["time"].unique())
            if pd.Timestamp(t) >= current_hour
            and pd.Timestamp(t).hour in hours and pd.Timestamp(t).minute == 0]


def make_payload(forecasts, route):
    current_hour = pd.Timestamp(datetime.now(ZoneInfo("Europe/Paris"))).floor("h")
    forecasts = forecasts[forecasts["time"] >= current_hour].copy()
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
            daily.append({
                "date": date.isoformat(),
                "weekday": day_abbreviations[date.weekday()],
                "day": date.day,
                "weather": category,
                "temperature_max": round(float(rows["temperature"].max())),
                "temperature_min": round(float(rows["temperature"].min())),
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
            "lat": float(town_row.lat), "lon": float(town_row.lon), "daily": daily,
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
    return {"route": route, "towns": towns, "frames": frames}


def build_html(payload):
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    title = escape(getattr(config, "project", "Prévisions météo"))
    page_url = escape(config.github_pages_url, quote=True)
    preview_url = escape(f"{config.github_pages_url}preview.png", quote=True)
    route_links = "".join(
        f'<a href="{escape(config.github_pages_base_url)}/{escape(config.route_slug_for(path))}/">'
        f'{escape(config.route_title_for(path))}</a>'
        for path in config.list_gpx_files()
    )
    menu_html = (
        f'<a href="{escape(config.github_pages_base_url)}/">Accueil et aide</a>'
        f'{route_links}'
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
.title{{position:relative;flex:0 0 auto;background:#18295c;color:white;padding:6px 48px;text-align:center;
font-weight:800;font-size:clamp(14px,1.7vw,22px);line-height:1.1}}
.menu-button{{position:absolute;left:4px;top:0;width:42px;height:100%;border:0;background:transparent;color:#fff;font-size:0;cursor:pointer;display:grid;place-items:center}}
.menu-button::before{{content:"";width:25px;height:3px;border-radius:2px;background:#fff;box-shadow:0 -8px #fff,0 8px #fff}}
.route-menu{{position:absolute;z-index:2000;left:8px;top:38px;
min-width:220px;background:#fff;border-radius:10px;padding:6px;box-shadow:0 5px 20px #0005;text-align:left}}
.route-menu[hidden]{{display:none}} .route-menu a{{display:block;padding:9px 11px;border-radius:7px;color:#17234d;text-decoration:none;font-size:14px;font-weight:650}}
.route-menu a:hover{{background:#edf1fa}}
.leaflet-overlay-pane svg{{z-index:450}}
.meteo-marker{{width:1px!important;height:1px!important;text-align:center;line-height:1;pointer-events:auto}}
.temperature,.weather{{position:absolute;left:0;top:0}}
.temperature{{color:#111;font-size:18px;font-weight:900;white-space:nowrap;
text-shadow:-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,1px 1px 0 #fff}}
.weather{{transform:translate(-50%,-50%);font-size:42px;filter:drop-shadow(0 1px 2px white)}}
.details{{flex:1;min-height:0;overflow:hidden;background:#fff;color:#17234d;line-height:1.2}}
.details[hidden]{{display:none}} main.details-open #map,main.details-open .controls{{display:none}}
.details-shell{{width:min(920px,100%);height:100%;margin:auto;padding:8px 14px;display:flex;flex-direction:column;overflow:hidden}}
.sheet-head{{position:relative;flex:0 0 32px;display:flex;align-items:center;margin-bottom:4px}}
.sheet-head h2{{position:absolute;left:50%;width:70%;transform:translateX(-50%);font-size:20px;text-align:center;margin:0;color:#111}}
.close-details{{position:absolute;z-index:1;left:0;top:50%;transform:translateY(-50%);border:1px solid #ddd;background:#f6f6f8;border-radius:18px;padding:6px 12px;font-size:13px;cursor:pointer}}
.daily-strip{{flex:0 0 70px;display:flex;gap:0;overflow:hidden;padding:3px 0;border-bottom:1px solid #ddd}}
.daily-choice{{flex:1 1 0;min-width:0;border:0;background:transparent;border-radius:8px;padding:2px 0;
display:grid;grid-template-columns:1fr;grid-template-rows:28px 14px 20px;place-items:center;color:#70768c;cursor:pointer}}
.daily-choice .daily-icon{{font-size:21px;line-height:1}} .daily-choice .daily-weekday{{font-size:9px;line-height:1}} .daily-choice .daily-number{{font-size:14px;line-height:1;font-weight:800;color:#315bb5}}
.daily-choice.active{{background:#f6a800;color:#fff}} .daily-choice.active .daily-number{{color:#fff}}
#details-content{{flex:1;min-height:0;display:flex;flex-direction:column;overflow:hidden}}
.forecast-chart{{flex:1 1 0;min-height:0;background:#fafafd;border-radius:10px;overflow:hidden}}
.forecast-chart svg{{display:block;width:100%;height:100%}}
.metric-grid{{flex:0 0 auto;display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:7px}}
.metric{{min-height:58px;background:#ececf2;border-radius:9px;padding:7px 9px}} .metric-label{{display:block;font-size:10px;color:#68708c;margin-bottom:3px}}
.metric-value{{display:block;font-size:15px;font-weight:800;color:#17234d}} .metric.temperature-card{{background:#ff4b4f}} .metric.gust-card{{background:#55c94c}}
.metric.temperature-card *,.metric.gust-card *{{color:#fff}} .metric.rain-card{{background:#e8edf8}} .metric.wind-card{{background:#e8edf8}}
.detail-source{{flex:0 0 auto;margin:5px 2px 0;text-align:center;font-size:10px;color:#68708c}} .detail-source a{{color:#315bb5;font-weight:700}}
@media(max-width:600px){{.details-shell{{padding:7px 7px}}.sheet-head h2{{font-size:18px}}.metric-grid{{grid-template-columns:repeat(2,1fr);gap:5px}}
.metric{{min-height:50px;padding:5px 7px}}.metric-value{{font-size:14px}}}}
@media(max-height:680px){{.daily-strip{{flex-basis:62px}}.daily-choice{{grid-template-rows:24px 12px 18px}}.daily-choice .daily-icon{{font-size:18px}}
.metric{{min-height:45px}}.detail-source{{margin-top:3px}}}}
.controls{{height:112px;flex:0 0 112px;background:#18295c;color:#fff;padding:5px 0}}
.timeline{{height:100%;display:grid;grid-template-rows:1fr 1fr;gap:4px}}
.strip-wrap{{position:relative;min-width:0;overflow:hidden}} .strip-wrap::after{{content:"";position:absolute;z-index:0;
left:50%;top:2px;bottom:2px;width:80px;transform:translateX(-50%);border-radius:8px;background:#f6a800;pointer-events:none}}
.strip{{position:relative;z-index:1;height:100%;display:flex;align-items:stretch;gap:5px;overflow-x:auto;padding:2px calc(50% - 40px);
scroll-snap-type:x mandatory;scrollbar-width:none;overscroll-behavior-x:contain;-webkit-overflow-scrolling:touch}}
.strip::-webkit-scrollbar{{display:none}} .strip button{{flex:0 0 80px;width:80px;border:0;border-radius:8px;
background:transparent;color:#fff;font-size:15px;font-weight:650;scroll-snap-align:center;cursor:pointer;padding:7px 12px}}
.strip button.active{{color:#fff;font-weight:850}}
.map-play{{width:56px;height:56px;border:0;border-radius:50%;background:#352d32e8;color:#fff;font-size:27px;
display:grid;place-items:center;cursor:pointer;box-shadow:0 2px 8px #0005}}
</style></head><body><main><header class="title"><button id="menu-button" class="menu-button" aria-label="Ouvrir le menu">☰</button>{title}</header><nav id="route-menu" class="route-menu" hidden>{menu_html}</nav><div id="map"></div>
<div class="controls"><div class="timeline"><div class="strip-wrap"><div id="days" class="strip days"></div></div><div class="strip-wrap"><div id="hours" class="strip hours"></div></div></div></div>
<aside id="details" class="details" hidden><div class="details-shell"><div class="sheet-head"><button id="close-details" class="close-details">Fermer</button><h2 id="details-title"></h2><span></span></div><div id="daily-strip" class="daily-strip"></div><div id="details-content"></div></div></aside>
</main><script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const data={data},currentHour=new Date();currentHour.setMinutes(0,0,0);
data.frames=data.frames.filter(frame=>new Date(frame.iso)>=currentHour);
const icons={{clear:'☀️',partly_cloudy:'🌤️',cloudy:'☁️',fog:'🌫️',drizzle:'🌦️',rain:'🌧️',snow:'🌨️',storm:'⛈️',unknown:'❔'}},
weatherNames={{clear:'Ciel dégagé',partly_cloudy:'Éclaircies',cloudy:'Nuageux',fog:'Brouillard',drizzle:'Bruine',rain:'Pluie',snow:'Neige',storm:'Orage',unknown:'Indéterminé'}};
const menuButton=document.querySelector('#menu-button'),routeMenu=document.querySelector('#route-menu');
menuButton.onclick=event=>{{event.stopPropagation();routeMenu.hidden=!routeMenu.hidden}};
document.addEventListener('click',event=>{{if(!routeMenu.contains(event.target)&&event.target!==menuButton)routeMenu.hidden=true}});
document.addEventListener('keydown',event=>{{if(event.key==='Escape')routeMenu.hidden=true}});
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
function dailyChart(town,selectedIndex){{const rows=town.daily,columnWidth=40,width=rows.length*columnWidth,height=350,tempTop=24,tempBottom=190,windBase=300;
  const temperatures=rows.flatMap(row=>[row.temperature_min,row.temperature_max]),tempMin=Math.floor(Math.min(...temperatures)-1),tempMax=Math.ceil(Math.max(...temperatures)+1);
  const maxWind=Math.max(1,...rows.map(row=>row.wind)),x=index=>columnWidth/2+index*columnWidth;
  const yTemp=value=>tempTop+(tempMax-value)/(tempMax-tempMin)*(tempBottom-tempTop),barWidth=24;
  const maxPoints=rows.map((row,index)=>`${{x(index)}},${{yTemp(row.temperature_max)}}`).join(' '),minPoints=rows.map((row,index)=>`${{x(index)}},${{yTemp(row.temperature_min)}}`).join(' ');
  const columns=rows.map((row,index)=>{{const px=x(index),barHeight=row.wind/maxWind*78,selected=index===selectedIndex;
    return `<rect x="${{px-barWidth/2}}" y="18" width="${{barWidth}}" height="${{windBase+6}}" rx="3" fill="${{selected?'#c9cdd6':'#eef0f4'}}" opacity="${{selected?.75:.7}}"/>
    <rect x="${{px-barWidth/2}}" y="${{windBase-barHeight}}" width="${{barWidth}}" height="${{barHeight}}" rx="3" fill="#50c744"/>
    <text x="${{px}}" y="${{windBase-barHeight-5}}" text-anchor="middle" font-size="10" fill="#389b31">${{row.wind}}</text>
    <text x="${{px}}" y="${{windBase+16}}" text-anchor="middle" font-size="17" fill="#17234d" transform="rotate(${{row.wind_degrees}} ${{px}} ${{windBase+11}})">↑</text>
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
  <div class="metric rain-card"><span class="metric-label">Précipitations</span><span class="metric-value">${{selected.precipitation}} mm</span></div>
  <div class="metric"><span class="metric-label">Conditions</span><span class="metric-value">${{icons[selected.weather]}} ${{weatherNames[selected.weather]}}</span></div>
  <div class="metric wind-card"><span class="metric-label">Vent maximal</span><span class="metric-value">${{selected.wind}} km/h · ${{selected.wind_direction}}</span></div>
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
function indexFor(day,hour){{const exact=data.frames.findIndex(frame=>frame.day===day&&frame.hour===hour);if(exact>=0)return exact;
  const candidates=data.frames.map((frame,index)=>({{frame,index}})).filter(item=>item.frame.day===day);
  return candidates.sort((a,b)=>Math.abs(a.frame.hour-hour)-Math.abs(b.frame.hour-hour))[0]?.index??currentIndex;
}}
const seenDays=new Set();data.frames.forEach(frame=>{{if(seenDays.has(frame.day))return;seenDays.add(frame.day);const button=document.createElement('button');
  button.dataset.day=frame.day;button.textContent=frame.day_label;button.onclick=()=>{{stop();show(indexFor(frame.day,data.frames[currentIndex].hour))}};days.append(button);
}});
function renderHours(day){{const available=[...new Set(data.frames.filter(frame=>frame.day===day).map(frame=>frame.hour))].sort((a,b)=>a-b);
  const displayed=[...hours.querySelectorAll('button')].map(button=>Number(button.dataset.hour));if(displayed.join(',')===available.join(','))return;
  hours.replaceChildren();available.forEach(hour=>{{const button=document.createElement('button');button.dataset.hour=hour;
    button.textContent=`${{String(hour).padStart(2,'0')}}:00`;button.onclick=()=>{{stop();show(indexFor(day,hour))}};hours.append(button)}});
}}
function selectCentered(strip,button){{if(!button)return;stop();if(strip===days)show(indexFor(button.dataset.day,data.frames[currentIndex].hour),strip);
  else show(indexFor(data.frames[currentIndex].day,Number(button.dataset.hour)),strip);
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
map.on('zoomend moveend resize',layoutLabels);map.whenReady(()=>show(0));
if('serviceWorker' in navigator)navigator.serviceWorker.register('{escape(config.github_pages_base_url)}/sw.js');
</script></body></html>"""


def main():
    forecasts = load_data()
    payload = make_payload(forecasts, load_track(config.gpx_file))
    if not payload["frames"]: raise ValueError("Aucune échéance à afficher")
    with open(config.html_path, "w", encoding="utf-8") as handle:
        handle.write(build_html(payload))
    print(f"Carte Leaflet : {config.html_path} ({len(payload['frames'])} échéances)")


if __name__ == "__main__": main()
