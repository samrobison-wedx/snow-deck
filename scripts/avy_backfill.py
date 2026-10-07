#!/usr/bin/env python3
"""Tries to fill in past avalanche danger ratings (past 5 winters) from avalanche.org so zones can be compared with
earlier seasons. Best effort: avalanche.org's archive format could not be checked ahead of time, so this exits
quietly (and prints what it saw) if the format is not what it expects. Writes into data/avy-archive.json."""
import json, os, sys, time, urllib.parse
from datetime import date, datetime, timedelta
import update as U
 
AVH = os.path.join(U.ROOT, "data", "avy-archive.json")
 
def months(start, end):
    d = date(start.year, start.month, 1)
    while d <= end:
        n = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        yield d, min(n - timedelta(days=1), end)
        d = n
 
def top_level(prod, valid=None):
    best = None
    for x in prod.get("danger") or []:
        if valid and x.get("valid_day") not in (valid, None):
            continue
        for b in ("upper", "middle", "lower"):
            v = U.lvl(x.get(b))
            if v and (best is None or v > best):
                best = v
        if best:
            break
    return best
 
def main():
    areas = [a for a in json.load(open(os.path.join(U.ROOT, "areas.json"))) if a["zone"]]
    try:
        store = json.load(open(AVH))
    except Exception:
        store = {"schema": 1, "areas": {}}
    done = set(store.get("backfilled", []))
    feats = (U.get(U.AV + "/products/map-layer") or {}).get("features", [])
    zones = {}
    for a in areas:
        z = U.avalanche_zone(a["lat"], a["lon"], feats)
        if z["_zid"] and z["avalCenter"]:
            zones.setdefault((z["avalCenter"], str(z["_zid"])), []).append(a["id"])
    today = date.today()
    first = date(today.year - 5, 10, 1)
    added, seen_shape = 0, False
    for center in sorted({c for c, _ in zones}):
        for m0, m1 in months(first, today - timedelta(days=1)):
            key = "%s:%s" % (center, m0.isoformat()[:7])
            if key in done or (today - m1).days < 3 and m1 == today - timedelta(days=1):
                continue
            q = urllib.parse.urlencode({"avalanche_center_id": center, "date_start": m0.isoformat(), "date_end": m1.isoformat()})
            j = U.get("%s/products?%s" % (U.AV, q), tries=2)
            items = j if isinstance(j, list) else (j or {}).get("products") if isinstance(j, dict) else None
            if not isinstance(items, list):
                print("  unexpected response for", key, str(j)[:200])
                continue
            for p in items:
                if not isinstance(p, dict) or not p.get("published_time"):
                    continue
                if not seen_shape:
                    print("  sample keys:", sorted(p.keys())[:20])
                    seen_shape = True
                day = str(p["published_time"])[:10]
                lv = top_level(p, "current")
                zs = {str(z.get("id")) for z in (p.get("forecast_zone") or []) if isinstance(z, dict)}
                for (c, zid), ids in zones.items():
                    if c == center and (not zs or zid in zs) and lv:
                        for aid in ids:
                            store["areas"].setdefault(aid, {})[day] = lv
                            added += 1
            done.add(key)
            time.sleep(0.5)
    store["backfilled"] = sorted(done)
    json.dump(store, open(AVH, "w"), separators=(",", ":"))
    print("Backfill added %d ratings" % added)
 
if __name__ == "__main__":
    main()
