#!/usr/bin/env python3
"""Optional live road incidents for Colorado (COtrip) and Utah (UDOT). Standard library only.

Both agencies give free API keys. Put them in GitHub: Settings > Secrets and variables > Actions > New repository secret:
  COTRIP_API_KEY   (from https://data.cotrip.org/)
  UDOT_API_KEY     (from https://udottraffic.utah.gov/developers/doc)
Without a key that state is simply skipped and the site shows the official link instead.
Writes data/roads.json  {updated, items:[{name, desc, lat, lon, state}]}.
NOTE: the response formats were written from the agencies' public documentation and could not be tested without a key, so the
reader below is deliberately forgiving. If the Data health table says 'Needs free API keys' or 0 reports, tell me and I'll adjust."""
import json, os, sys, urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "roads.json")

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "SnowDeck (personal project)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def pull(rows, state, nm, ds, la, lo):
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            lat, lon = float(r.get(la)), float(r.get(lo))
        except (TypeError, ValueError):
            continue
        out.append({"name": str(r.get(nm) or "Road report")[:80], "desc": str(r.get(ds) or "")[:200], "lat": lat, "lon": lon, "state": state})
    return out

items, ok = [], False
k = os.environ.get("UDOT_API_KEY")
if k:
    try:
        items += pull(get("https://udottraffic.utah.gov/api/v2/get/alerts?key=%s&format=json" % k), "UT", "Title", "Description", "StartLatitude", "StartLongitude")
        ok = True
    except Exception as ex:
        print("UDOT failed:", str(ex)[:100])
k = os.environ.get("COTRIP_API_KEY")
if k:
    try:
        j = get("https://data.cotrip.org/api/v1/incidents?apiKey=%s" % k)
        for f in (j if isinstance(j, list) else j.get("features", [])):
            p = f.get("properties", f)
            g = (f.get("geometry") or {}).get("coordinates") or []
            if g and isinstance(g[0], list):
                g = g[0]
            if len(g) >= 2:
                items.append({"name": str(p.get("type") or p.get("travelerInformationMessage") or "Incident")[:80],
                              "desc": str(p.get("travelerInformationMessage") or p.get("description") or "")[:200], "lat": g[1], "lon": g[0], "state": "CO"})
        ok = True
    except Exception as ex:
        print("COtrip failed:", str(ex)[:100])
if not ok:
    print("No API keys set or both failed; nothing written.")
    sys.exit(0)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump({"updated": datetime.now(timezone.utc).isoformat(), "items": items}, open(OUT, "w"), separators=(",", ":"))
print("saved", len(items), "reports")
