#!/usr/bin/env python3
"""Snow Deck weekly history builder. Standard library only.
For each SNOTEL station near one of your areas, compares this water year (Oct 1 to Sep 30) with every
earlier year since 1991: the median, the lowest and the highest, for snowpack (water in the snow)
and estimated snowfall (added up from daily snow depth gains). Writes data/history.json."""
import json, os, sys, time, urllib.parse
from datetime import date, datetime, timedelta, timezone
import update as U
 
OUT = os.path.join(U.ROOT, "data", "history.json")
STEP = 3                    # keep one point every 3 days to keep the file small
FIRST_WY = 1992             # water year 1992 starts Oct 1991
MIN_YEARS = 8
 
def wy_of(d):
    return d.year + 1 if d.month >= 10 else d.year
 
def fetch(triplets, begin, end):
    q = urllib.parse.urlencode({"stationTriplets": ",".join(triplets), "elements": "SNWD,WTEQ", "duration": "DAILY",
                                "beginDate": begin.isoformat(), "endDate": end.isoformat()})
    out = {}
    for st in U.get("%s/data?%s" % (U.SNOTEL, q)) or []:
        d = {}
        for el in st.get("data", []):
            code = (el.get("stationElement") or {}).get("elementCode")
            d[code] = {v["date"][:10]: v.get("value") for v in el.get("values", []) if v.get("value") is not None}
        out[st["stationTriplet"]] = d
    return out
 
def build(series, today):
    """series: {"SNWD": {iso: v}, "WTEQ": {iso: v}} -> compact stats, or None if too little history."""
    cur_wy = wy_of(today)
    years = {}
    for el in ("WTEQ", "SNWD"):
        for k, v in series.get(el, {}).items():
            d = date.fromisoformat(k)
            w = wy_of(d)
            idx = (d - date(w - 1, 10, 1)).days
            if 0 <= idx <= 365:
                years.setdefault(w, {"WTEQ": {}, "SNWD": {}})[el][idx] = v
    # estimated snowfall to date: sum of depth gains of 1 inch or more, ignoring jumps over 24 inches
    for w, y in years.items():
        tot, prev, cum = 0.0, None, {}
        for i in range(366):
            v = y["SNWD"].get(i)
            if v is not None:
                if prev is not None and 1 <= v - prev <= 24:
                    tot += v - prev
                prev = v
            if v is not None or prev is not None:
                cum[i] = tot
        y["FALL"] = cum
    done = [w for w in years if w != cur_wy and w >= FIRST_WY and len(years[w]["WTEQ"]) >= 120]
    if len(done) < MIN_YEARS:
        return None
 
    def stat(key, nd):
        med, lo, hi, cur = [], [], [], []
        for i in range(0, 366, STEP):
            vals = sorted(years[w][key][j] for w in done for j in (i,) if years[w][key].get(j) is not None)
            if len(vals) >= MIN_YEARS:
                med.append(round(vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2, nd))
                lo.append(round(vals[0], nd)); hi.append(round(vals[-1], nd))
            else:
                med.append(None); lo.append(None); hi.append(None)
            c = years.get(cur_wy, {}).get(key, {}).get(i)
            cur.append(round(c, nd) if c is not None else None)
        return {"med": med, "lo": lo, "hi": hi, "cur": cur}
    return {"years": len(done), "swe": stat("WTEQ", 1), "fall": stat("FALL", 0)}
 
def main():
    areas = json.load(open(os.path.join(U.ROOT, "areas.json")))
    stations = U.snotel_stations()
    pick = {}
    for a in areas:
        c = [(U.km(a["lat"], a["lon"], s["latitude"], s["longitude"]), s) for s in stations]
        c = [x for x in c if x[0] <= 45]
        if c:
            pick[a["id"]] = min(c, key=lambda x: x[0])[1]
    trip = sorted({s["stationTriplet"] for s in pick.values()})
    names = {s["stationTriplet"]: s.get("name") for s in pick.values()}
    today = datetime.now(U.TZ).date()
    wy0 = date(FIRST_WY - 1, 10, 1)
    out, bad = {}, 0
    print("%d stations" % len(trip))
    for i in range(0, len(trip), 3):
        got = fetch(trip[i:i + 3], wy0, today)
        for t in trip[i:i + 3]:
            r = build(got.get(t, {}), today) if t in got else None
            if r:
                r["name"] = names[t]
                out[t] = r
            else:
                bad += 1
                print("  not enough history:", t)
        time.sleep(1)
    if not out:
        print("No history built.")
        sys.exit(1)
    wy = wy_of(today)
    json.dump({"schema": 1, "updated": datetime.now(timezone.utc).isoformat(), "step": STEP, "wyStart": "%d-10-01" % (wy - 1),
               "stations": out}, open(OUT, "w"), separators=(",", ":"))
    print("Done: %d stations with history, %d without" % (len(out), bad))
 
if __name__ == "__main__":
    main()
 
