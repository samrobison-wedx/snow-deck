#!/usr/bin/env python3
"""Snow Deck news collector. Standard library only.
Reads news-sources.json (RSS/Atom feeds), respects each site's robots.txt, keeps only headline, link, date and a short
snippet, matches each article to the mountains in areas.json by name, and writes data/news.json.
Full articles are never copied: Snow Deck links to the original."""
import email.utils, hashlib, html, json, os, re, sys, time, urllib.error, urllib.request, urllib.robotparser
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
 
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "news.json")
UA = "SnowDeck (https://github.com/%s; personal project)" % os.environ.get("GITHUB_REPOSITORY", "local-test")
KEEP_DAYS, KEEP_MAX, SNIP = 45, 500, 240
 
# Extra names an article might use. Everything not listed here is matched by its own name.
ALIASES = {
    "Aspen Snowmass": ["Snowmass", "Aspen Mountain", "Aspen Highlands", "Buttermilk"],
    "Breckenridge": ["Breck"], "Keystone": [], "Winter Park": ["Winter Park Resort"], "Copper Mountain": ["Copper"],
    "Arapahoe Basin": ["A-Basin", "A Basin", "ABasin"], "Beaver Creek": [], "Steamboat": ["Steamboat Resort", "Steamboat Springs ski"],
    "Crested Butte": ["Crested Butte Mountain Resort"], "Telluride": ["Telluride Ski Resort"], "Purgatory": ["Durango Mountain"],
    "Wolf Creek": ["Wolf Creek Ski Area"], "Silverton Mountain": [], "Monarch": ["Monarch Mountain"], "Loveland": ["Loveland Ski Area"],
    "Eldora": ["Eldora Mountain"], "Sunlight": ["Sunlight Mountain"], "Powderhorn": ["Powderhorn Mountain"], "Ski Cooper": ["Cooper Ski"],
    "Park City": ["Park City Mountain"], "Deer Valley": ["Deer Valley Resort"], "Snowbird": [], "Alta": ["Alta Ski Area"], "Brighton": ["Brighton Resort"],
    "Solitude": ["Solitude Mountain"], "Snowbasin": [], "Powder Mountain": ["Powder Mountain Utah"], "Beaver Mountain": [], "Brian Head": ["Brian Head Resort"],
    "Sundance": ["Sundance Mountain Resort"], "Nordic Valley": [], "Taos": ["Taos Ski Valley"], "Bridger Bowl": [], "Big Sky": ["Big Sky Resort"],
    "Vail": ["Vail Mountain", "Vail Resorts"], "Echo Mountain": [], "Hesperus": ["Hesperus Ski"], "Howelsen Hill": [],
    "Little Cottonwood Canyon": ["Little Cottonwood", "LCC"], "Big Cottonwood Canyon": ["Big Cottonwood"], "Berthoud Pass": [], "Loveland Pass": [],
    "Vail Pass": [], "Hoosier Pass": [], "Independence Pass": [], "Monarch Pass": [], "Kebler Pass": [], "Red Mountain Pass": [], "Molas Pass": [],
    "Lizard Head Pass": [], "Wolf Creek Pass": [], "Cameron Pass": [], "Rabbit Ears Pass": [], "Buffalo Pass": [], "Rocky Mountain National Park": [],
    "Grand Mesa": [], "Park City Ridgeline": [], "Ogden Mountains": ["Ogden Valley"], "Provo and Timpanogos": ["Timpanogos"], "Mirror Lake Highway": [],
    "Palisades Tahoe": ["Palisades", "Squaw Valley", "Alpine Meadows"], "Mammoth Mountain": ["Mammoth", "Mammoth Lakes ski"],
    "Heavenly": ["Heavenly Mountain", "Heavenly Resort"], "Northstar": ["Northstar California"], "Kirkwood": ["Kirkwood Mountain"],
    "Jackson Hole": ["Jackson Hole Mountain Resort", "JHMR"], "Grand Targhee": ["Targhee"], "Whitefish Mountain": ["Whitefish Mountain Resort", "Big Mountain Whitefish"],
    "Sun Valley": ["Sun Valley Resort", "Bald Mountain Idaho"], "Mount Bachelor": ["Mt. Bachelor", "Mt Bachelor"],
    "Mt. Hood Meadows": ["Hood Meadows", "Mt Hood Meadows", "Mount Hood Meadows"], "Timberline": ["Timberline Lodge"],
    "Mt. Hood Skibowl": ["Skibowl", "Mount Hood Skibowl"], "Cooper Spur": ["Cooper Spur Mountain Resort"], "Summit Ski Area": ["Summit Ski Area Mount Hood"],
    "Killington": ["Killington Resort"], "Stowe": ["Stowe Mountain Resort"], "Sugarbush": ["Sugarbush Resort"], "Okemo": ["Okemo Mountain"],
    "Stratton": ["Stratton Mountain"], "Mount Snow": ["Mt. Snow", "Mt Snow"], "Jay Peak": ["Jay Peak Resort"], "Smugglers' Notch": ["Smugglers Notch", "Smuggs"],
    "Mad River Glen": [], "Mont Tremblant": ["Tremblant"], "Lake Louise": ["Lake Louise Ski Resort", "SkiLouise"], "Sunshine Village": ["Banff Sunshine", "Sunshine Village Banff"],
    "Revelstoke": ["Revelstoke Mountain Resort"], "Kicking Horse": ["Kicking Horse Mountain Resort"], "Sunday River": [], "Sugarloaf": ["Sugarloaf Mountain"],
    "Saddleback": ["Saddleback Maine"], "Shawnee Peak": [], "Loon Mountain": ["Loon Mountain Resort", "Loon"], "Cannon Mountain": ["Cannon"],
    "Attitash": ["Attitash Mountain"], "Wildcat": ["Wildcat Mountain"], "Waterville Valley": [], "Bretton Woods": [], "Mount Sunapee": ["Mt. Sunapee", "Sunapee"],
    "Gunstock": ["Gunstock Mountain"], "Santa Fe": ["Ski Santa Fe"], "Angel Fire": ["Angel Fire Resort"], "Red River": ["Red River Ski Area"],
    "Crystal Mountain": ["Crystal Mountain Washington", "Crystal Mountain Resort"], "Stevens Pass": [], "Mt. Baker": ["Mount Baker", "Mt Baker", "Baker Ski Area"],
    "Wachusett": ["Wachusett Mountain"],
    "Logan Canyon": [], "La Sal Mountains": ["La Sals"], "Abajo Mountains": ["Abajos"],
}
# Names that are also ordinary words or places. They only count when the text also has a ski or snow word.
AMBIGUOUS = {"Alta", "Vail", "Eldora", "Taos", "Sunlight", "Brighton", "Solitude", "Monarch", "Sundance", "Loveland", "Keystone", "Copper",
              "Telluride", "Purgatory", "Beaver Creek", "Big Sky", "Steamboat", "Breckenridge", "Park City", "Deer Valley", "Aspen Snowmass",
              "Copper Mountain", "Winter Park", "Crested Butte", "Wolf Creek", "Powderhorn", "Hesperus", "Snowbasin", "Snowbird", "Buffalo Pass", "Grand Mesa",
              "Heavenly", "Northstar", "Kirkwood", "Sun Valley", "Mount Bachelor", "Timberline", "Cooper Spur", "Summit Ski Area", "Stowe", "Okemo", "Stratton",
              "Wildcat", "Attitash", "Gunstock", "Santa Fe", "Red River", "Angel Fire", "Crystal Mountain", "Stevens Pass", "Saddleback", "Wachusett", "Cannon Mountain",
              "Loon Mountain", "Jackson Hole", "Mammoth Mountain", "Palisades Tahoe", "Killington", "Sugarloaf", "Mount Snow", "Mount Sunapee", "Whitefish Mountain",
              "Revelstoke", "Kicking Horse", "Lake Louise", "Mont Tremblant", "Shawnee Peak", "Bretton Woods", "Waterville Valley"}
CONTEXT = re.compile(r"\b(ski|skiing|skier|skiers|snow|snowboard|snowboarder|resort|lift|lifts|slopes|powder|avalanche|backcountry|trail map|gondola|opening day|closing day|season pass|epic pass|ikon pass|terrain|chairlift|snowpack|snowfall)\b", re.I)
 
def fetch(url, accept="text/html,*/*;q=0.8"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read()
 
_robots = {}
def allowed(url):
    """Respect robots.txt. A missing robots.txt (404) means no stated limits. If it can't be read, skip the source."""
    p = urlparse(url)
    key = p.scheme + "://" + p.netloc
    if key not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            try:
                rp.parse(fetch(key + "/robots.txt", "text/plain,*/*;q=0.8").decode("utf-8", "replace").splitlines())
            except urllib.error.HTTPError as he:
                if he.code != 404:
                    raise
                rp.parse([])
            _robots[key] = rp
        except Exception as e:
            print("  could not read robots.txt:", e)
            _robots[key] = None
    rp = _robots[key]
    if rp is None:
        return False
    ok = rp.can_fetch(UA, url)
    if not ok:
        print("  robots.txt does not allow automated access to this feed")
    return ok
 
def clean(s, n=SNIP):
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s or "")
    s = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()
    s = re.sub(r"\s+(The post .*? appeared first on .*)$", "", s)
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"
 
def when(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)
 
def local(tag):
    return tag.rsplit("}", 1)[-1]
 
def parse_feed(raw):
    if b"<!ENTITY" in raw:
        raise ValueError("feed uses XML entities; skipped for safety")
    root = ET.fromstring(raw)
    items = []
    for el in root.iter():
        t = local(el.tag)
        if t not in ("item", "entry"):
            continue
        f = {}
        for c in el:
            k = local(c.tag)
            if k == "link" and c.get("href"):
                if c.get("rel", "alternate") == "alternate" or "link" not in f:
                    f["link"] = c.get("href")
            elif k in ("title", "link", "pubDate", "published", "updated", "date", "description", "summary", "encoded", "content") and k not in f:
                f[k] = (c.text or "").strip()
        link = f.get("link", "")
        if not link.lower().startswith(("http://", "https://")) or not f.get("title"):
            continue
        d = when(f.get("pubDate") or f.get("published") or f.get("date") or f.get("updated"))
        items.append({"title": clean(f["title"], 200), "link": link, "date": d, "summary": clean(f.get("description") or f.get("summary") or f.get("encoded") or f.get("content"))})
    return items
 
def build_matchers(areas):
    m = []
    for a in areas:
        names = [a["name"]] + ALIASES.get(a["name"], [])
        pats = []
        for n in names:
            esc = re.escape(n).replace(r"\ ", r"[\s-]+")
            pats.append(r"\b" + esc + r"\b")
        m.append((a["id"], re.compile("|".join(pats), re.I), a["name"] in AMBIGUOUS and not a["zone"]))
    return m
 
def match(text, matchers):
    ctx = bool(CONTEXT.search(text))
    return [i for i, rx, amb in matchers if rx.search(text) and (ctx or not amb)]
 
def main():
    sources = json.load(open(os.path.join(ROOT, "news-sources.json")))
    areas = json.load(open(os.path.join(ROOT, "areas.json")))
    matchers = build_matchers(areas)
    try:
        old = json.load(open(OUT))
    except Exception:
        old = {}
    items = {x["link"]: x for x in old.get("items", [])}
    status, got = {}, 0
    for s in sources:
        if not s.get("enabled", True):
            continue
        print(s["name"])
        st = {"name": s["name"], "ok": False, "count": 0, "region": s.get("region"), "type": s.get("type")}
        status[s["id"]] = st
        try:
            if not allowed(s["url"]):
                st["note"] = "robots.txt did not allow it, or could not be read"
                continue
            found = parse_feed(fetch(s["url"], "application/rss+xml,application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.5"))
            st.update(ok=True, count=len(found))
            if found:
                st["newest"] = max([x["date"] for x in found if x["date"]] or [datetime.now(timezone.utc)]).isoformat()
            for x in found:
                d = x["date"] or datetime.now(timezone.utc)
                items[x["link"]] = {"title": x["title"], "link": x["link"], "src": s["id"], "date": d.isoformat(), "summary": x["summary"],
                                    "areas": match(x["title"] + " " + x["summary"], matchers)} if x["link"] not in items else dict(items[x["link"]], areas=match(x["title"] + " " + x["summary"], matchers))
            got += 1
            print("  %d articles" % len(found))
        except Exception as e:
            st["note"] = str(e)[:160]
            print("  skipped:", e)
        time.sleep(1.0)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    keep = sorted([x for x in items.values() if x["date"] >= cutoff], key=lambda x: x["date"], reverse=True)[:KEEP_MAX]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"schema": 1, "updated": datetime.now(timezone.utc).isoformat(), "sources": status, "items": keep}, open(OUT, "w"), separators=(",", ":"))
    print("Done: %d of %d sources worked, %d articles kept (%d matched to a mountain)" % (got, len(status), len(keep), sum(1 for x in keep if x["areas"])))
    sys.exit(0 if got else 1)
 
if __name__ == "__main__":
    main()    
