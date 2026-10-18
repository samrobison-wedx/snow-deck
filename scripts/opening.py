#!/usr/bin/env python3
"""Snow Deck opening-date collector. Standard library only.

Writes data/opening.json with, for each resort that has one:
  "official": a date the resort itself states (read from its own website, or typed into opening-dates.json; typed dates win)
  "news":     a date a recent news article clearly states, with a link to the article (always shown as an unconfirmed estimate)
Only dates that are still ahead are kept. robots.txt is respected. If nothing is found for a resort, it simply has no entry."""
import json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import terrain as T  # shares fetch(), allowed(), page_text() and the polite User-Agent

ROOT = T.ROOT
OUT = os.path.join(ROOT, "data", "opening.json")
BUDGET = 8 * 60
START = time.time()
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
DATE = re.compile(r"\b" + MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b(?!\s*(?:-|–|to)\s*\d{1,2}\s*(?:years|yrs))(?:,?\s*(20\d\d))?", re.I)
# words that mean "this is about opening day"
KEY = re.compile(r"(opening\s+day|scheduled\s+to\s+open|set\s+to\s+open|plans?\s+to\s+open|will\s+open|expected\s+to\s+open|projected\s+to\s+open|"
                 r"targeting|target\s+(?:opening|date)|first\s+chair|season\s+opens?|opens?\s+for\s+the\s+(?:20\d\d.{0,6}\s+)?season|"
                 r"opens?\s+(?:on\s+)?(?:" + MON + r")|opening\s+(?:on\s+|is\s+)?(?:" + MON + r"))", re.I)
# words that mean the date is about something else (events, passes, sales, past results)
NOISE = re.compile(r"(pass\s+sales?|deadline|price|prices|sale\b|early\s+bird|tickets?\s+on\s+sale|summer|bike|festival|concert|closing|closes?\s+(?:on\s+)?|last\s+day|hiring|job\s+fair|"
                   r"anniversary|ski\s+swap|film|race\b|fireworks)", re.I)

def pick_date(text, today, window=110, strict=False):
    """First opening-looking date in text that is still ahead (within 270 days). Returns (date, snippet) or None."""
    for k in KEY.finditer(text):
        seg = text[max(0, k.start() - 50): k.end() + window]
        for m in DATE.finditer(seg):
            ctx = seg[max(0, m.start() - 45): m.end() + 25]
            if NOISE.search(ctx):
                continue
            mo, d = MONTHS[m.group(1).lower()[:3]], int(m.group(2))
            y = int(m.group(3)) if m.group(3) else None
            try:
                if y is None:
                    y = today.year
                    if datetime(y, mo, d).date() < today - timedelta(days=1):
                        y += 1
                dt = datetime(y, mo, d).date()
            except ValueError:
                continue
            if today <= dt <= today + timedelta(days=270) and mo in (9, 10, 11, 12, 1, 2, 3, 4, 5, 6):
                return dt, re.sub(r"\s+", " ", seg[max(0, m.start() - 70): m.end() + 40]).strip()
    return None

def main():
    today = datetime.now(timezone.utc).astimezone().date()
    out, report = {}, {}
    def slot(i):
        return out.setdefault(i, {})
    # 1) the resorts' own pages
    sources = [s for s in json.load(open(os.path.join(ROOT, "terrain-sources.json"))) if s.get("enabled") is not False]
    def one(s):
        if time.time() - START > BUDGET:
            return s, None, "ran out of time"
        urls = []
        for u in (s.get("urls") or [s["url"]]):
            urls.append(u)
            p = urlparse(u)
            urls.append("%s://%s/" % (p.scheme, p.netloc))
        seen = set()
        for u in urls:
            if u in seen:
                continue
            seen.add(u)
            try:
                if not T.allowed(u):
                    return s, None, "robots.txt does not allow it"
                r = pick_date(T.page_text(T.fetch(u)), today)
                if r:
                    return s, (r[0], r[1], u), "ok"
            except Exception as ex:
                last = "error: %s" % str(ex)[:60]
        return s, None, "no opening date found"
    with ThreadPoolExecutor(max_workers=T.WORKERS) as pool:
        for s, r, why in pool.map(one, sources):
            print("%-22s %s" % (s["id"], ("%s  %s" % (r[0], r[1][:70])) if r else why))
            if r:
                slot(s["id"])["official"] = {"date": r[0].isoformat(), "text": r[1][:160], "url": r[2], "by": "website"}
            else:
                report[s["id"]] = why
    # 2) typed-in dates win: opening-dates.json  {"Alta": {"date": "2026-11-20", "note": "...", "url": "..."}}
    try:
        manual = json.load(open(os.path.join(ROOT, "opening-dates.json")))
    except Exception:
        manual = {}
    for i, v in manual.items():
        if i.startswith("_") or not isinstance(v, dict) or not v.get("date"):
            continue
        try:
            dt = datetime.strptime(v["date"], "%Y-%m-%d").date()
        except ValueError:
            print("opening-dates.json: bad date for", i, v.get("date"))
            continue
        if dt >= today:
            slot(i)["official"] = {"date": dt.isoformat(), "text": v.get("note", ""), "url": v.get("url", ""), "by": "typed"}
    # 3) news articles that clearly state a date
    try:
        news = json.load(open(os.path.join(ROOT, "data", "news.json")))
    except Exception:
        news = {}
    names = {k: v.get("name", k) for k, v in (news.get("sources") or {}).items()}
    for x in sorted(news.get("items", []), key=lambda x: x.get("date", "")):
        areas = x.get("areas") or []
        if not areas or len(areas) > 2:      # ambiguous: skip articles about many mountains
            continue
        try:
            if (datetime.now(timezone.utc) - datetime.fromisoformat(x["date"].replace("Z", "+00:00"))).days > 45:
                continue
        except Exception:
            continue
        text = "%s. %s" % (x.get("title", ""), x.get("summary", ""))
        if not re.search(r"\bopen", text, re.I):
            continue
        r = pick_date(text, today, window=90)
        if r:
            for i in areas:
                slot(i)["news"] = {"date": r[0].isoformat(), "title": x.get("title", "")[:140], "url": x.get("link", ""), "src": names.get(x.get("src"), x.get("src", "")), "published": x.get("date", "")}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"updated": datetime.now(timezone.utc).isoformat(), "resorts": out, "report": report}, open(OUT, "w"), separators=(",", ":"))
    print("\nOpening dates: %d resorts have at least one date (%d from a resort/typed, %d from news)" % (
        len(out), sum(1 for v in out.values() if v.get("official")), sum(1 for v in out.values() if v.get("news"))))

if __name__ == "__main__":
    main()
