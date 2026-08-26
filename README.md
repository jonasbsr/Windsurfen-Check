# Windsurf-Vorhersage

Prüft täglich automatisch die Windvorhersage für mehrere Spots und schickt dir
eine Push-Nachricht, wenn sich eine Session lohnt. Zusätzlich gibt es ein
installierbares Web-Dashboard (PWA).

## Ordnerstruktur

```
windsurf-vorhersage/
├── push-benachrichtigung/   ← Windcheck-Logik, Spot-Konfiguration, Push-Versand
│   ├── wind_check.py
│   └── spots.json
├── app/                      ← Dashboard/PWA (nutzt die Logik aus push-benachrichtigung/)
│   ├── generate_dashboard.py
│   └── icon.png
├── requirements.txt          ← gemeinsame Python-Abhängigkeiten (beide Teile)
└── .github/workflows/wind-check.yml
```

Wichtig: `app/generate_dashboard.py` importiert `wind_check.py` aus dem
Nachbarordner `push-benachrichtigung/` (statt die Logik zu duplizieren). Die
beiden Ordner sind organisatorisch getrennt, aber die App bleibt technisch
von der Windcheck-Logik abhängig – Änderungen an Anfahrt-Filter, Shore-
Klassifizierung, Möhnesee-Ortshinweisen etc. wirken sich automatisch auf
beide Teile aus, ohne dass du etwas doppelt pflegen musst.

## Setup (einmalig, ca. 15 Minuten)

### 1. Pushover einrichten
1. App **Pushover** aus dem App Store installieren (einmalig ca. 5€) und Account anlegen.
2. Auf [pushover.net](https://pushover.net) einloggen, deinen **User Key** kopieren (steht direkt auf der Startseite).
3. Unten auf der Seite auf **"Create an Application/API Token"** klicken, Namen vergeben
   (z.B. "Windsurf-Check"), erstellen → den **API Token/Key** kopieren.

### 2. Repository auf GitHub anlegen
1. Neues (privates) Repository auf GitHub erstellen.
2. Alle Dateien aus diesem Ordner hochladen, **inklusive der Unterordner-Struktur**
   (z.B. per Drag&Drop im Browser, oder `git init && git add . && git commit -m "init" && git push`).

### 3. Secrets hinterlegen
Im Repository: **Settings → Secrets and variables → Actions → New repository secret**
- `PUSHOVER_TOKEN` → dein API Token aus Schritt 1
- `PUSHOVER_USER` → dein User Key aus Schritt 1
- optional: `NTFY_TOPIC`, `NTFY_TEST`, `NTFY_TOPIC_BFT`, `NTFY_TOPIC_BFT_TEST` (siehe ntfy-Abschnitt unten)

### 4. GitHub Pages aktivieren (für das Dashboard)
**Settings → Pages** → bei "Source" **"GitHub Actions"** auswählen (nicht
"Deploy from a branch"!). Mehr dazu im Dashboard-Abschnitt unten.

### 5. Fertig
Der Workflow läuft ab jetzt automatisch jeden Tag um 16:00 UTC / 18:00 Uhr
deutscher Sommerzeit (siehe `.github/workflows/wind-check.yml`, Zeile mit
`cron:` – Uhrzeit kannst du dort frei anpassen). Du kannst ihn auch manuell
testen: Im Reiter **Actions** → "Windsurf Vorhersage Check" → **Run workflow**.

## Neuen Spot hinzufügen

Einfach in `push-benachrichtigung/spots.json` einen neuen Block im
`"spots"`-Array ergänzen:

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
- `offshore_direction`: Grad-Angabe der Windrichtung, die am Spot genau
  Offshore ist. Daraus berechnet das Skript automatisch Offshore /
  Side-Offshore / Sideshore / Side-Onshore / Onshore für die tatsächlich
  vorhergesagte Richtung. `null`, wenn nicht sinnvoll (z.B. Möhnesee).
- `location_zones`: optional – ordnet Windrichtungen einen konkreten
  Startort/Bereich am Spot zu (wie bei Möhnesee: Porno-Beach/Delecke bei
  Süd/Südwest, sonst ganzer See). Wird als 📍-Zeile in der Nachricht angezeigt.
- `gust_check: true` + `max_gust_ratio`: aktiviert die Böigkeits-Prüfung
  (wie bei Möhnesee) – Slots werden verworfen, wenn die Böen mehr als das
  angegebene Vielfache des Basiswinds betragen.
- Kein Code muss angefasst werden – das Dashboard übernimmt neue Spots
  automatisch, da es dieselbe `spots.json` liest.

## Kriterien pro Spot (Stand jetzt)

| Spot | Beste Richtung | Offshore-Richtung | Mindestwind | Anfahrt ab Dortmund | Besonderheit |
|---|---|---|---|---|---|
| Brouwersdam | SW/W/NW | OSO (112,5°) | 18 kn | 3,5 h | jede Richtung geht |
| Strand Horst | S, O (sideoffshore) | SO (135°) | 18 kn | 2,5 h | OSO & WNW eher unangenehm, aber ok |
| Möhnesee | egal (jede Richtung hat Spot) | – (nicht anwendbar) | 18 kn | 1 h | Böen dürfen Basiswind nicht zu stark übersteigen; Startort hängt von Richtung ab |
| Grömitz | N, W (sideoffshore) | NW (315°) | 18 kn | 5 h | nur als mehrtägiger Trip: Empfehlung nur bei ≥2 guten Tagen am Stück |

**Anfahrtszeit-Filter:** Abfahrt wird mit 8:30 Uhr angenommen (Mittelwert
aus 8-9 Uhr). Bei Tagesausflug-Spots (alle außer Grömitz) werden Wind-Slots
vor der realistischen Ankunftszeit automatisch ausgeblendet – z.B. lohnt sich
Brouwersdam frühestens ab 12 Uhr (8:30 + 3,5h Fahrzeit). Bei Grömitz spielt
das keine Rolle, da man vor Ort übernachtet; dort zählt stattdessen nur, ob
mind. 2 Tage am Stück gute Bedingungen herrschen (`min_consecutive_days` in
`spots.json` einstellbar).

**Möhnesee-Startort:** Laut lokalem Spotguide ist bei West-, Nordwest- und
Ostwind der gesamte See gut belüftet. Bei Süd- und Südwestwind (die
vorherrschende Richtung dort) schirmen die Hügel rund um den See große Teile
ab – dann lohnt sich nur der westliche Teil (Porno-Beach oder Delecke, 1.
Becken). Das Skript zeigt den passenden Startort automatisch in der
Push-Nachricht an, wenn Möhnesee empfohlen wird.

## Windrichtung & Shore-Klassifizierung

Die Push-Nachricht zeigt die Windrichtung als Himmelsrichtung (16er-Kompass,
z.B. "NW" statt "315°") sowie – wo eine `offshore_direction` hinterlegt ist –
zusätzlich die Einordnung Offshore / Side-Offshore / Sideshore /
Side-Onshore / Onshore relativ zur tatsächlich vorhergesagten Richtung.

## Durchschnitts- statt Maximalwerte

Windstärke und Böen werden nicht mehr als Einzelstundenwert bzw. Tages-Maximum
angezeigt, sondern als Durchschnitt über das jeweilige gute Zeitfenster
(⌀-Symbol). Bei einer Empfehlung wird dazu automatisch das zusammenhängende
Zeitfenster pro Tag ermittelt (z.B. "12:00–18:00 Uhr") und über dieses
gemittelt. In der Fallback-Übersicht (kein Spot geeignet) wird über alle
Tagesstunden (8-20 Uhr) im gesamten Vorhersagezeitraum gemittelt.

## Vorabend-Anreise-Hinweis

Bei Tagesausflug-Spots (alle außer Grömitz) werden Wind-Slots vor der
realistischen Ankunftszeit normalerweise einfach ausgeblendet (siehe oben).
Wenn es an einem Tag aber schon deutlich früher gute Bedingungen gibt, als
bei einer Abfahrt am selben Morgen erreichbar wären, wird das nicht
unterschlagen: die Nachricht bekommt dann automatisch eine 🌙-Zeile wie
"Bereits ab 08:00 Uhr guter Wind (⌀20.0kn) – ggf. lohnt sich eine Anreise
schon am Vorabend." Das greift naturgemäß am ehesten bei den weiter
entfernten Spots (z.B. Brouwersdam), kann technisch aber bei jedem
Tagesausflug-Spot auftreten.

## Lokal testen

```bash
pip install -r requirements.txt
cd push-benachrichtigung
export PUSHOVER_TOKEN="..."
export PUSHOVER_USER="..."
python wind_check.py
```

Ohne gesetzte Umgebungsvariablen gibt das Skript die Nachricht nur in der
Konsole aus (kein Versand) – gut zum Testen.

Das Dashboard lokal generieren:

```bash
pip install -r requirements.txt
cd app
python generate_dashboard.py
# Ergebnis liegt in app/site/ – z.B. mit "python -m http.server" im
# site-Ordner lokal im Browser ansehen
```

## Wenn kein Spot die Kriterien erfüllt

Du bekommst trotzdem eine Push-Nachricht – dann aber mit dem Titel
"Windsurf-Vorhersage (kein Spot geeignet)" und einer kurzen Übersicht mit
Durchschnittswind je Spot der nächsten Tage (unabhängig von
Mindestwind/Richtung). So siehst du auf einen Blick, wie nah es dran war,
ohne extra nachschauen zu müssen.

## Web-Dashboard (PWA) über GitHub Pages

Zusätzlich zu den Push-Nachrichten gibt es ein installierbares Dashboard,
das bei jedem Lauf automatisch neu gebaut und veröffentlicht wird – über die
moderne **GitHub-Actions-Pages-Methode** (kein Commit ins Repo nötig, die
Historie bleibt sauber).

**Einmalige Einrichtung:**
1. Im Repository: **Settings → Pages** → bei "Source" **"GitHub Actions"**
   auswählen (nicht "Deploy from a branch" – das ist die ältere Methode).
2. Nach dem nächsten Workflow-Lauf ist die Seite unter
   `https://<dein-github-username>.github.io/<repo-name>/` erreichbar
   (GitHub zeigt dir die exakte URL auf der Pages-Einstellungsseite und im
   Workflow-Lauf selbst an; nach der Ersteinrichtung dauert es ein paar
   Minuten, bis die Seite zum ersten Mal live ist).
3. Die URL auf dem iPhone in **Safari** öffnen (wichtig: Safari, nicht Chrome
   – nur Safari unterstützt "Zum Home-Bildschirm hinzufügen" korrekt für PWAs).
4. Teilen-Symbol → **"Zum Home-Bildschirm"** → Hinzufügen.

Danach hast du ein eigenes App-Icon auf dem Home-Bildschirm, das die Seite im
Vollbild ohne Safari-Rahmen öffnet. Der Workflow generiert das Dashboard bei
jedem Lauf frisch in `app/site/` (dieser Ordner wird nicht ins Repo committet,
siehe `.gitignore`) und lädt es direkt als Pages-Artefakt hoch.

**Eigenes Icon verwenden:** `app/icon.png` (180×180px) im Repo einfach durch
ein eigenes Bild ersetzen – wird beim nächsten Lauf automatisch mit
eingebaut.

## Hinweis zur Datenquelle

Ein lokaler Möhnesee-Spotguide empfiehlt Windfinder als recht zuverlässige
Quelle für diesen See und warnt vor "Superforecast", die dort oft deutlich
zu viel Wind anzeigt. Dieses Skript nutzt aktuell Open-Meteo mit
automatischer Modellauswahl (`best_match`, i.d.R. DWD ICON-D2/EU für
Deutschland/Holland) – falls sich in der Praxis zeigt, dass die Vorhersage
am Möhnesee spürbar daneben liegt, könnten wir testweise auf ein anderes
Modell wechseln (Open-Meteo bietet z.B. `icon_d2` explizit wählbar an).
