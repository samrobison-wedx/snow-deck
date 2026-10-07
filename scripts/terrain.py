#!/usr/bin/env python3
"""Snow Deck lift and trail collector. Standard library only.
Reads terrain-sources.json, fetches each resort's own page, pulls out
"open / total" numbers with the patterns listed there, and writes data/terrain.json.
If a page shows no numbers in its plain HTML (many load them with JavaScript), it is opened in a headless browser
(Playwright) when that is installed, and the same patterns are applied to the rendered text. robots.txt is respected either way."""
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
 
_robots = {}
def allowed(url):
    """Respect robots.txt. If it can't be read, skip the resort to be safe. Answers are remembered per site."""
    p = urlparse(url)
    key = (p.scheme, p.netloc, p.path)
    if key not in _robots:
        _robots[key] = _allowed(url, p)
    return _robots[key]
 
def _allowed(url, p):
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
 
# Common ways resorts write "open / total". Used when a source has no patterns of its own.
# Each pattern must capture (open, total) in that order.
# Two common layouts, tried as a pair so that "5 / 12 Lifts Open 40 / 120 Trails Open" and
# "Lifts Open 5 / 12  Trails Open 40 / 120" are not mixed up. Every pattern captures (open, total).
SEP = r"\s*(?:/|of|out of)\s*"
STYLE_A = {  # number first: "5 / 12 Lifts Open"
    "lifts": [r"(\d+)" + SEP + r"(\d+)\s*lifts?\b"],
    "trails": [r"(\d+)" + SEP + r"(\d+)\s*(?:trails?|runs?)\b"],
}
STYLE_B = {  # label first: "Lifts Open: 5 / 12"
    "lifts": [r"\b(?:open\s+)?lifts?(?:\s*open)?(?:\s*today)?\s*:?\s*(\d+)" + SEP + r"(\d+)"],
    "trails": [r"\b(?:open\s+)?(?:trails?|runs?)(?:\s*open)?(?:\s*today)?\s*:?\s*(\d+)" + SEP + r"(\d+)"],
}
DEFAULTS = {k: STYLE_A[k] + STYLE_B[k] for k in ("lifts", "trails")}
LIMIT = {"lifts": 60, "trails": 500}  # a bigger total is almost certainly a misread
 
def pull(text, patterns, kind="lifts"):
    """Returns [open, total], or [open, None] when a pattern has only the open count."""
    if isinstance(patterns, str):
        patterns = [patterns]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if not m:
            continue
        o = int(m.group(1))
        if m.lastindex and m.lastindex >= 2:
            t = int(m.group(2))
            if 0 <= o <= t and 0 < t <= LIMIT.get(kind, 500):
                return [o, t]
        elif 0 <= o <= LIMIT.get(kind, 500):
            return [o, None]
    return None
 
def extract(s, text):
    """Resort-specific patterns first; anything still missing comes from the better-fitting generic layout."""
    e = {}
    for k in ("lifts", "trails"):
        own = s.get(k) or []
        v = pull(text, [own] if isinstance(own, str) else own, k) if own else None
        if v:
            e[k] = v
    need = [k for k in ("lifts", "trails") if k not in e]
    if need:
        A = {k: pull(text, STYLE_A[k], k) for k in need}
        B = {k: pull(text, STYLE_B[k], k) for k in need}
        na, nb = sum(1 for v in A.values() if v), sum(1 for v in B.values() if v)
        pick = B if nb > na else A
        for k, v in pick.items():
            if v:
                e[k] = v
    return e
 
class Browser:
    """Optional headless browser. Only started if a page needs it and Playwright is installed."""
    def __init__(self):
        self.pw = self.br = None
        self.failed = False
    def text(self, url):
        if self.failed:
            return None
        try:
            if not self.br:
                from playwright.sync_api import sync_playwright
                self.pw = sync_playwright().start()
                self.br = self.pw.chromium.launch()
            pg = self.br.new_page(user_agent=UA)
            try:
                pg.goto(url, wait_until="networkidle", timeout=45000)
            except Exception:
                pass  # some pages never go idle; use whatever has loaded
            pg.wait_for_timeout(2500)
            t = pg.inner_text("body")
            pg.close()
            return re.sub(r"\s+", " ", t)
        except ImportError:
            print("  (Playwright is not installed, so pages that load their numbers by script are skipped)")
            self.failed = True
        except Exception as ex:
            print("  browser error:", str(ex)[:120])
        return None
    def close(self):
        try:
            if self.br: self.br.close()
            if self.pw: self.pw.stop()
        except Exception:
            pass
 
def main():
    sources = json.load(open(os.path.join(ROOT, "terrain-sources.json")))
    try:
        res = json.load(open(OUT)).get("resorts", {})
    except Exception:
        res = {}
    ok, good, bad = 0, [], []
    browser = Browser()
    for s in sources:
        print(s["id"])
        if s.get("enabled") is False:
            print("  off:", s.get("note", ""))
            continue
        try:
            urls = s.get("urls") or [s["url"]]
            e, how, blocked = {}, "", False
            for u in urls:
                if not allowed(u):
                    print("  robots.txt does not allow", u)
                    blocked = True
                    continue
                blocked = False
                text = page_text(fetch(u))
                e = extract(s, text)
                if e:
                    how = "page"
                    break
            if not e and not blocked and not s.get("nojs"):
                text = browser.text(urls[0])
                if text:
                    e = extract(s, text)
                    how = "browser"
                    if not e:
                        print("  rendered text starts:", text[:200])
            if not e:
                why = "robots.txt" if blocked else "no numbers found (closed for the season, or the layout needs a pattern)"
                print("  ", why)
                bad.append((s["id"], why))
                continue
            e["fetched"] = datetime.now(timezone.utc).isoformat()
            res[s["id"]] = e
            ok += 1
            good.append("%s (%s)" % (s["id"], how))
            print("  ", how, e)
        except Exception as ex:
            print("  failed:", ex)
            bad.append((s["id"], "error: %s" % str(ex)[:80]))
        time.sleep(2)
    browser.close()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"updated": datetime.now(timezone.utc).isoformat(), "resorts": res}, open(OUT, "w"), separators=(",", ":"))
    print("\n=== SUMMARY ===")
    print("Working (%d): %s" % (len(good), ", ".join(good) or "none"))
    print("Needs attention (%d):" % len(bad))
    for n, why in bad:
        print("  - %s: %s" % (n, why))
    print("Done: %d of %d resorts updated" % (ok, len(sources)))
 
if __name__ == "__main__":
    main()
 
