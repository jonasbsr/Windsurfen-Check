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
import html
import json
import re
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


def slugify(name):
    """Erzeugt eine URL-/ID-taugliche Kurzform aus dem Spot-Namen (für die
    Canvas-Element-IDs der Diagramme, z.B. 'Strand Horst' -> 'strand-horst')."""
    umlaute = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
    name = name.lower()
    for k, v in umlaute.items():
        name = name.replace(k, v)
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def render_good_day_html(day):
    """Rendert einen guten Tag fürs Dashboard: Wochentag + Uhrzeit groß/fett
    als eigene Zeile, danach die restlichen Infos (Wind/Böen, Richtung,
    Shore-Typ, Ort, Vorabend-Hinweis) wie gehabt darunter."""
    wd_full = wc.WEEKDAYS_DE_LANG[day["date"].weekday()]
    zeitspanne = f"{day['start'].strftime('%H')}-{day['end'].strftime('%H')}h"
    header = html.escape(f"{wd_full} {zeitspanne}", quote=False)

    stern = "⭐ " if day["label"] == "top" else ""
    shore_str = f" {day['shore']}" if day.get("shore") else ""
    lines = [f"{stern}{day['avg_speed']}/{day['avg_gust']}kn {day['compass']}{shore_str}"]

    if day.get("ort"):
        lines.append(f"📍 {day['ort']}")
    if day.get("vorabend_hinweis"):
        lines.append(f"🌙 {day['vorabend_hinweis']}")

    body = "<br>".join(html.escape(line, quote=False) for line in lines)

    return f'''<div class="goodday">
  <div class="day-header">{header}</div>
  <div class="day-body">{body}</div>
</div>'''


def render_good_days_text(days):
    """Zeigt die guten Tage fürs Dashboard. Gibt einen leeren String zurück,
    wenn kein Tag die Kriterien erfüllt – dann bleibt nur das Diagramm stehen."""
    good_days = [d for d in days if d["qualifies"]]
    if not good_days:
        return ""

    blocks = [render_good_day_html(day) for day in good_days]
    return f'<div class="goodtext">{"".join(blocks)}</div>'


def render_spot_section(spot, days):
    reise = f"{spot.get('travel_hours', 0):g}h Anfahrt"
    if spot.get("multi_day_only"):
        reise += " · mehrtägiger Trip"
    slug = slugify(spot["name"])
    good_html = render_good_days_text(days)
    return f'''<section class="spot">
  <h2>🏄 {spot["name"]} <span class="meta">({reise})</span></h2>
  <div class="chart-wrap"><canvas id="chart-{slug}" height="160"></canvas></div>
  {good_html}
</section>'''


def build_chart_data(spots, days_by_spot):
    """Baut die JSON-Daten für die Chart.js-Diagramme: pro Spot Labels
    (Wochentage), Windstärke- und Böen-Werte sowie eine Balkenfarbe je nach
    dem, ob der Tag die Mindestkriterien erfüllt."""
    charts = []
    for spot in spots:
        days = days_by_spot.get(spot["name"], [])
        charts.append({
            "slug": slugify(spot["name"]),
            "labels": [wc.WEEKDAYS_DE_KURZ[d["date"].weekday()] for d in days],
            "speeds": [d.get("avg_speed") or 0 for d in days],
            "gusts": [d.get("avg_gust") or 0 for d in days],
            "colors": ["#2ecc71" if d["qualifies"] else "#2d5c8a" for d in days],
        })
    return charts


def build_html(spots, days_by_spot, updated):
    sections = "\n".join(
        render_spot_section(spot, days_by_spot.get(spot["name"], []))
        for spot in spots
    )
    chart_data_json = json.dumps(build_chart_data(spots, days_by_spot), ensure_ascii=False)

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
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
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
  .chart-wrap {{
    background: #0f2438;
    border-radius: 12px;
    padding: 12px 8px;
    margin-bottom: 14px;
  }}
  .goodtext {{ display: flex; flex-direction: column; gap: 10px; }}
  .goodday {{
    background: #0f4c2f;
    border-radius: 12px;
    padding: 12px 14px;
  }}
  .goodday .day-header {{
    font-weight: 700;
    font-size: 1.15em;
    margin-bottom: 8px;
  }}
  .goodday .day-body {{
    line-height: 1.6;
    font-size: 0.92em;
  }}
  footer {{ margin-top: 32px; color: #5f7691; font-size: 0.75em; }}
</style>
</head>
<body>
  <h1>🌬️ Windsurf-Vorhersage</h1>
  <div class="updated">Stand: {updated} Uhr</div>
{sections}
  <footer>Automatisch generiert · Datenquelle: Open-Meteo</footer>

<script>
const CHART_DATA = {chart_data_json};

document.addEventListener('DOMContentLoaded', () => {{
  CHART_DATA.forEach(cfg => {{
    const el = document.getElementById('chart-' + cfg.slug);
    if (!el) return;
    new Chart(el, {{
      data: {{
        labels: cfg.labels,
        datasets: [
          {{
            type: 'bar',
            label: 'Wind (kn)',
            data: cfg.speeds,
            backgroundColor: cfg.colors,
            borderRadius: 4,
            order: 2,
          }},
          {{
            type: 'line',
            label: 'Böen (kn)',
            data: cfg.gusts,
            borderColor: '#f2c14e',
            borderDash: [4, 3],
            pointRadius: 2,
            pointBackgroundColor: '#f2c14e',
            fill: false,
            tension: 0.25,
            order: 1,
          }},
        ],
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
          legend: {{ labels: {{ color: '#bcd0e6', boxWidth: 12, font: {{ size: 11 }} }} }},
        }},
        scales: {{
          x: {{ ticks: {{ color: '#bcd0e6' }}, grid: {{ color: '#1c3552' }} }},
          y: {{ ticks: {{ color: '#bcd0e6' }}, grid: {{ color: '#1c3552' }}, beginAtZero: true }},
        }},
      }},
    }});
  }});
}});
</script>
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
