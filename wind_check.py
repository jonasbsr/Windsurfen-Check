#!/usr/bin/env python3
"""
Windsurf-Vorhersage-Check
==========================
Holt für alle in spots.json definierten Spots die stündliche Windvorhersage
von Open-Meteo (kostenlos, kein API-Key nötig) und bestimmt für jeden der
nächsten FORECAST_DAYS Tage das interessanteste Windfenster (Zeitspanne mit
den besten, die Kriterien erfüllenden Stunden). Schickt eine Tagesübersicht
pro Spot per Pushover UND ntfy – oder, falls nirgendwo genug Wind ist, eine
kurze Fallback-Übersicht mit dem jeweils besten Tag pro Spot.

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
FORECAST_DAYS = 7          # wie viele Tage vorausschauen
DAY_START_HOUR = 8         # nur Stunden zwischen 8 und 20 Uhr lokal betrachten
DAY_END_HOUR = 20
DEPARTURE_HOUR = 8       # realistische Abfahrtszeit (Mittelwert von 8-9 Uhr), für Anfahrt-Filter bei Tagesausflug-Spots
TIMEZONE = "Europe/Berlin"

COMPASS_POINTS = ["N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
WEEKDAYS_DE_KURZ = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
WEEKDAYS_DE_LANG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
                     "Freitag", "Samstag", "Sonntag"]

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN")
PUSHOVER_USER = os.environ.get("PUSHOVER_USER")

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")


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


def evaluate_spot_days(spot, hourly, default_min_knots, now):
    """Bestimmt für jeden der nächsten FORECAST_DAYS Tage das interessanteste
    Windfenster (zusammenhängende Stunden, die die Kriterien erfüllen) sowie
    Durchschnittswind und -böen darüber. Tage ohne qualifizierendes Fenster
    bekommen trotzdem einen Eintrag (qualifies=False) mit dem unter allen
    Tagesstunden gemittelten Wind, damit man bei Bedarf trotzdem den besten
    Tag der Woche ermitteln kann."""
    min_knots = spot.get("min_knots", default_min_knots)
    gust_check = spot.get("gust_check", False)
    max_gust_ratio = spot.get("max_gust_ratio", 1.4)
    zones = spot.get("direction_zones", [])
    travel_hours = spot.get("travel_hours", 0)
    multi_day_only = spot.get("multi_day_only", False)
    min_consecutive_days = spot.get("min_consecutive_days", 2)
    location_zones = spot.get("location_zones", [])
    offshore_direction = spot.get("offshore_direction")

    tz = ZoneInfo(TIMEZONE)

    # Bei Tagesausflug-Spots lohnen sich Slots erst ab Ankunftszeit
    # (realistische Abfahrt + Fahrzeit). Bei Mehrtages-Spots (z.B. Grömitz)
    # spielt das für die Auswertung keine Rolle, da man vor Ort übernachtet.
    earliest_hour = DAY_START_HOUR
    if not multi_day_only:
        earliest_hour = max(DAY_START_HOUR, DEPARTURE_HOUR + travel_hours)

    by_date_all = defaultdict(list)    # alle Tagesstunden (auch unter Mindestwind), für Fallback
    by_date_qual = defaultdict(list)   # Stunden, die alle Kriterien erfüllen UND ab Ankunftszeit liegen
    by_date_early = defaultdict(list)  # Stunden, die alle Kriterien erfüllen, aber VOR Ankunftszeit liegen

    for i, ts in enumerate(hourly["time"]):
        dt = datetime.fromisoformat(ts).replace(tzinfo=tz)
        if dt < now:
            continue
        hour_decimal = dt.hour + dt.minute / 60
        if not (DAY_START_HOUR <= hour_decimal <= DAY_END_HOUR):
            continue

        date = dt.date()
        speed = hourly["wind_speed_10m"][i]
        gust = hourly["wind_gusts_10m"][i]
        direction = hourly["wind_direction_10m"][i]

        by_date_all[date].append({"speed": speed, "gust": gust})

        meets_criteria = speed >= min_knots and not (
            gust_check and speed > 0 and (gust / speed) > max_gust_ratio
        )
        if meets_criteria:
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
                by_date_early[date].append(slot)
            else:
                by_date_qual[date].append(slot)

    # Grömitz: nur Tage behalten, die Teil einer Serie von >= min_consecutive_days
    # aufeinanderfolgenden guten Tagen sind
    if multi_day_only:
        all_qual_slots = [s for slots in by_date_qual.values() for s in slots]
        filtered = filter_consecutive_days(all_qual_slots, min_consecutive_days)
        keep_dates = set(s["zeit"].date() for s in filtered)
        by_date_qual = {d: v for d, v in by_date_qual.items() if d in keep_dates}

    days = []
    start_date = now.date()
    for offset in range(FORECAST_DAYS):
        date = start_date + timedelta(days=offset)
        qual_slots = sorted(by_date_qual.get(date, []), key=lambda s: s["zeit"])

        if qual_slots:
            speeds = [s["speed"] for s in qual_slots]
            gusts = [s["gust"] for s in qual_slots]
            directions = [s["direction"] for s in qual_slots]
            avg_speed = sum(speeds) / len(speeds)
            avg_gust = sum(gusts) / len(gusts)
            avg_dir = average_direction(directions)
            label, kommentar = direction_label(avg_dir, zones)

            early_slots = sorted(by_date_early.get(date, []), key=lambda s: s["zeit"])
            vorabend_hinweis = None
            if early_slots:
                early_speed = round(sum(s["speed"] for s in early_slots) / len(early_slots), 1)
                early_start = early_slots[0]["zeit"].strftime("%H:%M")
                vorabend_hinweis = (
                    f"Bereits ab {early_start} Uhr guter Wind (⌀{early_speed}kn) – "
                    f"ggf. lohnt sich eine Anreise schon am Vorabend."
                )

            days.append({
                "date": date,
                "qualifies": True,
                "start": qual_slots[0]["zeit"],
                "end": qual_slots[-1]["zeit"],
                "avg_speed": round(avg_speed, 1),
                "avg_gust": round(avg_gust, 1),
                "compass": compass_direction(avg_dir),
                "shore": shore_type(avg_dir, offshore_direction),
                "ort": location_hint(avg_dir, location_zones),
                "label": label,
                "vorabend_hinweis": vorabend_hinweis,
            })
        else:
            all_hours = by_date_all.get(date, [])
            if all_hours:
                avg_all_speed = round(sum(h["speed"] for h in all_hours) / len(all_hours), 1)
                avg_all_gust = round(sum(h["gust"] for h in all_hours) / len(all_hours), 1)
            else:
                avg_all_speed = None
                avg_all_gust = None
            days.append({
                "date": date,
                "qualifies": False,
                "avg_speed": avg_all_speed,
                "avg_gust": avg_all_gust,
            })

    return days


def format_day_line(day):
    """Formatiert einen Tag für einen relevanten Spot (der mind. an einem Tag
    Wind hat): schwache Tage zeigen die echte Windstärke/Böen ohne weitere
    Details, Tage, die die Kriterien erfüllen, den vollen Eintrag mit Stern."""
    wd = WEEKDAYS_DE_KURZ[day["date"].weekday()]

    if not day["qualifies"]:
        if day.get("avg_speed") is None:
            return f"{wd} keine Daten"
        return f"{wd} {day['avg_speed']}/{day['avg_gust']}kn"

    stern = " ⭐" if day["label"] == "top" else ""
    zeitspanne = f"{day['start'].strftime('%H')}-{day['end'].strftime('%H')}h"
    shore_str = f" {day['shore']}" if day["shore"] else ""
    zeile = f"{wd} {zeitspanne}{stern} {day['avg_speed']}/{day['avg_gust']}kn {day['compass']}{shore_str}"

    if day.get("ort"):
        zeile += f"\n   📍 {day['ort']}"
    if day.get("vorabend_hinweis"):
        zeile += f"\n   🌙 {day['vorabend_hinweis']}"

    return zeile


def format_best_day_line(name, days):
    """Ein-Zeiler für Spots ohne nennenswerten Wind: bester Tag (höchster
    Durchschnittswind, unabhängig von der Mindestschwelle) mit Wochentag."""
    candidates = [d for d in days if d.get("avg_speed") is not None]
    if candidates:
        best = max(candidates, key=lambda d: d["avg_speed"])
        wd = WEEKDAYS_DE_LANG[best["date"].weekday()]
        return f"{name}: max {best['avg_speed']}kn am {wd}"
    return f"{name}: keine Vorhersagedaten verfügbar"


def build_message(spots, days_by_spot):
    """Baut die Nachricht: Spots mit mind. einem Tag über der Mindestschwelle
    ('relevant') werden oben ausführlich mit Tages-für-Tag-Werten gezeigt,
    alle anderen darunter nur als Ein-Zeiler. Gibt None zurück, wenn KEIN
    Spot an KEINEM Tag genug Wind hat – dann übernimmt build_summary_message."""
    relevant_names = {
        spot["name"] for spot in spots
        if any(day["qualifies"] for day in days_by_spot.get(spot["name"], []))
    }
    if not relevant_names:
        return None

    relevant = [s for s in spots if s["name"] in relevant_names]
    others = [s for s in spots if s["name"] not in relevant_names]

    lines = []
    for spot in relevant:
        name = spot["name"]
        days = days_by_spot.get(name, [])
        lines.append(f"🏄 {name}")
        for day in days:
            lines.append(format_day_line(day))
        lines.append("")

    if others:
        lines.append("💤 Andere Spots ohne nennenswerten Wind:")
        for spot in others:
            lines.append(format_best_day_line(spot["name"], days_by_spot.get(spot["name"], [])))

    return "\n".join(lines).strip()


def build_summary_message(spots, days_by_spot):
    """Fallback-Nachricht, wenn nirgendwo genug Wind ist: zeigt pro Spot nur
    den besten Tag (höchster Durchschnittswind, unabhängig von der
    Mindestschwelle) mit ausgeschriebenem Wochentag."""
    lines = [f"🌬️ Kein Spot hat aktuell nennenswerten Wind (nächste {FORECAST_DAYS} Tage)", ""]

    for spot in spots:
        lines.append(format_best_day_line(spot["name"], days_by_spot.get(spot["name"], [])))

    return "\n".join(lines)


def send_pushover(message, title="🌬️ Windsurf-Vorhersage"):
    if not PUSHOVER_TOKEN or not PUSHOVER_USER:
        print("PUSHOVER_TOKEN oder PUSHOVER_USER fehlt – Pushover-Versand übersprungen.")
        return
    try:
        resp = requests.post(PUSHOVER_API_URL, data={
            "token": PUSHOVER_TOKEN,
            "user": PUSHOVER_USER,
            "title": title,
            "message": message,
        }, timeout=20)
        resp.raise_for_status()
        print("Pushover-Nachricht verschickt.")
    except Exception as e:
        print(f"Fehler beim Pushover-Versand: {e}", file=sys.stderr)


def send_ntfy(message, title="🌬️ Windsurf-Vorhersage"):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC fehlt – ntfy-Versand übersprungen.")
        return
    try:
        # JSON-Publish-API statt Header nutzen, da Emojis im Titel als
        # HTTP-Header (Latin-1) sonst Probleme machen können.
        resp = requests.post(NTFY_SERVER, json={
            "topic": NTFY_TOPIC,
            "title": title,
            "message": message,
        }, timeout=20)
        resp.raise_for_status()
        print("ntfy-Nachricht verschickt.")
    except Exception as e:
        print(f"Fehler beim ntfy-Versand: {e}", file=sys.stderr)


def main():
    spots, default_min_knots = load_spots()
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    days_by_spot = {}

    for spot in spots:
        try:
            hourly = fetch_forecast(spot["latitude"], spot["longitude"])
            days_by_spot[spot["name"]] = evaluate_spot_days(spot, hourly, default_min_knots, now)
        except Exception as e:
            print(f"Fehler bei Spot {spot['name']}: {e}", file=sys.stderr)

    message = build_message(spots, days_by_spot)

    if message:
        send_pushover(message)
        send_ntfy(message)
        print("\nGesendete Nachricht:\n")
        print(message)
    else:
        summary = build_summary_message(spots, days_by_spot)
        title = "🌬️ Windsurf-Vorhersage (kein Spot geeignet)"
        send_pushover(summary, title=title)
        send_ntfy(summary, title=title)
        print("\nKein Spot erfüllt die Kriterien – Übersichts-Nachricht verschickt:\n")
        print(summary)


if __name__ == "__main__":
    main()
