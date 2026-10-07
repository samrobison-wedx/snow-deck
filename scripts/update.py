#!/usr/bin/env python3
"""Snow Deck data collector. Uses only Python's standard library.
Reads areas.json, gathers data, writes data/conditions.json."""
import json, math, os, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "conditions.json")
TZ = ZoneInfo("America/Denver")  # all current areas are in Mountain time
UA = "SnowDeck (https://github.com/%s)" % os.environ.get("GITHUB_REPOSITORY", "local-test")
SNOTEL = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"
NOW = datetime.now(TZ)

def get(url, tries=3):
    err = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except Exception as e:
            err = e
            time.sleep(2 * (i + 1))
    print("  FAILED:", url, err)
    return None

def km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))

# ---------- National Weather Service ----------
def hourly(series, spread=False):
    """Turn an NWS time series into [(local_datetime, value)] one entry per hour."""
    out = []
    for v in series.get("values", []):
        if v.get("value") is None:
            continue
        start, dur = v["validTime"].split("/")
        t = datetime.fromisoformat(start.replace("Z", "+00:00"))
        m = re.match(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?)?", dur)
        hrs = max(1, int(m.group(1) or 0) * 24 + int(m.group(2) or 0))
        for k in range(hrs):
            out.append(((t + timedelta(hours=k)).astimezone(TZ), v["value"] / hrs if spread else v["value"]))
    return out

def forecast(lat, lon):
    pts = get("https://api.weather.gov/points/%.4f,%.4f" % (lat, lon))
    if not pts:
        return None
    grid = get(pts["properties"]["forecastGridData"])
    if not grid:
        return None
    g = grid["properties"]
    snow, temp = hourly(g["snowfallAmount"], True), hourly(g["temperature"])
    pop, wind = hourly(g["probabilityOfPrecipitation"]), hourly(g["windSpeed"])
    today = NOW.date()
    fc, temps, rain = [], [], []
    last = (30.0, 15.0)
    for i in range(7):
        d = today + timedelta(days=i)
        ts = [v * 9 / 5 + 32 for t, v in temp if t.date() == d]
        hi, lo = (max(ts), min(ts)) if ts else last
        last = (hi, lo)
        pm = max([v for t, v in pop if t.date() == d] or [0])
        fc.append(round(sum(v for t, v in snow if t.date() == d) / 25.4, 1))
        temps.append({"hi": round(hi), "lo": round(lo)})
        rain.append(hi >= 36 and pm >= 40)
    now_t = min(temp, key=lambda x: abs(x[0] - NOW))[1] * 9 / 5 + 32 if temp else 20
    w = max([v for t, v in wind if t.date() == today] or [0]) * 0.621
    return {"fc": fc, "temps": temps, "rain": rain, "now": round(now_t), "wind": round(w)}

# ---------- SNOTEL (snow depth) ----------
def snotel_stations():
    q = urllib.parse.urlencode({"stationTriplets": "*:CO:SNTL,*:UT:SNTL,*:NM:SNTL,*:MT:SNTL", "activeOnly": "true"})
    s = get("%s/stations?%s" % (SNOTEL, q)) or []
    return [x for x in s if x.get("latitude") is not None and x.get("stationTriplet")]

def snotel_depths(triplets):
    out, end = {}, NOW.date()
    for i in range(0, len(triplets), 20):
        q = urllib.parse.urlencode({"stationTriplets": ",".join(triplets[i:i + 20]), "elements": "SNWD", "duration": "DAILY",
                                    "beginDate": (end - timedelta(days=5)).isoformat(), "endDate": end.isoformat()})
        for st in get("%s/data?%s" % (SNOTEL, q)) or []:
            vals = sorted((v["date"], v["value"]) for el in st.get("data", []) for v in el.get("values", []) if v.get("value") is not None)
            out[st["stationTriplet"]] = [v for _, v in vals]
    return out

# ---------- avalanche.org ----------
def in_ring(x, y, ring):
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside

def in_geom(x, y, geom):
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"] if geom["type"] == "MultiPolygon" else []
    return any(in_ring(x, y, p[0]) and not any(in_ring(x, y, h) for h in p[1:]) for p in polys)

def avalanche_zone(lat, lon, feats):
    for f in feats:
        if f.get("geometry") and in_geom(lon, lat, f["geometry"]):
            p = f["properties"]
            dl = p.get("danger_level")
            return {"aval": dl if isinstance(dl, int) and 1 <= dl <= 5 else None, "avalZone": p.get("name"),
                    "avalCenter": p.get("center_id"), "offSeason": bool(p.get("off_season"))}
    return {"aval": None, "avalZone": None, "avalCenter": None, "offSeason": False}

# ---------- main ----------
def main():
    areas = json.load(open(os.path.join(ROOT, "areas.json")))
    try:
        old = json.load(open(OUT)).get("areas", {})
    except Exception:
        old = {}
    print("Fetching SNOTEL station list...")
    stations = snotel_stations()
    print("  %d stations" % len(stations))
    pick = {}
    for a in areas:
        c = [(km(a["lat"], a["lon"], s["latitude"], s["longitude"]), s) for s in stations]
        c = [x for x in c if x[0] <= 45]
        if c:
            pick[a["id"]] = min(c, key=lambda x: x[0])
    depths = snotel_depths(sorted({s["stationTriplet"] for _, s in pick.values()})) if pick else {}
    print("Fetching avalanche zones...")
    feats = (get("https://api.avalanche.org/v2/public/products/map-layer") or {}).get("features", [])
    print("  %d zones" % len(feats))
    result, ok = {}, 0
    for a in areas:
        print(a["id"])
        try:
            e = forecast(a["lat"], a["lon"])
            if not e:
                raise RuntimeError("no NWS forecast")
            e.update({"s24": 0, "s72": 0, "base": 0, "snotel": None})
            if a["id"] in pick:
                dist, st = pick[a["id"]]
                dv = depths.get(st["stationTriplet"], [])
                if dv:
                    diffs = [max(0, y - x) for x, y in zip(dv, dv[1:])]
                    e.update({"base": round(dv[-1]), "s24": round(diffs[-1]) if diffs else 0, "s72": round(sum(diffs[-3:]))})
                e["snotel"] = {"name": st.get("name"), "km": round(dist, 1)}
            if a["zone"]:
                e.update(avalanche_zone(a["lat"], a["lon"], feats))
                prev_e = old.get(a["id"], {})
                oa, changed = prev_e.get("aval"), prev_e.get("changedAt")
                if oa is not None and e["aval"] is not None and oa != e["aval"]:
                    e["prev"], e["changedAt"] = oa, NOW.isoformat()
                elif changed and (NOW - datetime.fromisoformat(changed)) < timedelta(hours=24):
                    e["prev"], e["changedAt"] = prev_e.get("prev", e["aval"]), changed
                else:
                    e["prev"] = e["aval"]
            result[a["id"]] = e
            ok += 1
        except Exception as ex:
            print("  skipped:", ex)
            if a["id"] in old:
                result[a["id"]] = dict(old[a["id"]], stale=True)
        time.sleep(0.4)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"updated": datetime.now(timezone.utc).isoformat(), "areas": result}, open(OUT, "w"), separators=(",", ":"))
    print("Done: %d of %d areas updated" % (ok, len(areas)))
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
