#!/usr/bin/env python3
"""
Windsurf-Dashboard-Generator
=============================
Baut eine statische HTML-Seite (docs/index.html) mit der aktuellen
Windvorhersage für alle Spots aus spots.json – zur Veröffentlichung über
GitHub Pages als installierbare PWA (Progressive Web App).

Nutzt dieselben Funktionen wie wind_check.py, damit die Logik (Anfahrt-Filter,
Shore-Klassifizierung, Möhnesee-Ortshinweise etc.) an genau einer Stelle
gepflegt wird.
"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import shutil
import sys

# wind_check.py liegt im Nachbarordner push-benachrichtigung/ – dort liegt die
# gesamte Windcheck-Logik (Anfahrt-Filter, Shore-Klassifizierung, Möhnesee-
# Ortshinweise etc.). Bewusst nicht dupliziert, damit es nur eine Quelle für
# diese Logik gibt.
sys.path.insert(0, str(Path(__file__).parent.parent / "push-benachrichtigung"))
import wind_check as wc

OUTPUT_DIR = Path(__file__).parent / "site"
ICON_SOURCE = Path(__file__).parent / "icon.png"


def render_day_badge(day):
    wd = wc.WEEKDAYS_DE_KURZ[day["date"].weekday()]

    if not day["qualifies"]:
        if day.get("avg_speed") is None:
            return f'<div class="day weak"><div class="wd">{wd}</div><div class="val">–</div></div>'
        return f'''<div class="day weak">
  <div class="wd">{wd}</div>
  <div class="val">{day['avg_speed']}/{day['avg_gust']}kn</div>
</div>'''

    stern = " ⭐" if day["label"] == "top" else ""
    zeitspanne = f"{day['start'].strftime('%H')}-{day['end'].strftime('%H')}h"
    shore = f'<div class="tag">{day["shore"]}</div>' if day.get("shore") else ""
    ort = f'<div class="tag ort">📍 {day["ort"]}</div>' if day.get("ort") else ""
    vorabend = f'<div class="tag ort">🌙 {day["vorabend_hinweis"]}</div>' if day.get("vorabend_hinweis") else ""

    return f'''<div class="day good">
  <div class="wd">{wd}{stern}</div>
  <div class="time">{zeitspanne}</div>
  <div class="val">{day['avg_speed']}/{day['avg_gust']}kn</div>
  <div class="compass">{day['compass']}</div>
  {shore}
  {ort}
  {vorabend}
</div>'''


def render_spot_section(spot, days):
    badges = "\n".join(render_day_badge(d) for d in days)
    reise = f"{spot.get('travel_hours', 0):g}h Anfahrt"
    if spot.get("multi_day_only"):
        reise += " · mehrtägiger Trip"
    return f'''<section class="spot">
  <h2>🏄 {spot["name"]} <span class="meta">({reise})</span></h2>
  <div class="days">
{badges}
  </div>
</section>'''


def build_html(spots, days_by_spot, updated):
    sections = "\n".join(
        render_spot_section(spot, days_by_spot.get(spot["name"], []))
        for spot in spots
    )

    return f'''<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Windsurf-Vorhersage</title>
<link rel="manifest" href="manifest.json">
<link rel="apple-touch-icon" href="icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Windsurf">
<meta name="theme-color" content="#0b5fa5">
<style>
  * {{ box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0b1e33;
    color: #f2f6fa;
    margin: 0;
    padding: 20px 16px 40px;
  }}
  h1 {{ font-size: 1.5em; margin: 0 0 4px; }}
  .updated {{ color: #8fa8c4; font-size: 0.85em; margin-bottom: 24px; }}
  .spot {{ margin-bottom: 28px; }}
  .spot h2 {{
    font-size: 1.1em;
    border-bottom: 1px solid #234161;
    padding-bottom: 8px;
    margin-bottom: 12px;
  }}
  .spot h2 .meta {{ color: #8fa8c4; font-weight: 400; font-size: 0.8em; }}
  .days {{ display: flex; flex-wrap: wrap; gap: 10px; }}
  .day {{
    background: #14304f;
    border-radius: 12px;
    padding: 10px 14px;
    min-width: 92px;
  }}
  .day.good {{ background: #0f4c2f; }}
  .day .wd {{ font-weight: 600; font-size: 0.95em; }}
  .day .val {{ font-size: 1.15em; margin-top: 4px; font-weight: 600; }}
  .day .time, .day .compass {{ font-size: 0.8em; color: #bcd0e6; margin-top: 2px; }}
  .day .tag {{ font-size: 0.72em; color: #9db4cc; margin-top: 4px; max-width: 160px; }}
  footer {{ margin-top: 32px; color: #5f7691; font-size: 0.75em; }}
</style>
</head>
<body>
  <h1>🌬️ Windsurf-Vorhersage</h1>
  <div class="updated">Stand: {updated} Uhr</div>
{sections}
  <footer>Automatisch generiert · Datenquelle: Open-Meteo</footer>
</body>
</html>'''


MANIFEST_JSON = '''{
  "name": "Windsurf-Vorhersage",
  "short_name": "Windsurf",
  "start_url": "./index.html",
  "display": "standalone",
  "background_color": "#0b1e33",
  "theme_color": "#0b5fa5",
  "icons": [
    { "src": "icon.png", "sizes": "180x180", "type": "image/png" }
  ]
}
'''


def main():
    spots, default_min_knots = wc.load_spots()
    tz = ZoneInfo(wc.TIMEZONE)
    now = datetime.now(tz)

    days_by_spot = {}
    for spot in spots:
        try:
            hourly = wc.fetch_forecast(spot["latitude"], spot["longitude"])
            days_by_spot[spot["name"]] = wc.evaluate_spot_days(spot, hourly, default_min_knots, now)
        except Exception as e:
            print(f"Fehler bei Spot {spot['name']}: {e}")
            days_by_spot[spot["name"]] = []

    updated = now.strftime("%d.%m.%Y %H:%M")
    html = build_html(spots, days_by_spot, updated)

    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "index.html").write_text(html, encoding="utf-8")
    (OUTPUT_DIR / "manifest.json").write_text(MANIFEST_JSON, encoding="utf-8")
    if ICON_SOURCE.exists():
        shutil.copy(ICON_SOURCE, OUTPUT_DIR / "icon.png")

    print(f"Dashboard geschrieben: {OUTPUT_DIR / 'index.html'}")


if __name__ == "__main__":
    main()
