"""
Mumbai Smart Traffic - Triggered Data Extractor
--------------------------------------------------
This is the "single run" version of the collector, built to be
triggered automatically on a schedule (e.g. by GitHub Actions)
rather than run in an infinite loop on your own laptop.

Each time this script runs, it:
1. Fetches live TomTom traffic + Open-Meteo weather for 5 Mumbai points
2. Appends the results to two CSV files (creates them if they don't exist)
3. Exits (the scheduler is responsible for calling it again later)

HOW THIS GETS "TRIGGERED" AUTOMATICALLY:
See mumbai_collector_workflow.yml - that file tells GitHub to run
this script every 15 minutes, forever, for free, without your
laptop needing to be on.
"""

import csv
import os
from datetime import datetime, timezone
import requests

# TomTom key is read from an environment variable (set as a GitHub
# Secret - see README below) rather than hardcoded, since this file
# will live in a public/private repo.
TOMTOM_KEY = os.environ.get("TOMTOM_KEY", "YOUR_TOMTOM_KEY_HERE")

LOCATIONS = [
    {"name": "Western_Express_Highway_Bandra",  "lat": 19.0596, "lon": 72.8656},
    {"name": "Eastern_Express_Highway_Sion",     "lat": 19.0448, "lon": 72.8619},
    {"name": "Sion_Panvel_Highway",              "lat": 19.0483, "lon": 72.9058},
    {"name": "Andheri_SEEPZ",                    "lat": 19.1197, "lon": 72.8468},
    {"name": "Malad_Kandivali",                  "lat": 19.0861, "lon": 72.8529},
]

TRAFFIC_CSV = "mumbai_traffic_log.csv"
WEATHER_CSV = "mumbai_weather_log.csv"


def fetch_traffic(lat, lon):
    url = (
        f"https://api.tomtom.com/traffic/services/4/flowSegmentData/"
        f"absolute/10/json?point={lat},{lon}&key={TOMTOM_KEY}"
    )
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()["flowSegmentData"]
    return {
        "currentSpeed": data.get("currentSpeed"),
        "freeFlowSpeed": data.get("freeFlowSpeed"),
        "currentTravelTime": data.get("currentTravelTime"),
        "freeFlowTravelTime": data.get("freeFlowTravelTime"),
        "confidence": data.get("confidence"),
        "roadClosure": data.get("roadClosure"),
    }


def fetch_weather(lat, lon):
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,precipitation,relative_humidity_2m"
        f"&timezone=Asia%2FKolkata"
    )
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()["current"]
    return {
        "temperature_c": data.get("temperature_2m"),
        "precipitation_mm": data.get("precipitation"),
        "humidity_pct": data.get("relative_humidity_2m"),
    }


def ensure_csv_header(path, fieldnames):
    if not os.path.exists(path):
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()


def log_row(path, fieldnames, row):
    ensure_csv_header(path, fieldnames)
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writerow(row)


def run_once():
    timestamp = datetime.now(timezone.utc).isoformat()

    traffic_fields = ["timestamp", "location_name", "lat", "lon",
                       "currentSpeed", "freeFlowSpeed", "currentTravelTime",
                       "freeFlowTravelTime", "confidence", "roadClosure"]
    weather_fields = ["timestamp", "location_name", "lat", "lon",
                       "temperature_c", "precipitation_mm", "humidity_pct"]

    for loc in LOCATIONS:
        try:
            t = fetch_traffic(loc["lat"], loc["lon"])
            row = {"timestamp": timestamp, "location_name": loc["name"],
                   "lat": loc["lat"], "lon": loc["lon"], **t}
            log_row(TRAFFIC_CSV, traffic_fields, row)
            print(f"[traffic] {loc['name']}: speed={t['currentSpeed']} "
                  f"freeflow={t['freeFlowSpeed']} confidence={t['confidence']}")
        except Exception as e:
            print(f"[traffic] FAILED for {loc['name']}: {e}")

        try:
            w = fetch_weather(loc["lat"], loc["lon"])
            row = {"timestamp": timestamp, "location_name": loc["name"],
                   "lat": loc["lat"], "lon": loc["lon"], **w}
            log_row(WEATHER_CSV, weather_fields, row)
            print(f"[weather] {loc['name']}: temp={w['temperature_c']}C "
                  f"rain={w['precipitation_mm']}mm")
        except Exception as e:
            print(f"[weather] FAILED for {loc['name']}: {e}")


if __name__ == "__main__":
    print(f"Running triggered extraction at {datetime.now(timezone.utc).isoformat()}")
    run_once()
    print("Done. Exiting (scheduler will trigger the next run).")
