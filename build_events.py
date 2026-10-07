#!/usr/bin/env python3
"""Robot qui rassemble les événements de plusieurs sources dans events.json.

Sources :
  1. Spa-Francorchamps : lecture du calendrier officiel (connecteur sur mesure)
  2. Pages web de sources.json : une IA lit chaque page et en extrait les événements
  3. manual_events.json : événements vérifiés à la main

Variables d'environnement : ANTHROPIC_API_KEY (pour l'étape 2, sinon ignorée).
Usage : python3 build_events.py
"""
import hashlib, html, json, os, re, subprocess, sys, tempfile, time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from page_text import fetch, to_text

HERE = Path(__file__).resolve().parent
OUT = HERE / "events.json"
GEOCACHE = HERE / "geocache.json"
MODEL = os.environ.get("EXTRACTION_MODEL", "claude-haiku-4-5-20251001")
CATEGORIES = ["concert", "festival", "party", "theatre", "expo", "cinema", "culture", "sport", "motorsport",
              "outdoor", "food", "flea", "fair", "family", "wellness", "workshop", "talks", "geek"]
CATEGORY_HELP = ("concert=concerts · festival=festivals de musique ou d'arts · party=soirées, clubs, bals · "
                 "theatre=théâtre, humour, danse, cirque, opéra · expo=expositions, musées · cinema=films · "
                 "culture=patrimoine, visites, conférences culturelles · sport=sport, courses, matchs · "
                 "motorsport=sport auto/moto · outdoor=nature, balades, randonnées · food=gastronomie, marchés alimentaires · "
                 "flea=brocantes, vide-greniers · fair=foires, fêtes foraines, marchés de Noël · family=activités enfants/famille · "
                 "wellness=yoga, bien-être · workshop=ateliers, stages · talks=conférences, rencontres, salons · geek=jeux, gaming, BD")
TODAY = date.today()
REPORT = []   # une ligne par source : combien d'événements, ou quelle erreur


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


# ============================================================ géocodage (OpenStreetMap)
_geo = json.loads(GEOCACHE.read_text()) if GEOCACHE.exists() else {}


def geocode(venue, city):
    key = f"{venue}|{city}".lower()
    if key in _geo:
        return _geo[key]
    result = None
    for q in ([f"{venue}, {city}, Belgique"] if venue else []) + [f"{city}, Belgique"]:
        time.sleep(1.1)  # Nominatim : 1 requête par seconde maximum
        r = subprocess.run(["curl", "-s", "--max-time", "20", "-A", "StudioAppsBot/1.0 (events-feed)",
                            f"https://nominatim.openstreetmap.org/search?format=json&limit=1&q={quote(q)}"],
                           capture_output=True)
        try:
            hit = json.loads(r.stdout)[0]
            result = [float(hit["lat"]), float(hit["lon"])]
            break
        except Exception:
            continue
    _geo[key] = result
    GEOCACHE.write_text(json.dumps(_geo, ensure_ascii=False, indent=1))
    return result


# ============================================================ 1. Spa-Francorchamps
SPA = "https://www.spa-francorchamps.be"
MONTHS = {m: i + 1 for i, m in enumerate(
    ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER",
     "NOVEMBER", "DECEMBER"])}
EVENT_HREF = re.compile(r'href="(/en/events/(race|event|trackday)/(\d+)_([^"]+))"')


def spa_calendar_days():
    cal = fetch(f"{SPA}/en/next-events")
    found = {}
    sections = re.split(r'<header id="([A-Z]+)(\d{4})"', cal)
    for i in range(1, len(sections), 3):
        month, year, body = MONTHS[sections[i]], int(sections[i + 1]), sections[i + 2]
        for td in re.findall(r"<td[^>]*>(.*?)</td>", body, re.S):
            num = re.search(r'evts-calendar__number">(\d+)<', td)
            if not num:
                continue
            day = date(year, month, int(num.group(1)))
            for path, kind, eid, _s in EVENT_HREF.findall(td):
                found.setdefault(eid, {"path": path, "kind": kind, "dates": []})["dates"].append(day)
    parts = re.split(r"<h1>(\d{2}/\d{2}/\d{4})</h1>", cal)
    for i in range(1, len(parts), 2):
        day = datetime.strptime(parts[i], "%d/%m/%Y").date()
        for path, kind, eid, _s in EVENT_HREF.findall(parts[i + 1].split("</div></div></div>")[0]):
            e = found.setdefault(eid, {"path": path, "kind": kind, "dates": []})
            if day not in e["dates"]:
                e["dates"].append(day)
    return found


def spa_home_races():
    home = fetch(f"{SPA}/en")
    return {eid: {"path": p, "kind": k, "dates": []} for p, k, eid, _s in EVENT_HREF.findall(home) if k == "race"}


def spa():
    found = spa_calendar_days()
    for eid, e in spa_home_races().items():
        found.setdefault(eid, e)
    out = []
    for eid, e in found.items():
        if e["kind"] == "trackday" and eid != "795":
            continue
        try:
            page = fetch(SPA + e["path"])
        except Exception as ex:
            print("  ! page illisible", e["path"], ex, file=sys.stderr)
            continue
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
        title = clean(h1.group(1)) if h1 else e["path"].split("_")[-1].replace("-", " ").title()
        runs = []
        for d in sorted(set(e["dates"])):
            if runs and (d - runs[-1][1]).days <= 1:
                runs[-1][1] = d
            else:
                runs.append([d, d])
        if not runs:
            text = clean(re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S))
            m = re.search(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s+([A-Za-z]+)\s+(20\d\d)", text)
            if m and m.group(3).upper() in MONTHS:
                mo, y = MONTHS[m.group(3).upper()], int(m.group(4))
                runs = [[date(y, mo, int(m.group(1))), date(y, mo, int(m.group(2)))]]
        img = next((u for u in re.findall(r'(/assets/[^"\' )]+\.(?:jpg|jpeg))', page)
                   if "icons" not in u and "calendar" not in u), None)
        tick = next(iter(re.findall(r'href="(https?://[^"]*(?:ticket|billet)[^"]*)"', page)), None) \
            or "https://eshop.spa-francorchamps.be/"
        paras = [clean(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", page, re.S)]
        summary = next((p for p in paras if len(p) > 90 and "cookie" not in p.lower()), "")
        for start, end in runs:
            if end < TODAY or (start - TODAY).days > 400:
                continue
            out.append({
                "id": f"spa-{eid}-{start.isoformat()}", "title": title,
                "category": "sport" if "marathon" in title.lower() else "motorsport",
                "venue": "Circuit de Spa-Francorchamps", "city": "Stavelot", "lat": 50.4372, "lon": 5.9714,
                "start": start.isoformat() + "T10:00:00", "end": end.isoformat() + "T18:00:00",
                "image": SPA + img if img else None, "url": tick,
                "summary": summary[:400] or "Événement au Circuit de Spa-Francorchamps.",
                "source": "spa-francorchamps.be",
            })
    return out


# ============================================================ 2. Pages lues par l'IA
PROMPT = """Tu extrais les ÉVÉNEMENTS À VENIR (sorties, concerts, spectacles, sport, marchés, foires, expositions, fêtes…) d'une page web.
Date du jour : {today}. Source : {name} ({url}). Ville par défaut : {city}.

Règles strictes :
- N'invente rien. Si une info n'est pas sur la page, mets null.
- Garde uniquement les événements qui ont lieu à partir d'aujourd'hui.
- "start" et "end" au format AAAA-MM-JJ ou AAAA-MM-JJTHH:MM (heure locale). Si l'année manque, déduis-la du contexte (prochaine occurrence).
- "image" : uniquement une URL d'image qui illustre CET événement sur la page (pas un logo ni une icône), sinon null.
- "page_url" : le lien de la PAGE DE L'ÉVÉNEMENT sur le site de la source (pas un site de billetterie externe), tel qu'il apparaît sur la page, sinon null.
- "ticket_url" : le lien de la billetterie / réservation s'il y en a un, sinon null.
- "category" : une seule valeur parmi {cats}. Sens : {cat_help}.
- "summary" : une phrase en français, 200 caractères maximum, tirée de la page.
- "price_from" : prix d'entrée minimum en euros (nombre) si indiqué, sinon null. "free" : true seulement si la page dit gratuit / entrée libre.
- "venue" : nom du lieu si indiqué, sinon null.

Réponds UNIQUEMENT avec un tableau JSON (aucun texte autour), de la forme :
[{{"title":"","start":"","end":null,"venue":null,"city":"","category":"","image":null,"page_url":null,"ticket_url":null,"summary":"","price_from":null,"free":null}}]
Si la page ne contient aucun événement à venir, réponds [].

PAGE :
{page}"""


def call_claude(prompt):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY absente")
    body = {"model": MODEL, "max_tokens": 16000, "messages": [{"role": "user", "content": prompt}]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(body, f)
        path = f.name
    r = subprocess.run(["curl", "-s", "--max-time", "120", "https://api.anthropic.com/v1/messages",
                        "-H", f"x-api-key: {key}", "-H", "anthropic-version: 2023-06-01",
                        "-H", "content-type: application/json", "-d", f"@{path}"], capture_output=True)
    os.unlink(path)
    data = json.loads(r.stdout)
    if "content" not in data:
        raise RuntimeError(f"réponse API inattendue : {str(data)[:200]}")
    return "".join(b.get("text", "") for b in data["content"])


def parse_json_array(text):
    """Lit un tableau JSON, même si la réponse de l'IA est coupée : on garde chaque objet complet."""
    start = text.find("[")
    if start < 0:
        return []
    try:
        return json.loads(text[start:text.rindex("]") + 1])
    except Exception:
        pass
    out, dec, i = [], json.JSONDecoder(), start + 1
    while True:
        m = re.compile(r"\s*,?\s*").match(text, i)
        i = m.end()
        if i >= len(text) or text[i] != "{":
            break
        try:
            obj, i = dec.raw_decode(text, i)
        except Exception:
            break
        out.append(obj)
    return out


PAGE_CACHE_FILE = HERE / "pages_cache.json"


def pages():
    cfg = json.loads((HERE / "sources.json").read_text())["pages"]
    cache = json.loads(PAGE_CACHE_FILE.read_text()) if PAGE_CACHE_FILE.exists() else {}
    out = []
    for s in cfg:
        if not s.get("enabled", True):
            continue
        try:
            text = to_text(fetch(s["url"]), s["url"])
            digest = hashlib.sha256((text + MODEL + s.get("category_hint", "")).encode()).hexdigest()
            hit = cache.get(s["url"])
            if hit and hit.get("hash") == digest and hit.get("day") == TODAY.isoformat()[:7]:
                raw, answer = hit["raw"], ""      # page inchangée ce mois-ci : pas d'appel à l'IA
            else:
                answer = call_claude(PROMPT.format(
                    today=TODAY.isoformat(), name=s["name"], url=s["url"], city=s.get("city", ""),
                    cats=", ".join(CATEGORIES), cat_help=CATEGORY_HELP, page=text))
                raw = parse_json_array(answer)
                cache[s["url"]] = {"hash": digest, "day": TODAY.isoformat()[:7], "raw": raw}
        except Exception as ex:
            print(f"  ! {s['name']} : {ex}", file=sys.stderr)
            REPORT.append({"source": s["name"], "ok": False, "erreur": str(ex)[:300]})
            continue
        n, skipped = 0, 0
        for r in raw:
            try:
                title, start = (r.get("title") or "").strip(), r.get("start")
                if not title or not start:
                    skipped += 1
                    continue
                sd = datetime.fromisoformat(start if "T" in start else start + "T10:00:00")
                ed = datetime.fromisoformat(r["end"] if r.get("end") and "T" in r["end"]
                                            else (r["end"] + "T20:00:00" if r.get("end") else sd.isoformat()))
                if ed.date() < TODAY:
                    skipped += 1
                    continue
                venue = r.get("venue") or s.get("venue")
                city = r.get("city") or s.get("city") or ""
                pos = geocode(venue, city)
                if not pos:
                    skipped += 1
                    continue
                out.append({
                    "id": "web-" + re.sub(r"[^a-z0-9]+", "-", f"{s['name']}-{title}-{sd.date()}".lower())[:80],
                    "title": title,
                    "category": r.get("category") if r.get("category") in CATEGORIES else s.get("category_hint", "culture"),
                    "venue": venue or city, "city": city, "lat": pos[0], "lon": pos[1],
                    "start": sd.isoformat(timespec="seconds"), "end": ed.isoformat(timespec="seconds"),
                    "image": r.get("image"), "page_url": r.get("page_url"),
                    "url": r.get("ticket_url") or r.get("page_url") or r.get("url") or s["url"],
                    "summary": (r.get("summary") or "")[:300],
                    "price_from": r.get("price_from"), "free": r.get("free"),
                    "source": s["name"],
                })
                n += 1
            except Exception as ex:
                print(f"  ! événement ignoré ({r.get('title')}): {ex}", file=sys.stderr)
        print(f"  {s['name']}: {n} événements")
        PAGE_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False))
        REPORT.append({"source": s["name"], "ok": True, "page_caracteres": len(text), "ia_a_trouve": len(raw),
                       "gardes": n, "ecartes": skipped,
                       "extrait_reponse_ia": answer[:200] if not raw else ""})
    return out


# ============================================================ 3. Événements manuels
def manual():
    out = []
    for e in json.loads((HERE / "manual_events.json").read_text())["events"]:
        e = dict(e)
        if e.get("recurrence") == "weekly:sunday":
            start = datetime.fromisoformat(e["start"])
            dur = datetime.fromisoformat(e["end"]) - start
            d = TODAY + timedelta(days=(6 - TODAY.weekday()) % 7)  # prochain dimanche
            for k in range(6):
                day = d + timedelta(weeks=k)
                s = datetime.combine(day, start.time())
                out.append({**e, "id": f"{e['id']}-{day}", "start": s.isoformat(timespec="seconds"),
                            "end": (s + dur).isoformat(timespec="seconds")})
        elif datetime.fromisoformat(e["end"]).date() >= TODAY:
            out.append(e)
    return out



# ============================================================ 4. Lieux d'activités (OpenStreetMap)
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]
CENTER = (50.6326, 5.5797)  # Liège
# (clé OSM, valeur) -> (type d'activité, libellé)
PLACE_KINDS = {
    ("leisure", "bowling_alley"): ("bowling", "Bowling"),
    ("leisure", "escape_game"): ("escape", "Escape game"),
    ("sport", "karting"): ("karting", "Karting"),
    ("sport", "laser_tag"): ("laser", "Laser game"),
    ("leisure", "trampoline_park"): ("trampoline", "Trampoline park"),
    ("sport", "climbing"): ("climbing", "Escalade"),
    ("sport", "billiards"): ("billiards", "Billard"),
    ("leisure", "amusement_arcade"): ("arcade", "Salle d'arcade"),
    ("leisure", "miniature_golf"): ("minigolf", "Mini-golf"),
    ("amenity", "nightclub"): ("club", "Club"),
    ("leisure", "water_park"): ("waterpark", "Parc aquatique"),
}


def places(radius=15000):
    around = f"(around:{radius},{CENTER[0]},{CENTER[1]})"
    body = "".join(f'nwr{around}["{k}"="{v}"];' for (k, v) in PLACE_KINDS)
    query = f"[out:json][timeout:60];({body});out tags center;"
    data = None
    for url in OVERPASS:
        r = subprocess.run(["curl", "-s", "--max-time", "90", "-A", "StudioAppsBot/1.0 (events-feed)",
                            "-H", "Accept: application/json", url, "--data-urlencode", "data=" + query],
                           capture_output=True)
        try:
            data = json.loads(r.stdout)
            break
        except Exception:
            continue
    if data is None:
        raise RuntimeError("OpenStreetMap indisponible")
    out, seen = [], set()
    for el in data.get("elements", []):
        t = el.get("tags", {})
        name = t.get("name")
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if not name or lat is None or re.search(r"casino|poker|paris|loto", name, re.I):
            continue
        kind = next((PLACE_KINDS[(k, v)] for (k, v) in PLACE_KINDS if t.get(k) == v), None)
        if not kind or (name.lower(), kind[0]) in seen:
            continue
        seen.add((name.lower(), kind[0]))
        site = t.get("website") or t.get("contact:website")
        if site and not site.startswith("http"):
            site = "https://" + site
        out.append({
            "id": f"osm-{el['type']}-{el['id']}", "name": name, "kind": kind[0], "kind_label": kind[1],
            "city": t.get("addr:city") or "", "lat": lat, "lon": lon, "website": site,
            "hours": t.get("opening_hours"), "image": None,
        })
    return out


# ============================================================ photos manquantes
BAD_IMG = re.compile(r"logo|favicon|icon|sprite|flag|avatar|placeholder|blank|pixel|spinner|loader|banner-cookie|\.svg|\.gif|qtranslate", re.I)


def best_image(page, base):
    """Photo la plus probable d'une page d'événement : og:image, sinon première vraie image du contenu."""
    from urllib.parse import urljoin
    for pat in (r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)'):
        m = re.search(pat, page, re.I)
        if m:
            img = urljoin(base, html.unescape(m.group(1)))
            if not BAD_IMG.search(img):
                return img
    body = re.sub(r"<(script|style|nav|footer|header)\b.*?</\1>", " ", page, flags=re.S | re.I)
    cands = re.findall(r'(?:src|data-src|data-lazy-src|content)=["\']([^"\']+\.(?:jpe?g|png|webp)(?:\?[^"\']*)?)["\']', body, re.I)
    for srcset in re.findall(r'srcset=["\']([^"\']+)["\']', body, re.I):  # la plus grande version
        parts = [p.strip().split(" ") for p in srcset.split(",") if p.strip()]
        parts = [(p[0], int(p[1][:-1])) for p in parts if len(p) > 1 and p[1].endswith("w") and p[1][:-1].isdigit()]
        if parts:
            cands.insert(0, max(parts, key=lambda x: x[1])[0])
    for c in cands:
        img = urljoin(base, html.unescape(c))
        if not BAD_IMG.search(img) and re.search(r"upload|image|media|photo|affiche|visuel|cache", img, re.I):
            return img
    # dernier recours : n'importe quelle image de la page (hors logos, miniatures et icônes),
    # en préférant les fichiers « originaux » sans suffixe -300x200
    anywhere = re.findall(r'https?://[^"\'\s)<>]+\.(?:jpe?g|png|webp)(?:\?[^"\'\s)<>]*)?', page, re.I)
    anywhere = [u for u in dict.fromkeys(anywhere) if not BAD_IMG.search(u) and not re.search(r"-\d{2,3}x\d{2,3}\.", u)]
    return anywhere[0] if anywhere else None


def fill_images(events, limit=150):
    done = 0
    for e in events:
        link = e.get("page_url") or e.get("url")
        if e.get("image") or not link or done >= limit:
            continue
        try:
            page = fetch(link, timeout=15)
        except Exception:
            continue
        done += 1
        img = best_image(page, link)
        if img:
            e["image"] = img


# ============================================================ assemblage
def main():
    events = []
    for name, fn in [("Spa-Francorchamps", spa), ("Manuel", manual), ("Pages web (IA)", pages)]:
        try:
            got = fn()
            print(f"{name}: {len(got)} événements")
            events += got
        except Exception as ex:
            print(f"{name}: ÉCHEC ({ex})", file=sys.stderr)
    seen, uniq = set(), []
    for e in sorted(events, key=lambda x: x["start"]):
        k = (re.sub(r"\W+", "", e["title"].lower()), e["start"][:10])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    fill_images(uniq)
    try:
        lieux = places()
        for p in lieux[:150]:           # photo du site officiel quand il y en a un
            if p.get("website"):
                try:
                    p["image"] = best_image(fetch(p["website"], timeout=12), p["website"])
                except Exception:
                    pass
        (HERE / "places.json").write_text(json.dumps({"generated": datetime.now().isoformat(timespec="seconds"),
                                                      "places": lieux}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Lieux d'activités: {len(lieux)}")
    except Exception as ex:
        print(f"Lieux d'activités: ÉCHEC ({ex}), on garde la liste précédente", file=sys.stderr)
    (HERE / "rapport.json").write_text(json.dumps(
        {"genere": datetime.now().isoformat(timespec="seconds"), "sources": REPORT,
         "avec_image": sum(1 for e in uniq if e.get("image")), "total": len(uniq)},
        ensure_ascii=False, indent=2), encoding="utf-8")
    OUT.write_text(json.dumps({"generated": datetime.now().isoformat(timespec="seconds"), "events": uniq},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"écrit : {OUT.name}, {len(uniq)} événements")


if __name__ == "__main__":
    main()
