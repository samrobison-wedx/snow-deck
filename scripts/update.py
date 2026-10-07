#!/usr/bin/env python3
"""Snow Deck data collector. Uses only Python's standard library.
Reads areas.json, gathers data from free public sources, writes data/conditions.json.
Sources: National Weather Service, Open-Meteo (other forecast models), NRCS SNOTEL, avalanche.org."""
import html, json, math, os, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
 
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "conditions.json")
TZ = ZoneInfo("America/Denver")  # every current area is in Mountain time
UA = "SnowDeck (https://github.com/%s)" % os.environ.get("GITHUB_REPOSITORY", "local-test")
SNOTEL = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"
OM = "https://api.open-meteo.com/v1/forecast"
AV = "https://api.avalanche.org/v2/public"
MODELS = {"gfs_seamless": "GFS", "ecmwf_ifs025": "ECMWF", "icon_seamless": "ICON", "gem_seamless": "GEM"}
NOW = datetime.now(TZ)
M_TO_FT = 3.28084
 
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
    print("  FAILED:", url[:140], err)
    return None
 
def km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))
 
def text(h, n=420):
    s = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(h or ""))).strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"
 
# ---------- National Weather Service ----------
def hourly(series, spread=False):
    """NWS time series -> {unix_hour: value}, one entry per hour."""
    out = {}
    for v in series.get("values", []):
        if v.get("value") is None:
            continue
        start, dur = v["validTime"].split("/")
        t = datetime.fromisoformat(start.replace("Z", "+00:00"))
        m = re.match(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?)?", dur)
        hrs = max(1, int(m.group(1) or 0) * 24 + int(m.group(2) or 0))
        for k in range(hrs):
            out[int((t + timedelta(hours=k)).timestamp())] = v["value"] / hrs if spread else v["value"]
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
    day = lambda ts: datetime.fromtimestamp(ts, TZ).date()
    fc, temps, rain, windD, dates = [], [], [], [], []
    last = (30.0, 15.0)
    for i in range(7):
        d = today + timedelta(days=i)
        ts = [v * 9 / 5 + 32 for t, v in temp.items() if day(t) == d]
        hi, lo = (max(ts), min(ts)) if ts else last
        last = (hi, lo)
        pm = max([v for t, v in pop.items() if day(t) == d] or [0])
        wm = max([v for t, v in wind.items() if day(t) == d] or [0]) * 0.621
        fc.append(round(sum(v for t, v in snow.items() if day(t) == d) / 25.4, 1))
        temps.append({"hi": round(hi), "lo": round(lo)})
        rain.append(hi >= 36 and pm >= 40)
        windD.append(round(wm))
        dates.append(d.isoformat())
    h0 = int(NOW.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0).timestamp())
    hrs = [h0 + 3600 * k for k in range(72)]
    hr = {"t": datetime.fromtimestamp(h0, TZ).isoformat(timespec="minutes"),
          "snow": [round(snow.get(t, 0) / 25.4, 2) for t in hrs],
          "temp": [round(temp[t] * 9 / 5 + 32) if t in temp else None for t in hrs],
          "wind": [round(wind.get(t, 0) * 0.621) for t in hrs],
          "pop": [round(pop.get(t, 0)) for t in hrs]}
    nowt = next((hr["temp"][k] for k in range(72) if hr["temp"][k] is not None), 20)
    return {"dates": dates, "fc": fc, "temps": temps, "rain": rain, "windD": windD, "now": nowt, "wind": hr["wind"][0], "hr": hr}
 
# ---------- Open-Meteo: other forecast models, base and summit ----------
def open_meteo(a):
    base = {"latitude": a["lat"], "longitude": a["lon"], "temperature_unit": "fahrenheit", "precipitation_unit": "inch",
            "wind_speed_unit": "mph", "timezone": "America/Denver", "forecast_days": 7}
    daily = "snowfall_sum,temperature_2m_max,temperature_2m_min"
    q1 = dict(base, elevation=round(a["base_ft"] / M_TO_FT), daily=daily, models=",".join(MODELS))
    j1 = get(OM + "?" + urllib.parse.urlencode(q1))
    q2 = dict(base, elevation=round(a["top_ft"] / M_TO_FT), daily=daily, hourly="freezing_level_height")
    j2 = get(OM + "?" + urllib.parse.urlencode(q2))
    out = {}
    if j1 and j1.get("daily"):
        d = j1["daily"]
        models, hi, lo, sn = {}, [], [], []
        for m, label in MODELS.items():
            s = d.get("snowfall_sum_" + m)
            if s and any(x is not None for x in s):
                models[label] = [round(x or 0, 1) for x in s]
        def avg(key):
            cols = [d[k] for k in d if k.startswith(key + "_") and any(x is not None for x in d[k])]
            return [round(sum(c[i] for c in cols if c[i] is not None) / max(1, len([c for c in cols if c[i] is not None]))) for i in range(len(d["time"]))] if cols else []
        out["models"] = models
        out["base"] = {"ft": a["base_ft"], "hi": avg("temperature_2m_max"), "lo": avg("temperature_2m_min"),
                       "snow": [round(sum(m[i] for m in models.values()) / len(models), 1) for i in range(len(d["time"]))] if models else []}
        out["dates"] = d["time"]
    if j2 and j2.get("daily"):
        d = j2["daily"]
        out["top"] = {"ft": a["top_ft"], "hi": [round(x) if x is not None else None for x in d.get("temperature_2m_max", [])],
                      "lo": [round(x) if x is not None else None for x in d.get("temperature_2m_min", [])],
                      "snow": [round(x or 0, 1) for x in d.get("snowfall_sum", [])]}
        h = j2.get("hourly") or {}
        fl = {}
        for t, v in zip(h.get("time", []), h.get("freezing_level_height", [])):
            if v is not None:
                fl.setdefault(t[:10], []).append(v * M_TO_FT)
        out["fl"] = [round(max(fl[x]) / 100) * 100 if x in fl else None for x in d.get("time", [])]
    return out or None
 
# ---------- SNOTEL (snow depth, history, snowpack vs median) ----------
def snotel_stations():
    q = urllib.parse.urlencode({"stationTriplets": "*:CO:SNTL,*:UT:SNTL,*:NM:SNTL,*:MT:SNTL", "activeOnly": "true"})
    s = get("%s/stations?%s" % (SNOTEL, q)) or []
    return [x for x in s if x.get("latitude") is not None and x.get("stationTriplet")]
 
def snotel_data(triplets):
    out, end = {}, NOW.date()
    for i in range(0, len(triplets), 10):
        q = urllib.parse.urlencode({"stationTriplets": ",".join(triplets[i:i + 10]), "elements": "SNWD,WTEQ", "duration": "DAILY",
                                    "beginDate": (end - timedelta(days=30)).isoformat(), "endDate": end.isoformat(),
                                    "centralTendencyType": "MEDIAN"})
        for st in get("%s/data?%s" % (SNOTEL, q)) or []:
            d = {}
            for el in st.get("data", []):
                code = (el.get("stationElement") or {}).get("elementCode")
                d[code] = [(v["date"], v.get("value"), v.get("median")) for v in el.get("values", []) if v.get("value") is not None]
            out[st["stationTriplet"]] = d
    return out
 
def snow_from(d):
    sd = d.get("SNWD", [])
    e = {}
    if sd:
        vals = [v for _, v, _ in sd]
        diffs = [max(0, y - x) for x, y in zip(vals, vals[1:])]
        e.update({"base": round(vals[-1]), "s24": round(diffs[-1]) if diffs else 0, "s72": round(sum(diffs[-3:])),
                  "hist": [[x, round(v)] for x, v, _ in sd][-30:], "snowDate": sd[-1][0]})
    w = d.get("WTEQ", [])
    if w and w[-1][2] is not None:
        now_, med = w[-1][1], w[-1][2]
        e["swe"] = {"now": round(now_, 1), "med": round(med, 1), "pct": round(now_ / med * 100) if med >= 1.0 else None, "date": w[-1][0]}
    return e
 
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
                    "avalCenter": p.get("center_id"), "offSeason": bool(p.get("off_season")),
                    "_zid": p.get("id"), "_link": p.get("link") or p.get("center_link"), "_advice": p.get("travel_advice")}
    return {"aval": None, "avalZone": None, "avalCenter": None, "offSeason": False, "_zid": None, "_link": None, "_advice": None}
 
def lvl(x):
    try:
        x = int(x)
        return x if 1 <= x <= 5 else None
    except (TypeError, ValueError):
        return None
 
def av_detail(center, zid, cache):
    k = (center, zid)
    if k in cache:
        return cache[k]
    j = get("%s/product?type=forecast&center_id=%s&zone_id=%s" % (AV, urllib.parse.quote(str(center)), urllib.parse.quote(str(zid))))
    d = {}
    if j:
        d["danger"] = {x.get("valid_day"): {b: lvl(x.get(b)) for b in ("upper", "middle", "lower")} for x in (j.get("danger") or []) if x.get("valid_day")}
        probs = []
        for p in (j.get("forecast_avalanche_problems") or [])[:4]:
            name = p.get("type") or p.get("name") or p.get("problem_type") or (p.get("avalanche_problem") or {}).get("name") or "Avalanche problem"
            loc = p.get("location") or p.get("aspects") or []
            size = p.get("size")
            probs.append({"name": str(name), "likelihood": p.get("likelihood"), "size": ", ".join(map(str, size)) if isinstance(size, list) else size,
                          "where": loc if isinstance(loc, list) else [str(loc)], "note": text(p.get("discussion"), 360)})
        d["problems"] = probs
        d["bottom"] = text(j.get("bottom_line"), 700)
        d["published"] = j.get("published_time")
        d["expires"] = j.get("expires_time")
    cache[k] = d
    return d
 
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
    sdata = snotel_data(sorted({s["stationTriplet"] for _, s in pick.values()})) if pick else {}
    print("Fetching avalanche zones...")
    feats = (get(AV + "/products/map-layer") or {}).get("features", [])
    print("  %d zones" % len(feats))
    avcache = {}
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
                e.update(snow_from(sdata.get(st["stationTriplet"], {})))
                e["snotel"] = {"name": st.get("name"), "km": round(dist, 1), "elev": st.get("elevation")}
            try:
                om = open_meteo(a)
                if om:
                    e["om"] = om
            except Exception as ex:
                print("  other models skipped:", ex)
            if a["zone"]:
                z = avalanche_zone(a["lat"], a["lon"], feats)
                zid, link, advice = z.pop("_zid"), z.pop("_link"), z.pop("_advice")
                e.update(z)
                if zid and z["avalCenter"]:
                    try:
                        det = av_detail(z["avalCenter"], zid, avcache)
                        det.update({"link": link, "advice": advice})
                        e["av"] = det
                    except Exception as ex:
                        print("  avalanche detail skipped:", ex)
                prev_e = old.get(a["id"], {})
                oa, changed = prev_e.get("aval"), prev_e.get("changedAt")
                if oa is not None and e["aval"] is not None and oa != e["aval"]:
                    e["prev"], e["changedAt"] = oa, NOW.isoformat()
                elif changed and (NOW - datetime.fromisoformat(changed)) < timedelta(hours=24):
                    e["prev"], e["changedAt"] = prev_e.get("prev", e["aval"]), changed
                else:
                    e["prev"] = e["aval"]
                hist = dict(prev_e.get("avHist") or {})
                cur = (e.get("av", {}).get("danger") or {}).get("current") or {}
                top = e["aval"] or max([v for v in cur.values() if v] or [0]) or None
                if top:
                    hist[NOW.date().isoformat()] = top
                e["avHist"] = dict(sorted(hist.items())[-8:])
            result[a["id"]] = e
            ok += 1
        except Exception as ex:
            print("  skipped:", ex)
            if a["id"] in old:
                result[a["id"]] = dict(old[a["id"]], stale=True)
        time.sleep(0.4)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"schema": 2, "updated": datetime.now(timezone.utc).isoformat(), "areas": result}, open(OUT, "w"), separators=(",", ":"))
    print("Done: %d of %d areas updated" % (ok, len(areas)))
    sys.exit(0 if ok else 1)
 
if __name__ == "__main__":
    main()
 
