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
import math
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

COMPASS_POINTS = ["N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

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


def compass_direction(degrees):
    """Wandelt Gradzahl in 16er-Himmelsrichtung um (deutsche Abkürzung)."""
    index = round(degrees / 22.5) % 16
    return COMPASS_POINTS[index]


def average_direction(directions_deg):
    """Zirkulärer Mittelwert mehrerer Windrichtungen (Vektor-Mittel), da man
    Grad-Werte nicht einfach arithmetisch mitteln kann (0°/360°-Sprung)."""
    sin_sum = sum(math.sin(math.radians(d)) for d in directions_deg)
    cos_sum = sum(math.cos(math.radians(d)) for d in directions_deg)
    return math.degrees(math.atan2(sin_sum, cos_sum)) % 360


def shore_type(direction_deg, offshore_deg):
    """Klassifiziert die Windrichtung relativ zur Offshore-Richtung des Spots:
    Offshore / Side-Offshore / Sideshore / Side-Onshore / Onshore.
    Gibt None zurück, wenn für den Spot keine offshore_direction definiert ist
    (z.B. weil dort ohnehin jede Richtung passt)."""
    if offshore_deg is None:
        return None
    diff = abs((direction_deg - offshore_deg + 180) % 360 - 180)
    if diff <= 22.5:
        return "Offshore"
    elif diff <= 67.5:
        return "Side-Offshore"
    elif diff <= 112.5:
        return "Sideshore"
    elif diff <= 157.5:
        return "Side-Onshore"
    else:
        return "Onshore"


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
    early_slots = []  # gute Bedingungen VOR der Ankunftszeit bei Abfahrt am selben Morgen
                       # -> Kandidat für "am Vorabend anreisen"

    for i, ts in enumerate(hourly["time"]):
        dt = datetime.fromisoformat(ts).replace(tzinfo=tz)
        if dt < now:
            continue
        hour_decimal = dt.hour + dt.minute / 60
        if not (DAY_START_HOUR <= hour_decimal <= DAY_END_HOUR):
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

        slot = {
            "zeit": dt,
            "speed": round(speed, 1),
            "gust": round(gust, 1),
            "direction": round(direction),
            "label": label,
            "kommentar": kommentar,
        }

        if not multi_day_only and hour_decimal < earliest_hour:
            early_slots.append(slot)
        else:
            good_slots.append(slot)

    if multi_day_only:
        good_slots = filter_consecutive_days(good_slots, min_consecutive_days)

    return good_slots, early_slots


def location_hint(degrees, location_zones):
    """Gibt den passenden Startort/Bereich zurück, falls für den Spot
    location_zones definiert sind (z.B. Möhnesee: welcher Bereich des Sees
    je nach Windrichtung Sinn ergibt). None, wenn nichts passt."""
    for zone in location_zones:
        lo, hi = zone["from"], zone["to"]
        if lo <= hi:
            in_zone = lo <= degrees <= hi
        else:
            in_zone = degrees >= lo or degrees <= hi
        if in_zone:
            return zone.get("ort")
    return None


def build_daily_summaries(slots, zones, offshore_direction, location_zones=None, early_slots=None):
    """Fasst die guten Stunden eines Spots pro Tag zu einem Zeitfenster mit
    Durchschnittswerten zusammen (statt einzelner Stundenwerte). Wenn es an
    einem Tag schon vor der realistischen Ankunftszeit gute Bedingungen gibt
    (early_slots), wird ein Hinweis auf eine mögliche Vorabend-Anreise ergänzt."""
    by_date = defaultdict(list)
    for s in slots:
        by_date[s["zeit"].date()].append(s)

    early_by_date = defaultdict(list)
    for s in (early_slots or []):
        early_by_date[s["zeit"].date()].append(s)

    summaries = []
    for date, day_slots in sorted(by_date.items()):
        day_slots = sorted(day_slots, key=lambda s: s["zeit"])
        speeds = [s["speed"] for s in day_slots]
        gusts = [s["gust"] for s in day_slots]
        directions = [s["direction"] for s in day_slots]

        avg_speed = sum(speeds) / len(speeds)
        avg_gust = sum(gusts) / len(gusts)
        avg_dir = average_direction(directions)
        label, kommentar = direction_label(avg_dir, zones)

        vorabend_hinweis = None
        early_for_date = sorted(early_by_date.get(date, []), key=lambda s: s["zeit"])
        if early_for_date:
            early_speed = round(sum(s["speed"] for s in early_for_date) / len(early_for_date), 1)
            early_start = early_for_date[0]["zeit"].strftime("%H:%M")
            vorabend_hinweis = (
                f"Bereits ab {early_start} Uhr guter Wind (⌀{early_speed}kn) – "
                f"ggf. lohnt sich eine Anreise schon am Vorabend."
            )

        summaries.append({
            "start": day_slots[0]["zeit"],
            "end": day_slots[-1]["zeit"],
            "avg_speed": round(avg_speed, 1),
            "avg_gust": round(avg_gust, 1),
            "compass": compass_direction(avg_dir),
            "shore": shore_type(avg_dir, offshore_direction),
            "ort": location_hint(avg_dir, location_zones or []),
            "label": label,
            "vorabend_hinweis": vorabend_hinweis,
        })

    return summaries


def format_daily_summary(summary):
    tag = summary["start"].strftime("%a %d.%m.")
    zeitspanne = f"{summary['start'].strftime('%H:%M')}–{summary['end'].strftime('%H:%M')}"
    stern = " ⭐" if summary["label"] == "top" else ""
    warn = " ⚠️" if summary["label"] == "ungünstig" else ""
    shore_str = f", {summary['shore']}" if summary["shore"] else ""
    zeile = (f"{tag} {zeitspanne} Uhr: ⌀{summary['avg_speed']}kn "
             f"(Böen ⌀{summary['avg_gust']}kn), {summary['compass']}{shore_str}{stern}{warn}")
    if summary.get("ort"):
        zeile += f"\n    📍 {summary['ort']}"
    if summary.get("vorabend_hinweis"):
        zeile += f"\n    🌙 {summary['vorabend_hinweis']}"
    return zeile


def build_message(results, spots_by_name, early_by_spot=None):
    early_by_spot = early_by_spot or {}
    lines = []
    any_hits = False

    for spot_name, slots in results.items():
        if not slots:
            continue
        any_hits = True
        spot = spots_by_name[spot_name]
        zones = spot.get("direction_zones", [])
        offshore_direction = spot.get("offshore_direction")
        location_zones = spot.get("location_zones", [])
        early_slots = early_by_spot.get(spot_name, [])

        summaries = build_daily_summaries(slots, zones, offshore_direction, location_zones, early_slots)

        suffix = f" ({spot.get('travel_hours', 0):g}h Anfahrt"
        suffix += ", mehrtägiger Trip)" if spot.get("multi_day_only") else ")"
        lines.append(f"🏄 {spot_name}{suffix}")
        # "top"-Tage zuerst, sonst chronologisch
        sorted_summaries = sorted(summaries, key=lambda s: (s["label"] != "top", s["start"]))
        for summary in sorted_summaries[:4]:
            lines.append("  " + format_daily_summary(summary))
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
    """Fallback-Nachricht, wenn kein Spot die Mindestkriterien erfüllt: zeigt
    pro Spot den Durchschnittswind der nächsten Tage (Tagesstunden), damit man
    trotzdem einen Überblick hat."""
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    lines = ["Kein Spot erfüllt aktuell genügend Wind bzw. die passende Richtung. "
              "Hier trotzdem die Vorschau der nächsten Tage:", ""]

    for spot_name, hourly in hourly_by_spot.items():
        spot = spots_by_name[spot_name]
        offshore_direction = spot.get("offshore_direction")
        location_zones = spot.get("location_zones", [])

        speeds, gusts, directions = [], [], []
        for i, ts in enumerate(hourly["time"]):
            dt = datetime.fromisoformat(ts).replace(tzinfo=tz)
            if dt < now:
                continue
            if not (DAY_START_HOUR <= dt.hour <= DAY_END_HOUR):
                continue
            speeds.append(hourly["wind_speed_10m"][i])
            gusts.append(hourly["wind_gusts_10m"][i])
            directions.append(hourly["wind_direction_10m"][i])

        if speeds:
            avg_speed = sum(speeds) / len(speeds)
            avg_gust = sum(gusts) / len(gusts)
            avg_dir = average_direction(directions)
            compass = compass_direction(avg_dir)
            shore = shore_type(avg_dir, offshore_direction)
            ort = location_hint(avg_dir, location_zones)
            shore_str = f", {shore}" if shore else ""
            zeile = (
                f"🏄 {spot_name}: ⌀{round(avg_speed, 1)}kn "
                f"(Böen ⌀{round(avg_gust, 1)}kn), {compass}{shore_str} "
                f"(nächste {FORECAST_DAYS} Tage, {DAY_START_HOUR}-{DAY_END_HOUR} Uhr)"
            )
            if ort:
                zeile += f"\n   📍 {ort}"
            lines.append(zeile)
        else:
            lines.append(f"🏄 {spot_name}: keine Vorhersagedaten verfügbar")

    return "\n".join(lines)


def main():
    spots, default_min_knots = load_spots()
    results = {}
    early_by_spot = {}
    hourly_by_spot = {}

    for spot in spots:
        try:
            hourly = fetch_forecast(spot["latitude"], spot["longitude"])
            hourly_by_spot[spot["name"]] = hourly
            slots, early_slots = evaluate_spot(spot, hourly, default_min_knots)
            results[spot["name"]] = slots
            early_by_spot[spot["name"]] = early_slots
        except Exception as e:
            print(f"Fehler bei Spot {spot['name']}: {e}", file=sys.stderr)

    spots_by_name = {s["name"]: s for s in spots}
    message = build_message(results, spots_by_name, early_by_spot)

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
