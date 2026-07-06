#!/usr/bin/env python3
"""Génère la visualisation météo interactive Leaflet/OSM."""

import json
import math
import os
from html import escape

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
    return [pd.Timestamp(t) for t in sorted(forecasts["time"].unique())
            if pd.Timestamp(t).hour in hours and pd.Timestamp(t).minute == 0]


def make_payload(forecasts, route):
    towns_frame = (forecasts.sort_values("time").drop_duplicates("point_index")
                   .sort_values("point_index"))
    towns = [{"id": str(int(row.point_index)), "name": row["name"],
              "lat": float(row.lat), "lon": float(row.lon)}
             for _, row in towns_frame.iterrows()]
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
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
*{{box-sizing:border-box}} html,body{{height:100%;margin:0;overflow:hidden;font-family:system-ui,sans-serif}}
main{{height:100dvh;display:flex;flex-direction:column;background:#171717}} #map{{min-height:0;flex:1}}
.title{{flex:0 0 auto;background:#111;color:white;padding:8px 18px;text-align:center;
font-weight:800;font-size:clamp(15px,2.3vw,30px);line-height:1.15}}
.leaflet-overlay-pane svg{{z-index:450}}
.meteo-marker{{width:1px!important;height:1px!important;text-align:center;line-height:1;pointer-events:auto}}
.temperature,.weather{{position:absolute;left:0;top:0}}
.temperature{{color:#111;font-size:18px;font-weight:900;white-space:nowrap;
text-shadow:-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,1px 1px 0 #fff}}
.weather{{transform:translate(-50%,-50%);font-size:42px;filter:drop-shadow(0 1px 2px white)}}
.details{{position:absolute;z-index:1000;right:10px;bottom:10px;width:min(290px,calc(100% - 20px));
background:#fffffff2;color:#171717;border-radius:8px;padding:12px 14px;box-shadow:0 2px 14px #0005;line-height:1.35}}
.details[hidden]{{display:none}} .details h2{{font-size:17px;margin:0 25px 6px 0}} .details p{{margin:4px 0}}
.details button{{position:absolute;right:6px;top:5px;border:0;background:none;font-size:20px;cursor:pointer}}
.controls{{height:112px;flex:0 0 112px;background:#18295c;color:#fff;padding:5px 0}}
.timeline{{height:100%;display:grid;grid-template-rows:1fr 1fr;gap:4px}}
.strip{{display:flex;align-items:stretch;gap:5px;overflow-x:auto;padding:2px max(12px,calc(50vw - 45px));
scroll-snap-type:x mandatory;scrollbar-width:none;overscroll-behavior-x:contain;-webkit-overflow-scrolling:touch}}
.strip::-webkit-scrollbar{{display:none}} .strip button{{flex:0 0 auto;min-width:74px;border:0;border-radius:8px;
background:transparent;color:#fff;font-size:15px;font-weight:650;scroll-snap-align:center;cursor:pointer;padding:7px 12px}}
.hours button{{min-width:64px}} .strip button.active{{background:#f6a800;color:#fff;font-weight:850}}
.map-play{{width:56px;height:56px;border:0;border-radius:50%;background:#352d32e8;color:#fff;font-size:27px;
display:grid;place-items:center;cursor:pointer;box-shadow:0 2px 8px #0005}}
</style></head><body><main><div class="title">{title}</div><div id="map">
<aside id="details" class="details" hidden><button id="close-details" aria-label="Fermer">×</button><div id="details-content"></div></aside></div>
<div class="controls"><div class="timeline"><div id="days" class="strip days"></div><div id="hours" class="strip hours"></div></div></div>
</main><script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const data={data}, icons={{clear:'☀️',partly_cloudy:'🌤️',cloudy:'☁️',fog:'🌫️',drizzle:'🌦️',rain:'🌧️',snow:'🌨️',storm:'⛈️',unknown:'❔'}};
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
let currentIndex=0,selectedTownId=null,mapPlay=null;
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
function showDetails(id){{selectedTownId=id;const town=townById[id],frame=data.frames[currentIndex],v=frame.values[id];if(!v)return;
  const source=v.ensemble?'Médiane de 51 scénarios ECMWF':'Modèle local haute résolution';
  const sourceUrl=v.ensemble?'https://open-meteo.com/en/docs/ensemble-api':'https://open-meteo.com/en/docs';
  const uncertainty=v.ensemble?`<p><b>Plage probable :</b> ${{v.low}} à ${{v.high}}°C</p>`:'';
  const rainProbability=v.probability===null?'':`<p><b>Probabilité de pluie :</b> ${{v.probability}} %</p>`;
  const precipitation=v.precipitation>0?`<p><b>Quantité de pluie :</b> ${{v.precipitation}} mm${{v.ensemble?' en moyenne':''}}</p>`:'';
  document.querySelector('#details-content').innerHTML=`<h2>${{escapeHtml(town.name)}} — ${{frame.label}}</h2>
  <p><b>Température :</b> ${{v.temperature}}°C</p>${{uncertainty}}${{rainProbability}}${{precipitation}}
  <p><b>Vent :</b> ${{v.wind}} km/h, direction ${{v.wind_direction}}, rafales ${{v.gusts}} km/h</p><p><small>${{source}}${{v.ensemble?' — incertitude croissante avec l’échéance.':''}}<br>
  <a href="${{sourceUrl}}" target="_blank" rel="noopener">Source des données météo</a></small></p>`;
  details.hidden=false;
}}
function show(i){{currentIndex=Math.max(0,Math.min(data.frames.length-1,i));const f=data.frames[currentIndex];
  for(const [id,m] of Object.entries(markers)){{const v=f.values[id],e=m.getElement();if(!e||!v)continue;const temp=e.querySelector('.temperature');
    temp.textContent=v.temperature+'°';e.querySelector('.weather').textContent=icons[v.weather];
    e.title=v.ensemble?`${{v.low}} à ${{v.high}}°C (80 % des scénarios)`:'';
  }}
  const activeDay=days.querySelector(`[data-day="${{f.day}}"]`),activeHour=hours.querySelector(`[data-hour="${{f.hour}}"]`);
  days.querySelectorAll('button').forEach(button=>button.classList.toggle('active',button===activeDay));
  hours.querySelectorAll('button').forEach(button=>button.classList.toggle('active',button===activeHour));
  centerChoice(days,activeDay);centerChoice(hours,activeHour);
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
const uniqueHours=[...new Set(data.frames.map(frame=>frame.hour))].sort((a,b)=>a-b);uniqueHours.forEach(hour=>{{const button=document.createElement('button');
  button.dataset.hour=hour;button.textContent=`${{String(hour).padStart(2,'0')}}:00`;button.onclick=()=>{{stop();show(indexFor(data.frames[currentIndex].day,hour))}};hours.append(button);
}});
function enableSlideSelection(strip){{let pending,userScrolling=false;const arm=()=>{{userScrolling=true}};strip.addEventListener('pointerdown',arm);
  strip.addEventListener('touchstart',arm,{{passive:true}});strip.addEventListener('wheel',arm,{{passive:true}});
  strip.addEventListener('scroll',()=>{{if(!userScrolling)return;clearTimeout(pending);pending=setTimeout(()=>{{
    const center=strip.scrollLeft+strip.clientWidth/2,buttons=[...strip.querySelectorAll('button')];
    const closest=buttons.reduce((best,button)=>Math.abs(button.offsetLeft+button.offsetWidth/2-center)<Math.abs(best.offsetLeft+best.offsetWidth/2-center)?button:best,buttons[0]);
    userScrolling=false;if(closest&&!closest.classList.contains('active'))closest.click();
  }},180)}});
}}
enableSlideSelection(days);enableSlideSelection(hours);
let timer=null;function stop(){{clearInterval(timer);timer=null;if(mapPlay)mapPlay.textContent='▶'}}function toggle(){{if(timer)return stop();if(currentIndex===data.frames.length-1)show(0);mapPlay.textContent='⏸';
  timer=setInterval(()=>{{if(currentIndex>=data.frames.length-1)return stop();show(currentIndex+1)}},{speed});
}}
document.querySelector('#close-details').onclick=()=>{{details.hidden=true;selectedTownId=null}};
document.addEventListener('keydown',event=>{{if(event.key==='ArrowLeft'){{stop();show(currentIndex-1)}}if(event.key==='ArrowRight'){{stop();show(currentIndex+1)}}if(event.key===' '){{event.preventDefault();toggle()}}}});
map.on('zoomend moveend resize',layoutLabels);map.whenReady(()=>show(0));
</script></body></html>"""


def main():
    forecasts = load_data()
    payload = make_payload(forecasts, load_track(config.gpx_file))
    if not payload["frames"]: raise ValueError("Aucune échéance à afficher")
    with open(config.html_path, "w", encoding="utf-8") as handle:
        handle.write(build_html(payload))
    print(f"Carte Leaflet : {config.html_path} ({len(payload['frames'])} échéances)")


if __name__ == "__main__": main()
