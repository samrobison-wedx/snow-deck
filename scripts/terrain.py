#!/usr/bin/env python3
"""Snow Deck lift and trail collector. Standard library only.
Reads terrain-sources.json, fetches each resort's own page, pulls out
"open / total" numbers with the patterns listed there, and writes data/terrain.json."""
import html, json, os, re, sys, time, urllib.error, urllib.request, urllib.robotparser
from datetime import datetime, timezone
from urllib.parse import urlparse
 
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "terrain.json")
UA = "SnowDeck (https://github.com/%s; personal project)" % os.environ.get("GITHUB_REPOSITORY", "local-test")
 
def fetch(url, accept="text/html,application/xhtml+xml,*/*;q=0.8"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")
 
def allowed(url):
    """Respect robots.txt. If it can't be read, skip the resort to be safe."""
    p = urlparse(url)
    try:
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.parse(fetch("%s://%s/robots.txt" % (p.scheme, p.netloc), "text/plain,*/*;q=0.8").splitlines())
        except urllib.error.HTTPError as he:
            if he.code == 404:  # no robots.txt means no stated restrictions
                return True
            raise
        ok = rp.can_fetch(UA, url)
        if not ok:
            print("  robots.txt says automated access to this page is not allowed")
        return ok
    except Exception as e:
        print("  could not read robots.txt:", e)
        return False
    
 
def page_text(raw):
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))
 
def pull(text, pattern):
    m = re.search(pattern, text, re.I)
    if not m:
        return None
    o, t = int(m.group(1)), int(m.group(2))
    return [o, t] if 0 <= o <= t and t > 0 else None
 
def main():
    sources = json.load(open(os.path.join(ROOT, "terrain-sources.json")))
    try:
        res = json.load(open(OUT)).get("resorts", {})
    except Exception:
        res = {}
    ok = 0
    for s in sources:
        print(s["id"])
        try:
            if not allowed(s["url"]):
                print("  skipped: robots.txt does not allow it, or could not be read")
                continue
            text = page_text(fetch(s["url"]))
            e = {k: pull(text, s[k]) for k in ("lifts", "trails") if s.get(k)}
            e = {k: v for k, v in e.items() if v}
            if not e:
                print("  no numbers found (page layout may have changed or the numbers load after the page opens)")
                print("  page text starts:", text[:300])
                continue
            e["fetched"] = datetime.now(timezone.utc).isoformat()
            res[s["id"]] = e
            ok += 1
            print("  ", e)
        except Exception as ex:
            print("  failed:", ex)
        time.sleep(2)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"updated": datetime.now(timezone.utc).isoformat(), "resorts": res}, open(OUT, "w"), separators=(",", ":"))
    print("Done: %d of %d resorts updated" % (ok, len(sources)))
 
if __name__ == "__main__":
    main()
 
