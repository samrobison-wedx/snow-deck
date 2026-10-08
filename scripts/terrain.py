#!/usr/bin/env python3
"""Snow Deck lift and trail collector. Standard library only (Playwright is optional).
 
Reads terrain-sources.json, fetches each resort's own page, pulls out "open / total" numbers and writes data/terrain.json.
Step 1: plain page fetch (several resorts at a time).
Step 2: pages that show no numbers (many load them with JavaScript) are opened in a headless browser, if Playwright is installed.
Safety checks, so old or made-up numbers are not shown as current:
  - a page that says it was last updated more than 3 days ago is ignored
  - a page that says the resort is closed for the season is recorded as "closed" instead of showing counts
  - generic (non resort-specific) patterns only count when the word "open" is right next to the numbers
robots.txt is respected. Results are saved after each step, and the run stops by itself after about 20 minutes."""
import html, json, os, re, sys, time, urllib.error, urllib.request, urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
 
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "terrain.json")
UA = "SnowDeck (https://github.com/%s; personal project)" % os.environ.get("GITHUB_REPOSITORY", "local-test")
START = time.time()
BUDGET = 20 * 60      # seconds; after this the run saves what it has and stops
STALE_DAYS = 3
WORKERS = 6
 
def fetch(url, accept="text/html,application/xhtml+xml,*/*;q=0.8"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")
 
_robots = {}
def allowed(url):
    """Respect robots.txt. If it can't be read, skip the resort to be safe. Answers are remembered per page."""
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
        return rp.can_fetch(UA, url)
    except Exception:
        return False
 
def page_text(raw):
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))
 
# ---------- reading the numbers ----------
SEP = r"\s*(?:/|of|out of)\s*"
STYLE_A = {  # number first: "5 / 12 Lifts Open"
    "lifts": [r"(\d+)" + SEP + r"(\d+)\s*lifts?\b"],
    "trails": [r"(\d+)" + SEP + r"(\d+)\s*(?:trails?|runs?)\b"],
}
STYLE_B = {  # label first: "Lifts Open: 5 / 12"
    "lifts": [r"\b(?:open\s+)?lifts?(?:\s*open)?(?:\s*today)?\s*:?\s*(\d+)" + SEP + r"(\d+)"],
    "trails": [r"\b(?:open\s+)?(?:trails?|runs?)(?:\s*open)?(?:\s*today)?\s*:?\s*(\d+)" + SEP + r"(\d+)"],
}
LIMIT = {"lifts": 60, "trails": 500}  # a bigger total is almost certainly a misread
 
def pull(text, patterns, kind="lifts", need_open=False):
    """Returns [open, total], or [open, None] when a pattern has only the open count."""
    if isinstance(patterns, str):
        patterns = [patterns]
    for pat in patterns:
        for m in re.finditer(pat, text, re.I):
            if need_open and not re.search(r"open", text[max(0, m.start() - 40):m.end() + 40], re.I):
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
        A = {k: pull(text, STYLE_A[k], k, True) for k in need}
        B = {k: pull(text, STYLE_B[k], k, True) for k in need}
        na, nb = sum(1 for v in A.values() if v), sum(1 for v in B.values() if v)
        for k, v in (B if nb > na else A).items():
            if v:
                e[k] = v
    return e
 
# ---------- old or closed-season pages ----------
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
STAMP = re.compile(r"(?:last\s+updated|updated|as\s+of|report\s+(?:date|time))\s*(?:on|at)?\s*:?\s*(?:[a-z]+day,?\s+)?"
                   r"(?:" + MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(20\d\d))?|(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?)", re.I)
CLOSED = re.compile(r"closed\s+for\s+(?:the\s+)?(?:(?:20)?\d\d\s*[-–/]\s*(?:20)?\d\d\s+)?(?:season|summer|winter|offseason|off-season)"
                    r"|season\s+(?:has\s+)?(?:ended|is\s+over|is\s+complete)|see\s+you\s+(?:next\s+)?(?:season|winter|fall)|until\s+next\s+(?:season|winter)", re.I)
 
def newest_stamp(text, today):
    """Most recent 'updated ...' date mentioned on the page, or None if there is none."""
    best = None
    for m in STAMP.finditer(text):
        try:
            if m.group(1):
                mo, d, y = MONTHS[m.group(1).lower()[:3]], int(m.group(2)), int(m.group(3)) if m.group(3) else None
            else:
                mo, d, y = int(m.group(4)), int(m.group(5)), m.group(6)
                y = (int(y) + 2000 if int(y) < 100 else int(y)) if y else None
            if y is None:
                y = today.year
                if datetime(y, mo, d).date() > today + timedelta(days=2):
                    y -= 1
            dt = datetime(y, mo, d).date()
        except (ValueError, KeyError):
            continue
        if best is None or dt > best:
            best = dt
    return best
 
def judge(s, text):
    """Returns (result dict or None, reason). result may be {'closed': True}."""
    e = extract(s, text)
    today = datetime.now(timezone.utc).date()
    st = newest_stamp(text, today)
    if e and st and (today - st).days > STALE_DAYS:
        return None, "page says it was last updated %s (old data, ignored)" % st.isoformat()
    opened = any(v and v[0] > 0 for v in e.values())
    if CLOSED.search(text) and not opened:
        return {"closed": True}, "closed for the season"
    if not e:
        return None, "no numbers found"
    # sanity: open can never exceed the total, and totals must look like a real resort
    for k, cap in (("lifts", 60), ("trails", 400)):
        v = e.get(k)
        if v and v[1] is not None and (v[0] > v[1] or v[1] > cap or v[1] == 0):
            return None, "implausible %s numbers %s (ignored)" % (k, v)
    # outside the ski season only the few resorts that open early or run late may report counts
    if today.month in (5, 6, 7, 8, 9, 10) and not s.get("early"):
        return None, "off-season, counts ignored"
    # pages not yet checked by hand must show a fresh 'updated' date, otherwise they could be old marketing text
    if s.get("unverified") and not (st and (today - st).days <= STALE_DAYS):
        return None, "unverified page with no recent 'updated' date (ignored)"
    return e, "ok"
 
# ---------- optional headless browser ----------
class Browser:
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
                pg.goto(url, wait_until="domcontentloaded", timeout=25000)
                try:
                    pg.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                pg.wait_for_timeout(1500)
                t = pg.inner_text("body")
            finally:
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
 
def out_of_time():
    return time.time() - START > BUDGET
 
def main():
    sources = [s for s in json.load(open(os.path.join(ROOT, "terrain-sources.json"))) if s.get("enabled") is not False]
    try:
        res = json.load(open(OUT)).get("resorts", {})
    except Exception:
        res = {}
    good, bad = [], {}
 
    def save():
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        json.dump({"updated": datetime.now(timezone.utc).isoformat(), "resorts": res,
                   "report": {"ok": good, "problems": bad}}, open(OUT, "w"), separators=(",", ":"))
 
    def record(s, e, why, how):
        now = datetime.now(timezone.utc).isoformat()
        if e:
            e["fetched"] = now
            if s.get("early"):
                e["early"] = True
            res[s["id"]] = e
            good.append(s["id"])
            bad.pop(s["id"], None)
        else:
            bad[s["id"]] = why
 
    # step 1: plain pages
    def step1(s):
        try:
            for u in (s.get("urls") or [s["url"]]):
                if not allowed(u):
                    return s, None, "robots.txt does not allow it", ""
                e, why = judge(s, page_text(fetch(u)))
                if e:
                    return s, e, why, "page"
            return s, None, why, ""
        except Exception as ex:
            return s, None, "error: %s" % str(ex)[:80], ""
    todo = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for s, e, why, how in pool.map(step1, sources):
            print("%-22s %s %s" % (s["id"], how or "-", (e or why)))
            if e:
                record(s, e, why, how)
            elif not s.get("nojs") and why in ("no numbers found",) and not out_of_time():
                todo.append(s)
            else:
                bad[s["id"]] = why
    save()
 
    # step 2: headless browser for the rest
    browser = Browser()
    for s in todo:
        if out_of_time():
            bad[s["id"]] = "ran out of time"
            continue
        try:
            text = browser.text((s.get("urls") or [s["url"]])[0])
            if text is None:
                bad[s["id"]] = "browser not available or page did not load"
            else:
                e, why = judge(s, text)
                print("%-22s browser %s" % (s["id"], e or why))
                record(s, e, why, "browser")
        except Exception as ex:
            bad[s["id"]] = "error: %s" % str(ex)[:80]
        time.sleep(1)
    browser.close()
    save()
 
    print("\n=== SUMMARY ===")
    print("Working (%d): %s" % (len(good), ", ".join(good) or "none"))
    print("Needs attention (%d):" % len(bad))
    for n, why in sorted(bad.items()):
        print("  - %s: %s" % (n, why))
    print("Done in %d seconds" % (time.time() - START))
 
if __name__ == "__main__":
    main()
 
