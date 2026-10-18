#!/usr/bin/env python3
"""Snow Deck notices + season-snowfall collector. Standard library only (Playwright optional).

Writes data/notices.json:
  resorts[id] = {"notices": [{"text","url"}], "season": inches or null, "seasonUrl": url}
Notices are short sentences the resort's own pages say about closures, holds, wind, delays, road or parking problems.
Only reads pages robots.txt allows. A resort with nothing found simply has no entry."""
import json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import terrain as T

ROOT = T.ROOT
OUT = os.path.join(ROOT, "data", "notices.json")
START = time.time()
BUDGET = 8 * 60
WORDS = re.compile(r"(wind\s+hold|on\s+hold|temporarily\s+closed|closed\s+(?:today|due|until|for)|will\s+remain\s+closed|delayed\s+opening|"
                   r"late\s+start|chains?\s+(?:are\s+)?required|traction\s+law|road\s+(?:closure|closed|conditions)|parking\s+(?:lot\s+)?(?:is\s+)?full|"
                   r"avalanche\s+(?:control|mitigation)|lift\s+(?:maintenance|closure)|alert|advisory|notice|closure)", re.I)
SKIP = re.compile(r"(cookie|privacy|subscribe|newsletter|sign\s+up|terms|©|menu|skip\s+to|season\s+pass|gift\s+card|ticket\s+deals?)", re.I)
SEASON = re.compile(r"(?:season(?:al)?\s+(?:total|snowfall|to\s+date|snow)|snowfall\s+(?:this\s+season|to\s+date|season)|year[-\s]to[-\s]date|ytd|total\s+snowfall)"
                    r"[^0-9]{0,40}(\d{2,3}(?:\.\d)?)\s*(?:\"|”|in\b|inches)", re.I)

def sentences(text):
    for s in re.split(r"(?<=[.!?])\s+|\s{2,}", text):
        s = s.strip()
        if 25 <= len(s) <= 220:
            yield s

def one(s):
    if time.time() - START > BUDGET:
        return s["id"], None, "ran out of time"
    base = urlparse(s["url"])
    urls = []
    for u in (s.get("urls") or [s["url"]]) + ["%s://%s/" % (base.scheme, base.netloc)]:
        if u not in urls:
            urls.append(u)
    notices, season, surl, seen = [], None, "", set()
    for u in urls:
        try:
            if not T.allowed(u):
                return s["id"], None, "robots.txt does not allow it"
            text = T.page_text(T.fetch(u))
        except Exception as ex:
            continue
        # only read what looks like current text: skip the page if it says it is a past season
        for x in sentences(text):
            if WORDS.search(x) and not SKIP.search(x) and x.lower() not in seen and len(notices) < 4:
                seen.add(x.lower())
                notices.append({"text": x, "url": u})
        m = SEASON.search(text)
        if m and season is None:
            v = float(m.group(1))
            if 5 <= v <= 900:
                season, surl = v, u
    if notices or season is not None:
        return s["id"], {"notices": notices, "season": season, "seasonUrl": surl}, "ok"
    return s["id"], None, "nothing found"

def main():
    sources = [s for s in json.load(open(os.path.join(ROOT, "terrain-sources.json"))) if s.get("enabled") is not False]
    res, report = {}, {}
    with ThreadPoolExecutor(max_workers=T.WORKERS) as pool:
        for i, r, why in pool.map(one, sources):
            print("%-22s %s" % (i, "ok" if r else why))
            if r:
                res[i] = r
            else:
                report[i] = why
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"updated": datetime.now(timezone.utc).isoformat(), "resorts": res, "report": report}, open(OUT, "w"), separators=(",", ":"))
    print("saved", len(res), "resorts")

if __name__ == "__main__":
    main()
