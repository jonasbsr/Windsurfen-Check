# Windsurf-Vorhersage

Prüft täglich automatisch die Windvorhersage für mehrere Spots und schickt dir
eine Pushover-Push-Nachricht, wenn sich eine Session lohnt.

## Setup (einmalig, ca. 10 Minuten)

### 1. Pushover einrichten
1. App **Pushover** aus dem App Store installieren (einmalig ca. 5€) und Account anlegen.
2. Auf [pushover.net](https://pushover.net) einloggen, deinen **User Key** kopieren (steht direkt auf der Startseite).
3. Unten auf der Seite auf **"Create an Application/API Token"** klicken, Namen vergeben
   (z.B. "Windsurf-Check"), erstellen → den **API Token/Key** kopieren.

### 2. Repository auf GitHub anlegen
1. Neues (privates) Repository auf GitHub erstellen.
2. Alle Dateien aus diesem Ordner hochladen (z.B. per Drag&Drop im Browser,
   oder `git init && git add . && git commit -m "init" && git push`).

### 3. Secrets hinterlegen
Im Repository: **Settings → Secrets and variables → Actions → New repository secret**
- `PUSHOVER_TOKEN` → dein API Token aus Schritt 1
- `PUSHOVER_USER` → dein User Key aus Schritt 1

### 4. Fertig
Der Workflow läuft ab jetzt automatisch jeden Tag um 6:00 UTC (siehe
`.github/workflows/wind-check.yml`, Zeile mit `cron:` – Uhrzeit kannst du dort
frei anpassen). Du kannst ihn auch manuell testen: Im Reiter **Actions** →
"Windsurf Vorhersage Check" → **Run workflow**.

## Neuen Spot hinzufügen

Einfach in `spots.json` einen neuen Block im `"spots"`-Array ergänzen:

```json
{
  "name": "Mein neuer Spot",
  "latitude": 12.345,
  "longitude": 6.789,
  "min_knots": 18,
  "gust_check": false,
  "travel_hours": 2.0,
  "direction_zones": [
    { "from": 200, "to": 250, "label": "top", "kommentar": "SW ist ideal" }
  ]
}
```

- `travel_hours`: Fahrzeit ab Dortmund. Slots vor der realistischen
  Ankunftszeit (Abfahrt 8:30 Uhr + Fahrzeit) werden ausgeblendet.
- `multi_day_only: true` + `min_consecutive_days`: für weit entfernte Spots,
  die sich nur bei mehrtägigem gutem Wind lohnen (wie Grömitz) – dann entfällt
  der Anfahrt-Filter und es wird nur bei entsprechenden Wetterserien empfohlen.

- `direction_zones`: Bereiche in Grad (0/360 = Nord, 90 = Ost, 180 = Süd, 270 = West).
  Nicht abgedeckte Richtungen gelten automatisch als "gut". Zusätzlich möglich:
  `"label": "ungünstig"` für Richtungen, die zwar gehen aber unangenehm sind.
- `gust_check: true` + `max_gust_ratio`: aktiviert die Böigkeits-Prüfung
  (wie bei Möhnesee) – Slots werden verworfen, wenn die Böen mehr als das
  angegebene Vielfache des Basiswinds betragen.
- Kein Code muss angefasst werden, keine neue Auslieferung nötig.

## Kriterien pro Spot (Stand jetzt)

| Spot | Beste Richtung | Mindestwind | Anfahrt ab Dortmund | Besonderheit |
|---|---|---|---|---|
| Brouwersdam | SW/W/NW | 18 kn | 3,5 h | jede Richtung geht |
| Strand Horst | S, O (sideoffshore) | 18 kn | 2,5 h | OSO & WNW eher unangenehm, aber ok |
| Möhnesee | egal (jede Richtung hat Spot) | 18 kn | 1 h | Böen dürfen Basiswind nicht zu stark übersteigen |
| Grömitz | N, W (sideoffshore) | 18 kn | 5 h | nur als mehrtägiger Trip: Empfehlung nur bei ≥2 guten Tagen am Stück |

**Anfahrtszeit-Filter:** Abfahrt wird mit 8:30 Uhr angenommen (Mittelwert
aus 8-9 Uhr). Bei Tagesausflug-Spots (alle außer Grömitz) werden Wind-Slots
vor der realistischen Ankunftszeit automatisch ausgeblendet – z.B. lohnt sich
Brouwersdam frühestens ab 12 Uhr (8:30 + 3,5h Fahrzeit). Bei Grömitz spielt
das keine Rolle, da man vor Ort übernachtet; dort zählt stattdessen nur, ob
mind. 2 Tage am Stück gute Bedingungen herrschen (`min_consecutive_days` in
`spots.json` einstellbar).

## Lokal testen

```bash
pip install -r requirements.txt
export PUSHOVER_TOKEN="..."
export PUSHOVER_USER="..."
python wind_check.py
```

Ohne gesetzte Umgebungsvariablen gibt das Skript die Nachricht nur in der
Konsole aus (kein Versand) – gut zum Testen.
