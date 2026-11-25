"""Adaptive Mumbai traffic collector (TomTom Flow Segment Data + Open-Meteo rain).
Runs once per call. GitHub Actions calls it every 30 minutes; the script decides which
segments are due, respects a daily call cap, and appends to monthly CSV files.
Usage: python collector.py [--dry-run] [--now 2026-09-30T08:07:00]"""
import argparse, csv, json, os, random, sys, time
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request

IST = timezone(timedelta(hours=5, minutes=30))
KEY = os.environ.get("TOMTOM_KEY", "")
DAILY_CAP = int(os.environ.get("DAILY_CAP", "600"))   # set from YOUR TomTom dashboard limit
DATA_DIR = os.environ.get("DATA_DIR", "data")
RAIN_MM = 0.5                                          # at or above this, poll at peak rate
WX_POINTS = [("south", 18.95, 72.83), ("central", 19.05, 72.86), ("north", 19.17, 72.85)]
TRAFFIC_FIELDS = ["ts_utc", "ts_ist", "seg_id", "corridor", "q_lat", "q_lon", "current_speed", "free_flow_speed",
                  "current_tt", "free_flow_tt", "confidence", "road_closure", "frc", "seg_key", "flag", "band"]
WEATHER_FIELDS = ["ts_utc", "ts_ist", "point", "lat", "lon", "precip_mm", "temp_c", "rh_pct", "weather_code"]

def band_k(hour, raining):
    """Return (band name, poll every k half-hour slots)."""
    if 7 <= hour < 11 or 17 <= hour < 21: return "peak", 1
    if raining: return "rain", 1
    if 5 <= hour < 7 or 11 <= hour < 17 or 21 <= hour < 23: return "shoulder", 4
    return "night", 6

def get_json(url):
    for attempt in (1, 2):
        try:
            with urlopen(Request(url, headers={"User-Agent": "mumbai-traffic-research"}), timeout=15) as r:
                return json.load(r)
        except Exception as e:
            err = e; time.sleep(1.5)
    raise err

def fetch_weather(dry):
    if dry:
        return [{"point": n, "lat": la, "lon": lo, "precip_mm": random.choice([0] * 19 + [0.6]), "temp_c": 28.0, "rh_pct": 80, "weather_code": 3} for n, la, lo in WX_POINTS]
    lat = ",".join(str(p[1]) for p in WX_POINTS); lon = ",".join(str(p[2]) for p in WX_POINTS)
    d = get_json("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&current=temperature_2m,precipitation,relative_humidity_2m,weather_code&timezone=Asia%%2FKolkata" % (lat, lon))
    d = d if isinstance(d, list) else [d]
    out = []
    for (n, la, lo), x in zip(WX_POINTS, d):
        c = x["current"]
        out.append({"point": n, "lat": la, "lon": lo, "precip_mm": c.get("precipitation"), "temp_c": c.get("temperature_2m"),
                    "rh_pct": c.get("relative_humidity_2m"), "weather_code": c.get("weather_code")})
    return out

def fetch_flow(lat, lon, dry):
    if dry:
        ff = random.randint(25, 60); cs = max(3, int(ff * random.uniform(.3, 1.05)))
        return {"currentSpeed": cs, "freeFlowSpeed": ff, "currentTravelTime": 100, "freeFlowTravelTime": 80, "confidence": round(random.uniform(.7, 1), 3),
                "roadClosure": False, "frc": "FRC2", "coordinates": {"coordinate": [{"latitude": lat, "longitude": lon}]}}
    d = get_json("https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json?point=%s,%s&key=%s" % (lat, lon, KEY))
    return d["flowSegmentData"]

def month_file(kind, now):
    os.makedirs(os.path.join(DATA_DIR, kind), exist_ok=True)
    return os.path.join(DATA_DIR, kind, now.strftime("%Y-%m") + ".csv")

def append(path, fields, rows):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if new: w.writeheader()
        w.writerows(rows)

def calls_today(path, today):
    if not os.path.exists(path): return 0
    with open(path, encoding="utf-8") as fh:
        return sum(1 for r in csv.DictReader(fh) if r["ts_ist"].startswith(today))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--now"); a = ap.parse_args()
    if not a.dry_run and not KEY: sys.exit("TOMTOM_KEY is not set")
    now = datetime.fromisoformat(a.now).replace(tzinfo=IST) if a.now else datetime.now(IST)
    ts_utc, ts_ist = now.astimezone(timezone.utc).isoformat(timespec="seconds"), now.isoformat(timespec="seconds")
    slot = (now.hour * 60 + now.minute) // 30

    wx = []
    try:
        wx = fetch_weather(a.dry_run)
        append(month_file("weather", now), WEATHER_FIELDS, [dict(w, ts_utc=ts_utc, ts_ist=ts_ist) for w in wx])
    except Exception as e:
        print("weather failed:", e)
    raining = any((w["precip_mm"] or 0) >= RAIN_MM for w in wx)
    band, k = band_k(now.hour, raining)

    with open("segments.csv", encoding="utf-8") as fh:
        segs = sorted(csv.DictReader(fh), key=lambda r: int(r["priority"]))
    tpath = month_file("traffic", now)
    left = DAILY_CAP - calls_today(tpath, now.strftime("%Y-%m-%d"))
    lean = DAILY_CAP < int(len(segs) * 23 * 1.1)   # not enough budget: priority-2 roads only hourly at peak
    due = [s for s in segs if ((band == "peak" and slot % 2 == 0) if (lean and s["priority"] == "2") else slot % k == 0)]
    if len(due) > left:
        print("cap: %d calls left today, %d due; low-priority segments skipped" % (left, len(due)))
        due = due[:max(left, 0)]
    rows, fails = [], 0
    for s in due:
        try:
            f = fetch_flow(float(s["lat"]), float(s["lon"]), a.dry_run)
            cs, ff, cf = f.get("currentSpeed"), f.get("freeFlowSpeed"), f.get("confidence")
            flag = "ok"
            if cs is None or cs < 0: flag = "bad_speed"
            elif ff and cs > 1.15 * ff: flag = "speed_gt_free"
            elif cf is not None and cf < 0.8: flag = "low_conf"
            c0 = (f.get("coordinates", {}).get("coordinate") or [{}])[0]
            rows.append({"ts_utc": ts_utc, "ts_ist": ts_ist, "seg_id": s["seg_id"], "corridor": s["corridor"], "q_lat": s["lat"], "q_lon": s["lon"],
                         "current_speed": cs, "free_flow_speed": ff, "current_tt": f.get("currentTravelTime"), "free_flow_tt": f.get("freeFlowTravelTime"),
                         "confidence": cf, "road_closure": f.get("roadClosure"), "frc": f.get("frc"),
                         "seg_key": "%.5f,%.5f" % (c0.get("latitude", 0), c0.get("longitude", 0)), "flag": flag, "band": band})
        except Exception as e:
            fails += 1; print("flow failed for", s["seg_id"], e)
        if not a.dry_run: time.sleep(0.3)
    if rows: append(tpath, TRAFFIC_FIELDS, rows)
    print("%s band=%s raining=%s polled=%d failed=%d" % (ts_ist, band, raining, len(rows), fails))
    if due and not rows: sys.exit(1)

if __name__ == "__main__":
    main()
