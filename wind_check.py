#!/usr/bin/env python3
"""
Windsurf-Vorhersage-Check
==========================
Holt für alle in spots.json definierten Spots die stündliche Windvorhersage
von Open-Meteo (kostenlos, kein API-Key nötig), bewertet jede Stunde anhand
der spot-spezifischen Kriterien (Mindestwind, Windrichtung, ggf. Böigkeit)
und schickt eine zusammenfassende Empfehlung per Pushover.

Neuen Spot hinzufügen: siehe spots.json (kein Code ändern nötig).
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SPOTS_FILE = Path(__file__).parent / "spots.json"
FORECAST_DAYS = 4          # wie viele Tage vorausschauen
DAY_START_HOUR = 8         # nur Stunden zwischen 8 und 20 Uhr lokal betrachten
DAY_END_HOUR = 20
DEPARTURE_HOUR = 8.5       # realistische Abfahrtszeit (Mittelwert von 8-9 Uhr), für Anfahrt-Filter bei Tagesausflug-Spots
TIMEZONE = "Europe/Berlin"

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN")
PUSHOVER_USER = os.environ.get("PUSHOVER_USER")


def load_spots():
    with open(SPOTS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["spots"], data.get("default_min_knots", 18)


def fetch_forecast(latitude, longitude):
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m",
        "wind_speed_unit": "kn",
        "timezone": TIMEZONE,
        "forecast_days": FORECAST_DAYS,
    }
    resp = requests.get(OPEN_METEO_URL, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()["hourly"]


def direction_label(degrees, zones):
    """Gibt 'top', 'ungünstig' oder 'gut' zurück, je nach Zonen-Definition."""
    for zone in zones:
        lo, hi = zone["from"], zone["to"]
        if lo <= hi:
            in_zone = lo <= degrees <= hi
        else:  # Zone geht über 360/0 Grad (z.B. Nord)
            in_zone = degrees >= lo or degrees <= hi
        if in_zone:
            return zone["label"], zone.get("kommentar", "")
    return "gut", ""


def filter_consecutive_days(slots, min_days):
    """Behält nur Slots, die zu einer Serie von >= min_days aufeinanderfolgenden
    Tagen mit gutem Wind gehören (für Spots, die sich nur für mehrtägige
    Aufenthalte lohnen, z.B. weit entfernte Spots wie Grömitz)."""
    by_date = defaultdict(list)
    for s in slots:
        by_date[s["zeit"].date()].append(s)

    dates = sorted(by_date.keys())
    good_dates = set()
    run = []
    for d in dates:
        if run and (d - run[-1]).days == 1:
            run.append(d)
        else:
            if len(run) >= min_days:
                good_dates.update(run)
            run = [d]
    if len(run) >= min_days:
        good_dates.update(run)

    return [s for s in slots if s["zeit"].date() in good_dates]


def evaluate_spot(spot, hourly, default_min_knots):
    min_knots = spot.get("min_knots", default_min_knots)
    gust_check = spot.get("gust_check", False)
    max_gust_ratio = spot.get("max_gust_ratio", 1.4)
    zones = spot.get("direction_zones", [])
    travel_hours = spot.get("travel_hours", 0)
    multi_day_only = spot.get("multi_day_only", False)
    min_consecutive_days = spot.get("min_consecutive_days", 2)

    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    # Bei Tagesausflug-Spots lohnen sich Slots erst ab Ankunftszeit
    # (realistische Abfahrt + Fahrzeit). Bei Mehrtages-Spots (z.B. Grömitz)
    # spielt das für die Auswertung keine Rolle, da man vor Ort übernachtet.
    earliest_hour = DAY_START_HOUR
    if not multi_day_only:
        earliest_hour = max(DAY_START_HOUR, DEPARTURE_HOUR + travel_hours)

    good_slots = []

    for i, ts in enumerate(hourly["time"]):
        dt = datetime.fromisoformat(ts).replace(tzinfo=tz)
        if dt < now:
            continue
        hour_decimal = dt.hour + dt.minute / 60
        if not (earliest_hour <= hour_decimal <= DAY_END_HOUR):
            continue

        speed = hourly["wind_speed_10m"][i]
        gust = hourly["wind_gusts_10m"][i]
        direction = hourly["wind_direction_10m"][i]

        if speed < min_knots:
            continue

        if gust_check and speed > 0 and (gust / speed) > max_gust_ratio:
            # zu böig / unzuverlässig am Wasser
            continue

        label, kommentar = direction_label(direction, zones)

        good_slots.append({
            "zeit": dt,
            "speed": round(speed, 1),
            "gust": round(gust, 1),
            "direction": round(direction),
            "label": label,
            "kommentar": kommentar,
        })

    if multi_day_only:
        good_slots = filter_consecutive_days(good_slots, min_consecutive_days)

    return good_slots


def format_slot(slot):
    tag = slot["zeit"].strftime("%a %d.%m. %H:%Uhr").replace("%Uhr", "Uhr")
    stern = " ⭐" if slot["label"] == "top" else ""
    warn = " ⚠️" if slot["label"] == "ungünstig" else ""
    return f"{tag}: {slot['speed']}kn (Böen {slot['gust']}kn), {slot['direction']}°{stern}{warn}"


def build_message(results, spots_by_name):
    lines = []
    any_hits = False

    for spot_name, slots in results.items():
        if not slots:
            continue
        any_hits = True
        spot = spots_by_name[spot_name]
        suffix = f" ({spot.get('travel_hours', 0):g}h Anfahrt"
        suffix += ", mehrtägiger Trip)" if spot.get("multi_day_only") else ")"
        lines.append(f"🏄 {spot_name}{suffix}")
        # nur die besten paar Slots pro Spot anzeigen, "top" zuerst
        sorted_slots = sorted(slots, key=lambda s: (s["label"] != "top", s["zeit"]))
        for slot in sorted_slots[:4]:
            lines.append("  " + format_slot(slot))
        lines.append("")

    if not any_hits:
        return None

    return "\n".join(lines).strip()


def send_pushover(message, title="🌬️ Windsurf-Vorhersage"):
    if not PUSHOVER_TOKEN or not PUSHOVER_USER:
        print("PUSHOVER_TOKEN oder PUSHOVER_USER fehlt – Nachricht wird nur ausgegeben:\n")
        print(message)
        return

    resp = requests.post(PUSHOVER_API_URL, data={
        "token": PUSHOVER_TOKEN,
        "user": PUSHOVER_USER,
        "title": title,
        "message": message,
    }, timeout=20)
    resp.raise_for_status()


def build_summary_message(hourly_by_spot, spots_by_name):
    """Fallback-Nachricht, wenn kein Spot die Mindestkriterien erfüllt:
    zeigt pro Spot kurz den besten (stärksten) erwarteten Wind der nächsten
    Tage, damit man trotzdem einen Überblick hat."""
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    lines = ["Kein Spot erfüllt aktuell genügend Wind bzw. die passende Richtung. "
              "Hier trotzdem die Vorschau der nächsten Tage:", ""]

    for spot_name, hourly in hourly_by_spot.items():
        best = None
        for i, ts in enumerate(hourly["time"]):
            dt = datetime.fromisoformat(ts).replace(tzinfo=tz)
            if dt < now:
                continue
            if not (DAY_START_HOUR <= dt.hour <= DAY_END_HOUR):
                continue
            speed = hourly["wind_speed_10m"][i]
            if best is None or speed > best["speed"]:
                best = {
                    "zeit": dt,
                    "speed": speed,
                    "gust": hourly["wind_gusts_10m"][i],
                    "direction": hourly["wind_direction_10m"][i],
                }

        if best:
            tag = best["zeit"].strftime("%a %d.%m. %H:%M")
            lines.append(
                f"🏄 {spot_name}: max. {round(best['speed'], 1)}kn "
                f"(Böen {round(best['gust'], 1)}kn) am {tag} Uhr, "
                f"{round(best['direction'])}°"
            )
        else:
            lines.append(f"🏄 {spot_name}: keine Vorhersagedaten verfügbar")

    return "\n".join(lines)


def main():
    spots, default_min_knots = load_spots()
    results = {}
    hourly_by_spot = {}

    for spot in spots:
        try:
            hourly = fetch_forecast(spot["latitude"], spot["longitude"])
            hourly_by_spot[spot["name"]] = hourly
            slots = evaluate_spot(spot, hourly, default_min_knots)
            results[spot["name"]] = slots
        except Exception as e:
            print(f"Fehler bei Spot {spot['name']}: {e}", file=sys.stderr)

    spots_by_name = {s["name"]: s for s in spots}
    message = build_message(results, spots_by_name)

    if message:
        send_pushover(message)
        print("Nachricht verschickt:\n")
        print(message)
    else:
        summary = build_summary_message(hourly_by_spot, spots_by_name)
        send_pushover(summary, title="🌬️ Windsurf-Vorhersage (kein Spot geeignet)")
        print("Kein Spot erfüllt die Kriterien – Übersichts-Nachricht verschickt:\n")
        print(summary)


if __name__ == "__main__":
    main()
