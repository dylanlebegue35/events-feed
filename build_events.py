#!/usr/bin/env python3
"""Robot qui rassemble les événements de plusieurs sources dans events.json.

Sources :
  1. Spa-Francorchamps : lecture du calendrier officiel (connecteur sur mesure)
  2. Pages web de sources.json : une IA lit chaque page et en extrait les événements
  3. manual_events.json : événements vérifiés à la main
  4. Propositions envoyées depuis l'app et acceptées par Dylan (boîte Cloudflare, variable INBOX_TOKEN)

Variables d'environnement : ANTHROPIC_API_KEY (pour l'étape 2, sinon ignorée).
Usage : python3 build_events.py
"""
import base64, hashlib, html, json, os, re, subprocess, sys, tempfile, time, unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
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


def geocode(venue, city, world=False):
    """world=True : pour les propositions venues d'ailleurs (Paris, Lille…) on ne force pas la Belgique."""
    key = f"{venue}|{city}".lower() + ("|world" if world else "")
    if key in _geo:
        return _geo[key]
    result = None
    queries = ([f"{venue}, {city}"] if venue else []) + [city] if world else \
        ([f"{venue}, {city}, Belgique"] if venue else []) + [f"{city}, Belgique"]
    for q in queries:
        time.sleep(1.1)  # Nominatim : 1 requête par seconde maximum
        extra = "&countrycodes=be,fr,nl,lu,de,gb" if world else ""
        r = subprocess.run(["curl", "-s", "--max-time", "20", "-A", "StudioAppsBot/1.0 (events-feed)",
                            f"https://nominatim.openstreetmap.org/search?format=json&limit=1{extra}&q={quote(q)}"],
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
- "audience" : une seule valeur parmi student (soirée/événement pensé pour étudiants ou cercles étudiants), young (surtout 18-30 ans : clubs, concerts rock/électro/rap, festivals, afterworks), adult, family (enfants/parents), senior, all (tout public). Si rien n'indique le public, "all".
- "tags" : jusqu'à 4 mots-clés courts en français parmi : soirée, afterwork, entre potes, en plein air, gratuit, étudiant, romantique, découverte, sportif, créatif, gourmand. Seulement s'ils sont justifiés par la page.
- "summary" : une phrase en français, 200 caractères maximum, tirée de la page.
- "price_from" : prix d'entrée minimum en euros (nombre) si indiqué, sinon null. "free" : true seulement si la page dit gratuit / entrée libre.
- "venue" : nom du lieu si indiqué, sinon null.

Réponds UNIQUEMENT avec un tableau JSON (aucun texte autour), de la forme :
[{{"title":"","start":"","end":null,"venue":null,"city":"","category":"","image":null,"page_url":null,"ticket_url":null,"audience":"all","tags":[],"summary":"","price_from":null,"free":null}}]
Si la page ne contient aucun événement à venir, réponds [].

PAGE :
{page}"""


def call_claude(prompt, images=None):
    """images : liste de (type_mime, octets) à joindre au message (affiches, captures d'écran)."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY absente")
    content = [{"type": "image", "source": {"type": "base64", "media_type": mt,
                                             "data": base64.b64encode(data).decode()}}
               for mt, data in (images or [])] + [{"type": "text", "text": prompt}]
    body = {"model": MODEL, "max_tokens": 16000, "messages": [{"role": "user", "content": content}]}
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
            digest = hashlib.sha256((PROMPT + text + MODEL + s.get("category_hint", "")).encode()).hexdigest()
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
            old = cache.get(s["url"])
            if not (old and old.get("raw")):
                REPORT.append({"source": s["name"], "ok": False, "erreur": str(ex)[:300]})
                continue
            # L'IA ou le site est indisponible : on garde la dernière lecture réussie plutôt que de vider l'app.
            raw, answer, text = old["raw"], "", ""
            REPORT.append({"source": s["name"], "ok": False, "erreur": "lecture indisponible, dernière lecture réutilisée : " + str(ex)[:200]})
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
                    "audience": [r["audience"]] if r.get("audience") not in (None, "", "all") else [],
                    "tags": [t for t in (r.get("tags") or []) if isinstance(t, str)][:4],
                    "source": s["name"],
                })
                n += 1
            except Exception as ex:
                print(f"  ! événement ignoré ({r.get('title')}): {ex}", file=sys.stderr)
        print(f"  {s['name']}: {n} événements")
        PAGE_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False))
        if REPORT and REPORT[-1].get("source") == s["name"] and not REPORT[-1].get("ok"):
            REPORT[-1].update({"gardes": n, "ecartes": skipped})       # repli : on complète la ligne déjà écrite
        else:
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



# ============================================================ 5. Boîte de dépôt (affiches et captures d'écran)
INBOX = HERE / "inbox"
POSTERS = HERE / "posters"
INBOX_STORE = HERE / "inbox_events.json"
RAW_BASE = "https://raw.githubusercontent.com/dylanlebegue35/events-feed/main/posters/"
IMG_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}

INBOX_NOTE = ("Les informations sont dans l'IMAGE jointe (affiche, flyer ou capture d'écran d'une page d'événement, "
              "souvent Facebook). Lis tout le texte visible : titre, date, heure, lieu, prix, public visé. "
              "Ignore les éléments d'interface (boutons, compteurs d'invités). "
              "Si l'année manque, déduis la prochaine occurrence. Une affiche peut annoncer plusieurs dates : "
              "fais un événement par date.")


def inbox():
    """Lit les images et textes déposés dans inbox/, crée les événements, range les fichiers traités."""
    store = json.loads(INBOX_STORE.read_text()) if INBOX_STORE.exists() else []
    files = sorted(p for p in INBOX.iterdir()
                   if p.is_file() and not p.name.startswith(".") and (p.suffix.lower() in IMG_TYPES or p.suffix.lower() == ".txt"))
    for p in files:
        try:
            images, page = None, ""
            if p.suffix.lower() in IMG_TYPES:
                data = p.read_bytes()
                if len(data) > 4_500_000:
                    raise RuntimeError("image trop lourde (plus de 4,5 Mo)")
                images = [(IMG_TYPES[p.suffix.lower()], data)]
                page = INBOX_NOTE
            else:
                page = INBOX_NOTE.replace("l'IMAGE jointe", "le TEXTE ci-dessous") + "\n\n" + p.read_text(errors="ignore")[:20000]
            answer = call_claude(PROMPT.format(today=TODAY.isoformat(), name="Dépôt", url="(dépôt)", city="Liège",
                                               cats=", ".join(CATEGORIES), cat_help=CATEGORY_HELP, page=page), images)
            raw = parse_json_array(answer)
            poster_url = None
            if images:
                POSTERS.mkdir(exist_ok=True)
                target = POSTERS / re.sub(r"[^A-Za-z0-9._-]+", "-", p.name)
                p.replace(target)
                poster_url = RAW_BASE + target.name
            else:
                (INBOX / "done").mkdir(exist_ok=True)
                p.replace(INBOX / "done" / p.name)
            n = 0
            for r in raw:
                ev = event_from_ai(r, "Dépôt", poster_url)
                if ev:
                    store.append(ev)
                    n += 1
            REPORT.append({"source": f"Dépôt : {p.name}", "ok": True, "ia_a_trouve": len(raw), "gardes": n})
        except Exception as ex:
            print(f"  ! dépôt {p.name} : {ex}", file=sys.stderr)
            REPORT.append({"source": f"Dépôt : {p.name}", "ok": False, "erreur": str(ex)[:300]})
    # on garde les événements à venir, sans doublons
    seen, keep = set(), []
    for e in store:
        k = (re.sub(r"\W+", "", e["title"].lower()), e["start"][:10])
        if k in seen or datetime.fromisoformat(e["end"]).date() < TODAY:
            continue
        seen.add(k)
        keep.append(e)
    INBOX_STORE.write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
    return keep


# ============================================================ propositions envoyées depuis l'app (boîte Cloudflare)
INBOX_URL = os.environ.get("INBOX_URL", "https://vivyo-inbox.dylanlebegue35.workers.dev")
SUBMITTED_STORE = HERE / "submitted_events.json"
ACK_FILE = Path(tempfile.gettempdir()) / "vivyo_ack.json"


def _box(path, payload=None):
    """Appelle la boîte de réception (mot de passe : variable INBOX_TOKEN, jamais écrit dans un fichier)."""
    cmd = ["curl", "-s", "--max-time", "30", "-H", f"Authorization: Bearer {os.environ['INBOX_TOKEN']}"]
    if payload is not None:
        cmd += ["-X", "POST", "-H", "Content-Type: application/json", "-d", json.dumps(payload)]
    r = subprocess.run(cmd + ["--", INBOX_URL + path], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"boîte de réception injoignable (curl {r.returncode})")
    data = json.loads(r.stdout.decode("utf-8"))
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"boîte de réception : {data['error']}")
    return data


def _km(a, b):
    from math import asin, cos, radians, sin, sqrt
    la1, lo1, la2, lo2 = map(radians, (a[0], a[1], b[0], b[1]))
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 12742 * asin(sqrt(h))


def event_from_submission(it):
    """Une proposition acceptée par Dylan devient une entrée de events.json (ou None)."""
    start = datetime.fromisoformat(it["start"].replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Brussels")).replace(tzinfo=None)
    end = start + timedelta(hours=3)
    if end.date() < TODAY:
        return None
    city, venue = (it.get("city") or "").strip(), (it.get("venue") or "").strip() or None
    city_pos = geocode(None, city, world=True)
    pos = geocode(venue, city, world=True) if venue else None
    if pos and city_pos and _km(pos, city_pos) > 25:     # un homonyme à l'autre bout du pays : on garde la ville
        pos = None
    pos = pos or city_pos
    if not pos:
        raise RuntimeError(f"lieu introuvable : {venue or ''} {city}")
    poster_url = None
    if it.get("image"):
        POSTERS.mkdir(exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", f"sub-{it['id']}.jpg")
        (POSTERS / name).write_bytes(base64.b64decode(it["image"]))
        poster_url = RAW_BASE + name
    price = re.search(r"\d+(?:[.,]\d+)?", it.get("price") or "")
    link = it.get("link") or ""
    return {
        "id": "sub-" + it["id"],
        "title": it["title"],
        "category": it.get("category") if it.get("category") in CATEGORIES else "culture",
        "venue": venue or city, "city": city, "lat": pos[0], "lon": pos[1],
        "start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds"),
        "image": poster_url, "page_url": link if link.startswith("http") else None,
        "url": link if link.startswith("http") else None,
        "summary": (it.get("details") or "")[:300],
        "price_from": None if it.get("free") or not price else float(price.group().replace(",", ".")),
        "free": True if it.get("free") else None,
        "audience": [], "tags": [],
        "source": "Proposé via l'app",
    }


def submissions():
    """Récupère les propositions acceptées dans la boîte et les ajoute aux événements."""
    store = json.loads(SUBMITTED_STORE.read_text()) if SUBMITTED_STORE.exists() else []
    if not os.environ.get("INBOX_TOKEN"):
        REPORT.append({"source": "Propositions de l'app", "ok": False, "erreur": "INBOX_TOKEN absent : ignorées"})
        return store
    known, ack, n = {e["id"] for e in store}, [], 0
    for it in _box("/accepted"):
        ack.append(it["id"])
        if "sub-" + it["id"] in known:
            continue
        try:
            ev = event_from_submission(it)
            if ev:
                store.append(ev)
                n += 1
        except Exception as ex:
            print(f"  ! proposition {it.get('title')} : {ex}", file=sys.stderr)
            REPORT.append({"source": f"Proposition : {it.get('title')}", "ok": False, "erreur": str(ex)[:300]})
    store = [e for e in store if datetime.fromisoformat(e["end"]).date() >= TODAY]
    SUBMITTED_STORE.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    ACK_FILE.write_text(json.dumps(ack))     # on ne vide la boîte qu'une fois le travail bien enregistré (étape suivante)
    REPORT.append({"source": "Propositions de l'app", "ok": True, "recues": len(ack), "nouvelles": n})
    return store


def acknowledge():
    """Vide de la boîte les propositions déjà enregistrées (appelé après l'envoi sur GitHub)."""
    if not (os.environ.get("INBOX_TOKEN") and ACK_FILE.exists()):
        return
    ids = json.loads(ACK_FILE.read_text())
    if ids:
        print("boîte vidée :", _box("/ack", {"ids": ids}))


# ============================================================ événements publics Facebook, via un service tiers (Apify)
# ATTENTION : lire Facebook par programme est contre le règlement de Meta. Le service tiers peut s'arrêter, ou Meta peut
# réagir. Rien de ceci n'utilise un compte Facebook. Désactivable en un mot : "enabled": false dans facebook_searches.json.
FB_CONFIG = HERE / "facebook_searches.json"
FB_STORE = HERE / "facebook_events.json"
FB_CITIES = json.loads(FB_CONFIG.read_text()).get("cities", []) if FB_CONFIG.exists() else []
FB_CATEGORY = {"MUSIC": "concert", "NIGHTLIFE": "party", "PARTY": "party", "COMEDY": "theatre", "THEATER": "theatre",
               "ART": "expo", "FOOD_AND_DRINK": "food", "SPORTS": "sport", "FAMILY": "family", "FILM": "cinema",
               "FITNESS_AND_WELLNESS": "wellness", "WORKSHOP": "workshop", "CLASSES": "workshop", "GAMING": "geek"}
FB_PARTY = re.compile(r"soir[ée]e|party|club|\bdj\b|afterwork|techno|rave|bal\b|\btd\b|cantus|guindaille", re.I)
FB_STUDENT = re.compile(r"[ée]tudiant|cercle|bapt[eê]me|\bbleus?\b|\btd\b|cantus|guindaille|erasmus|campus", re.I)
FB_FREE = re.compile(r"gratuit|entr[ée]e libre|free entry|\bfree\b|vrij toegang|gratis", re.I)


def _fb_event(it, query):
    """Un résultat du service tiers devient une entrée de events.json (ou None)."""
    if it.get("isCanceled") or it.get("isPast") or it.get("isOnline") or not it.get("name") or not it.get("id"):
        return None
    start_raw = it.get("utcStartDate") or it.get("startTime")
    if not start_raw:
        return None
    start = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
    start = (start.astimezone(ZoneInfo("Europe/Brussels")) if start.tzinfo else start).replace(tzinfo=None)
    end_raw = it.get("utcEndDate")
    try:
        end = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
        end = (end.astimezone(ZoneInfo("Europe/Brussels")) if end.tzinfo else end).replace(tzinfo=None)
    except Exception:
        end = start + timedelta(hours=3)
    if end < start or end - start > timedelta(days=3):
        end = start + timedelta(hours=3)
    if end.date() < TODAY:
        return None
    loc = it.get("location") or {}
    city = (loc.get("city") or "").strip() or next((c for c in FB_CITIES if c.lower() in (query or "").lower()), (query or "").strip())
    city = re.sub(r",\s*(Belgium|Belgique|België|France|Nederland|Netherlands)$", "", city, flags=re.I)
    venue = (loc.get("name") or "").strip() or None
    if loc.get("latitude") and loc.get("longitude"):
        pos = [float(loc["latitude"]), float(loc["longitude"])]
    else:
        pos = geocode(venue, city, world=True) or geocode(None, city, world=True)
    if not pos:
        return None
    text = f"{it['name']} {it.get('description') or ''}"
    cats = [c.upper() for c in (it.get("discoveryCategories") or []) if isinstance(c, str)]
    category = next((FB_CATEGORY[c] for c in cats if c in FB_CATEGORY), None)
    if FB_PARTY.search(it["name"]):
        category = "party"
    category = category or "culture"
    audience = ["student"] if FB_STUDENT.search(text) else (["young"] if category == "party" else [])
    ext = [x for x in (it.get("externalLinks") or []) if isinstance(x, str) and x.startswith("http")]
    return {
        "id": "fb-" + str(it["id"]),
        "title": clean(it["name"])[:140],
        "category": category,
        "venue": venue or city, "city": city, "lat": pos[0], "lon": pos[1],
        "start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds"),
        "image": it.get("imageUrl"),
        "page_url": it.get("url"),
        "url": ext[0] if ext else it.get("url"),
        "summary": clean(it.get("description") or "")[:300],
        "price_from": None, "free": True if FB_FREE.search(text + " " + str(it.get("ticketsInfo") or "")) else None,
        "audience": audience, "tags": [],
        "source": "Facebook (événement public)",
    }


def _fb_rehost(ev):
    """L'adresse de l'image Facebook expire en quelques jours : on garde une copie dans posters/."""
    url = ev.get("image")
    if not url or url.startswith(RAW_BASE):
        return
    POSTERS.mkdir(exist_ok=True)
    name = f"{ev['id']}.jpg"
    r = subprocess.run(["curl", "-sL", "--max-time", "30", "--", url], capture_output=True)
    if r.returncode == 0 and 3_000 < len(r.stdout) < 4_000_000 and r.stdout[:3] in (b"\xff\xd8\xff", b"\x89PN", b"RIF"):
        (POSTERS / name).write_bytes(r.stdout)
        ev["image"] = RAW_BASE + name
    else:
        ev["image"] = None


FB_CLASSIFY_PROMPT = """Tu aides une app de sorties (Belgique, France). Voici des résultats de recherche d'événements Facebook.
Pour CHAQUE élément, réponds avec un objet JSON :
- "id" : l'identifiant donné
- "keep" : false si ce n'est PAS un vrai événement où des gens se retrouvent à une date précise : revente de billets d'attractions
  ou de visites touristiques (GetYourGuide…), « louer un atelier », publicité, annonce commerciale, cours en ligne. Sinon true.
- "category" : une seule valeur parmi {cats}
- "audience" : une seule valeur parmi student (soirée ou événement pensé pour étudiants, cercles, TD, baptêmes),
  young (surtout 18-30 ans : clubs, concerts rock/électro/rap, afterworks), adult, family, senior, all (tout public).
Sois strict sur "student" : seulement si c'est clairement pour des étudiants.
Catégories : {cat_help}
Réponds UNIQUEMENT par un tableau JSON.

{items}"""


def fb_classify(events):
    """Une IA trie les résultats : vrais événements ou non, catégorie, public visé. Ne touche qu'aux événements pas encore triés."""
    todo = [e for e in events if not e.get("classified")]
    if not todo or not os.environ.get("ANTHROPIC_API_KEY"):
        return events
    verdict = {}
    for i in range(0, len(todo), 40):
        batch = todo[i:i + 40]
        lines = "\n".join(json.dumps({"id": e["id"], "titre": e["title"], "lieu": e["venue"], "ville": e["city"],
                                       "texte": e["summary"][:160]}, ensure_ascii=False) for e in batch)
        try:
            raw = parse_json_array(call_claude(FB_CLASSIFY_PROMPT.format(cats=", ".join(CATEGORIES), cat_help=CATEGORY_HELP, items=lines)))
            verdict.update({r["id"]: r for r in raw if isinstance(r, dict) and r.get("id")})
        except Exception as ex:
            print(f"  ! tri Facebook : {ex}", file=sys.stderr)
    out = []
    for e in events:
        v = verdict.get(e["id"])
        if v is not None:
            if v.get("keep") is False:
                continue
            if v.get("category") in CATEGORIES:
                e["category"] = v["category"]
            e["audience"] = [v["audience"]] if v.get("audience") in ("student", "young", "adult", "family", "senior") else []
            e["classified"] = True
        out.append(e)
    return out


def fb_extension_posters(cfg, store, token):
    """Les événements relevés avec l'extension n'ont pas d'affiche : on demande au service tiers ces événements précis
    (par leur lien) et on copie l'affiche tout de suite. Payant (~0,003 $ par événement) : "posters_per_run" à 0 = arrêté."""
    tried = set(store.get("posters_tried") or [])
    todo = []
    for e in json.loads((HERE / "manual_events.json").read_text())["events"]:
        fid = e["id"][4:] if e["id"].startswith("fbx-") else None
        if (fid and not e.get("image") and e["id"] not in tried and datetime.fromisoformat(e["end"]).date() >= TODAY
                and not (POSTERS / f"fb-{fid}.jpg").exists() and not (POSTERS / f"{e['id']}.jpg").exists()):
            todo.append(e)
    todo = todo[:cfg["posters_per_run"]]
    if not todo:
        return
    payload = {"startUrls": [{"url": f"https://www.facebook.com/events/{e['id'][4:]}/"} for e in todo], "maxEvents": len(todo)}
    cap = round(len(todo) * cfg.get("cost_usd_per_event", 0.004), 2)
    r = subprocess.run(
        ["curl", "-s", "--max-time", "290", "-X", "POST", "-H", "Content-Type: application/json", "-d", json.dumps(payload),
         f"https://api.apify.com/v2/acts/{cfg.get('actor', 'alfalfa~facebook-events-scraper')}/run-sync-get-dataset-items"
         f"?token={token}&maxTotalChargeUsd={cap}&timeout=280"], capture_output=True)
    items = json.loads(r.stdout.decode("utf-8", errors="ignore") or "[]")
    if not isinstance(items, list):
        raise RuntimeError(str(items)[:200])
    by_id = {str(it.get("id")): it for it in items if isinstance(it, dict)}
    ok = 0
    for e in todo:
        it = by_id.get(e["id"][4:])
        if it and it.get("imageUrl"):
            ev = {"id": e["id"], "image": it["imageUrl"]}
            _fb_rehost(ev)
            ok += bool(ev["image"])
        tried.add(e["id"])
    store["posters_tried"] = sorted(tried)
    REPORT.append({"source": "Facebook : affiches de l'extension", "ok": True, "demandes": len(todo), "recus": len(items), "copiees": ok})


def facebook():
    cfg = json.loads(FB_CONFIG.read_text()) if FB_CONFIG.exists() else {}
    store = json.loads(FB_STORE.read_text()) if FB_STORE.exists() else {"last_run": None, "events": []}
    cities = cfg.get("cities") or []
    centers = [p for p in (geocode(None, c, world=True) for c in cities) if p]
    radius = cfg.get("max_km_from_city", 45)

    def tidy(events):
        """On garde l'à-venir, près d'une de nos villes (la recherche de Facebook déborde parfois très loin), puis l'IA trie."""
        up = [e for e in events if datetime.fromisoformat(e["end"]).date() >= TODAY]
        if centers:
            up = [e for e in up if any(_km([e["lat"], e["lon"]], c) <= radius for c in centers)]
        return fb_classify(up)

    token = os.environ.get("APIFY_TOKEN")
    if cfg.get("enabled") and token and cfg.get("posters_per_run", 0) > 0:
        try:
            fb_extension_posters(cfg, store, token)
        except Exception as ex:
            print(f"  ! affiches de l'extension : {str(ex).replace(token, '***')}", file=sys.stderr)
    if not cfg.get("enabled") or not token:
        store["events"] = tidy(store["events"])
        FB_STORE.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
        REPORT.append({"source": "Facebook (service tiers)", "ok": True, "etat": "désactivé", "gardes": len(store["events"])})
        return store["events"]
    last = store.get("last_run")
    if last and (datetime.now() - datetime.fromisoformat(last)) < timedelta(days=cfg.get("min_days_between_runs", 7)):
        store["events"] = tidy(store["events"])
        FB_STORE.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
        REPORT.append({"source": "Facebook (service tiers)", "ok": True, "etat": "pas encore l'heure", "gardes": len(store["events"])})
        return store["events"]
    known, new_ids = {e["id"] for e in store["events"]}, set()
    queries = cfg.get("queries") or [f"{k} {c}".strip() for c in cities for k in (cfg.get("keywords") or [""])]
    spent_cap = cfg.get("max_cost_usd_per_query", 0.30)
    for q in queries:
        try:
            payload = {"searchQueries": [q], "maxEvents": cfg.get("max_events_per_query", 40)}
            r = subprocess.run(
                ["curl", "-s", "--max-time", "290", "-X", "POST", "-H", "Content-Type: application/json", "-d", json.dumps(payload),
                 f"https://api.apify.com/v2/acts/{cfg.get('actor', 'alfalfa~facebook-events-scraper')}/run-sync-get-dataset-items"
                 f"?token={token}&maxTotalChargeUsd={spent_cap}&timeout=280"], capture_output=True)
            items = json.loads(r.stdout.decode("utf-8", errors="ignore") or "[]")
            if not isinstance(items, list):
                raise RuntimeError(str(items)[:200].replace(token, "***"))
            got = 0
            for it in items:
                ev = _fb_event(it, q)
                if ev and ev["id"] not in known:
                    _fb_rehost(ev)          # tout de suite : l'adresse de l'affiche donnée par Facebook expire en quelques jours
                    store["events"].append(ev)
                    known.add(ev["id"])
                    new_ids.add(ev["id"])
                    got += 1
            REPORT.append({"source": f"Facebook : {q}", "ok": True, "recus": len(items), "nouveaux": got})
        except Exception as ex:
            print(f"  ! facebook {q} : {str(ex).replace(token, '***')}", file=sys.stderr)
            REPORT.append({"source": f"Facebook : {q}", "ok": False, "erreur": str(ex).replace(token, "***")[:300]})
    store["events"] = tidy(store["events"])
    for i in new_ids - {e["id"] for e in store["events"]}:      # écarté par le tri : on ne garde pas son affiche
        (POSTERS / f"{i}.jpg").unlink(missing_ok=True)
    store["last_run"] = datetime.now().isoformat(timespec="seconds")
    FB_STORE.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    return store["events"]


def event_from_ai(r, source_name, poster_url=None, default_city="", default_venue=None, hint="culture"):
    """Transforme la réponse de l'IA pour un événement en entrée du fichier events.json (ou None)."""
    title, start = (r.get("title") or "").strip(), r.get("start")
    if not title or not start:
        return None
    sd = datetime.fromisoformat(start if "T" in start else start + "T10:00:00")
    end = r.get("end")
    ed = datetime.fromisoformat(end if end and "T" in end else (end + "T23:00:00" if end else sd.isoformat()))
    if ed.date() < TODAY:
        return None
    venue = r.get("venue") or default_venue
    city = r.get("city") or default_city or "Liège"
    pos = geocode(venue, city)
    if not pos:
        return None
    return {
        "id": "web-" + re.sub(r"[^a-z0-9]+", "-", f"{source_name}-{title}-{sd.date()}".lower())[:80],
        "title": title,
        "category": r.get("category") if r.get("category") in CATEGORIES else hint,
        "venue": venue or city, "city": city, "lat": pos[0], "lon": pos[1],
        "start": sd.isoformat(timespec="seconds"), "end": ed.isoformat(timespec="seconds"),
        "image": poster_url or r.get("image"), "page_url": r.get("page_url"),
        "url": r.get("ticket_url") or r.get("page_url") or r.get("url"),
        "summary": (r.get("summary") or "")[:300],
        "price_from": r.get("price_from"), "free": r.get("free"),
        "audience": [r["audience"]] if r.get("audience") not in (None, "", "all") else [],
        "tags": [t for t in (r.get("tags") or []) if isinstance(t, str)][:4],
        "source": source_name,
    }



# ============================================================ photos libres de droit (Wikimedia Commons)
IMG_CACHE_FILE = HERE / "image_cache.json"
_img_cache = json.loads(IMG_CACHE_FILE.read_text()) if IMG_CACHE_FILE.exists() else {}
# Wikimedia fait attendre quand on lui demande beaucoup (~6 s par recherche en moyenne) : on lui accorde au plus
# 3 minutes par passage pour rester sous les 25 minutes du robot. Le cache est enregistré à chaque passage,
# donc les photos se complètent au fil des passages.
IMAGE_LOOKUP_SECONDS = 180
_lookup_spent = 0.0
_desc = {}   # texte décrivant chaque photo (titre + description), pour vérifier le lieu
OK_LICENSES = re.compile(r"^(CC0|CC BY|CC BY-SA|Public domain|PD|Attribution)", re.I)
BAD_TITLE = re.compile(r"logo|map|carte|plan|diagram|schema|svg|flag|drapeau|coat|blason|poster|affiche|screenshot|icon|televis|broadcast|engraving|gravure|portrait|manuscript|stamp|coin|painting|drawing", re.I)

def commons_images(query, n=8, min_w=900, require=None, generic=False):
    """Plusieurs photos libres pour une recherche : liste de (url, crédit). Résultats mémorisés."""
    key = "L|" + query + ("|" + ",".join(require) if require else "") + ("|g" if generic else "")
    if key in _img_cache:
        for x in _img_cache[key]:
            if len(x) > 2:
                _desc[x[0]] = x[2]
        return [tuple(x[:2]) for x in _img_cache[key]]
    global _lookup_spent
    if _lookup_spent >= IMAGE_LOOKUP_SECONDS:
        return []                                    # temps du passage épuisé : on réessaiera au prochain
    started = time.time()
    try:
        return _commons_search(key, query, n, min_w, require, generic)
    finally:
        _lookup_spent += time.time() - started


def _commons_search(key, query, n, min_w, require, generic):
    params = ("action=query&format=json&generator=search&gsrnamespace=6&gsrlimit=30&prop=imageinfo"
              "&iiprop=url|size|extmetadata&iiurlwidth=1000&gsrsearch=" + quote(query + " filetype:bitmap"))
    body = None
    for wait in (0, 6, 20, 45):
        time.sleep(wait)
        r = subprocess.run(["curl", "-s", "--max-time", "25", "-A", "StudioAppsBot/1.0 (events-feed; contact via GitHub dylanlebegue35)",
                            "-w", "\nHTTP %{http_code}", "https://commons.wikimedia.org/w/api.php?" + params], capture_output=True)
        text = r.stdout.decode("utf-8", "ignore")
        if text.rsplit("\nHTTP", 1)[-1].strip() == "200":
            body = text.rsplit("\nHTTP", 1)[0]
            break
    time.sleep(1.3)
    if body is None:
        return []
    found, seen_artists = [], set()
    try:
        pages_ = sorted(json.loads(body).get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
        for p in pages_:
            ii = (p.get("imageinfo") or [{}])[0]
            meta = ii.get("extmetadata", {})
            lic = meta.get("LicenseShortName", {}).get("value", "")
            w, h = ii.get("width", 0), ii.get("height", 0)
            if not ii.get("thumburl") or not OK_LICENSES.match(lic) or BAD_TITLE.search(p.get("title", "")):
                continue
            if w < min_w or h < 500 or w < h * 1.1:
                continue
            said = (p.get("title", "") + "\n" + meta.get("ObjectName", {}).get("value", "") + " "
                    + re.sub(r"<[^>]+>", " ", meta.get("ImageDescription", {}).get("value", ""))).lower()
            if require and not any(tok in said for tok in require):
                continue
            if generic and query.lower() not in said:
                continue
            artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip() or "auteur inconnu"
            if re.search(r"\b1[0-8]\d\d\b|mercator|atlas|teynard|imprimerie|lithograph|etching", artist + " " + said[:300], re.I):
                continue                                     # œuvre ou photo ancienne : pas pour une app de sorties
            if artist in seen_artists:                   # variété : pas dix photos du même photographe
                continue
            seen_artists.add(artist)
            found.append((ii["thumburl"], f"{artist[:60]} / Wikimedia Commons, {lic}"))
            _desc[ii["thumburl"]] = said
            if len(found) >= n:
                break
    except Exception:
        found = []
    _img_cache[key] = [[u, c, _desc.get(u, "")] for u, c in found]
    IMG_CACHE_FILE.write_text(json.dumps(_img_cache, ensure_ascii=False, indent=1))
    return found


GENERIC_WORDS = set("le la les de du des d l un une et au aux en sur the of and escape game karting bowling club salle studio rock bloc dalle partie centrale gauche droite face ouest est nord sud massif "
                    "centre center parc park liege liège belgique belgium mini golf laser trampoline arcade billard pub bar".split())


def distinctive_tokens(name):
    """Mots propres au lieu (ex. « Rocher de Ham » -> rocher, ham) : sans mots génériques."""
    words = re.findall(r"[a-zA-Zà-ÿ]{3,}", (name or "").lower())
    return [w for w in words if w not in GENERIC_WORDS]


def _plain(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def venue_photo(name, city):
    """Photo libre du lieu précis, ou rien. Le nom du fichier doit contenir tous les mots propres au lieu, et la photo
    doit être située dans la ville ou en Belgique : jamais l'« illustration » d'un autre lieu (un bowling américain…)."""
    tokens = distinctive_tokens(name)
    if not tokens or not any(len(t) >= 6 for t in tokens):
        return None
    # tous les mots du nom, même « mini-golf » ou « rocher » : une photo du village de Crisnée n'est pas son mini-golf
    words = [w for w in re.findall(r"[a-zA-Zà-ÿ]{3,}", name.lower()) if w not in ("les", "des", "une", "aux", "sur", "the", "and")]
    region = [_plain(w) for w in re.findall(r"[a-zA-Zà-ÿ]{4,}", city or "")] + ["liege", "belgi", "wallon", "bruxelles", "brussel"]
    for url, credit in commons_images(f'"{name}" {city}'.strip(), n=4, require=tokens):
        title, _, rest = _plain(_desc.get(url, "")).partition("\n")
        if all(_plain(w) in title for w in words) and any(w in title + rest for w in region):
            return url, credit
    return None


# ============================================================ photos manquantes
BAD_IMG = re.compile(r"logo|favicon|icon|sprite|flag|avatar|placeholder|blank|pixel|spinner|loader|banner-cookie|\.svg|\.gif|qtranslate|cropped-|site-icon|apple-touch|thumb-?\d{2,3}x\d{2,3}", re.I)
# l'og:image des pages Facebook (lookaside.fbsbx.com) n'est pas une image : une petite page HTML qui renvoie
# vers facebook.com/photo.php (il faut un compte). On ne la garde jamais ; l'app montre alors la photo de la catégorie.
FB_FAKE_IMG = re.compile(r"lookaside\.(fbsbx|facebook)\.com|static\.xx\.fbcdn\.net/rsrc\.php", re.I)   # + image de l'interface


def best_image(page, base):
    """Photo la plus probable d'une page d'événement : og:image, sinon première vraie image du contenu."""
    img = _best_image(page, base)
    if img and FB_FAKE_IMG.search(img):
        return None
    return img.replace("http://", "https://", 1) if img else img          # l'iPhone refuse les images non sécurisées


def _best_image(page, base):
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


def own_site(name, url):
    """Le site est-il celui du lieu ? Son adresse doit contenir un mot propre au lieu :
    « rbcesneux.be » (un club de basket) n'est pas le site du Mini-golf du Panorama."""
    host = _plain(re.sub(r"^https?://(www\.)?", "", url or "").split("/")[0])
    return any(_plain(t) in host for t in distinctive_tokens(name))


def is_image(url):
    """L'adresse renvoie-t-elle vraiment une image ? (et pas une page d'erreur ou de connexion)"""
    r = subprocess.run(["curl", "-sL", "-o", "/dev/null", "--max-time", "10", "-r", "0-2047", "-w", "%{http_code} %{content_type}",
                        "--", url], capture_output=True, text=True)
    code, _, ctype = r.stdout.partition(" ")
    return code in ("200", "206") and ctype.startswith("image/")


def fill_images(events, limit=150):
    done = 0
    for e in events:
        link = e.get("page_url") or e.get("url")
        if e.get("image") or not link or done >= limit:
            continue
        if re.match(r"https?://([a-z0-9-]+\.)?(facebook\.com|fb\.me)/", link, re.I):
            continue            # une page Facebook ne donne qu'une fausse image (voir FB_FAKE_IMG)
        try:
            page = fetch(link, timeout=15)
        except Exception:
            continue
        done += 1
        img = best_image(page, link)
        if img and is_image(img):
            e["image"] = img


# ============================================================ assemblage
# ============================================================ Ticketmaster (clé gardée côté robot, jamais dans l'app)
TM_HUBS = [("Liège", 50.6326, 5.5797), ("Bruxelles", 50.8503, 4.3517), ("Namur", 50.4674, 4.8720),
           ("Charleroi", 50.4108, 4.4446), ("Gand", 51.0543, 3.7174), ("Anvers", 51.2194, 4.4025), ("Arlon", 49.6833, 5.8167)]   # Belgique d'abord ; d'autres pays quand on les développera
TM_SEGMENTS = ["Music", "Sports", "Arts & Theatre", "Miscellaneous"]


def _tm_category(segment, genre):
    g, s = genre.lower(), segment.lower()
    has = lambda *w: any(x in g for x in w)
    if has("motor", "racing"):
        return "motorsport"
    if s == "music":
        if has("festival"):
            return "festival"
        return "party" if has("dance", "electronic", "club", "dj") else "concert"
    if s == "sports":
        return "sport"
    if s == "film":
        return "cinema"
    if s == "arts & theatre":
        return "expo" if has("fine art", "museum", "exhibit", "photograph") else "theatre"
    if has("fair", "fête"):
        return "fair"
    if has("festival"):
        return "festival"
    if has("family", "children", "kids"):
        return "family"
    if has("gaming", "esport", "comic", "convention"):
        return "geek"
    if has("lecture", "seminar", "talk", "conference"):
        return "talks"
    return "culture"


def ticketmaster():
    key = (os.environ.get("TICKETMASTER_KEY") or "").strip()   # un copier-coller ajoute parfois un saut de ligne
    if not key:
        raise RuntimeError("TICKETMASTER_KEY absente")
    found, errors, answered = {}, [], 0
    for city, lat, lon in TM_HUBS:
        for seg in TM_SEGMENTS:
            url = ("https://app.ticketmaster.com/discovery/v2/events.json?apikey=" + quote(key) +
                   f"&latlong={lat},{lon}&radius=60&unit=km&countryCode=BE&size=100&sort=date,asc&locale=*&classificationName={quote(seg)}")  # Belgique seulement : le rayon déborde sur la France, le Luxembourg, etc.
            raw = subprocess.run(["curl", "-s", "--max-time", "30", url], capture_output=True, text=True).stdout
            time.sleep(0.4)
            try:
                data = json.loads(raw)
            except Exception:
                errors.append(f"réponse illisible : {raw[:120]!r}")
                continue
            # Ticketmaster répond par {"fault": {...}} ou {"errors": [...]} quand il refuse (clé invalide, limite atteinte…)
            fault = (data.get("fault") or {}).get("faultstring") or "; ".join(e.get("detail") or e.get("code") or "" for e in data.get("errors") or [])
            if fault:
                errors.append(fault)
                continue
            answered += 1
            items = data.get("_embedded", {}).get("events", [])
            for it in items:
                try:
                    venue = it["_embedded"]["venues"][0]
                    la, lo = float(venue["location"]["latitude"]), float(venue["location"]["longitude"])
                    start = it["dates"]["start"].get("dateTime") or (it["dates"]["start"]["localDate"] + "T20:00:00Z")
                    sd = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Brussels")).replace(tzinfo=None)
                except Exception:
                    continue
                if sd.date() < TODAY:
                    continue
                cl = (it.get("classifications") or [{}])[0]
                imgs = sorted(it.get("images") or [], key=lambda i: i.get("width") or 0)
                big = [i for i in imgs if (i.get("width") or 0) >= 640]
                img = (next((i for i in big if i.get("ratio") == "16_9"), None) or (big[0] if big else None) or (imgs[-1] if imgs else {})).get("url")
                prices = [p["min"] for p in it.get("priceRanges") or [] if p.get("min") is not None]
                title = it["name"].strip()
                base = title.split(" - ")[0].strip()
                k = (re.sub(r"\W+", "", base.lower()), (venue.get("name") or "").lower())
                ev = {
                    "id": "tm-" + it["id"], "title": base, "category": _tm_category((cl.get("segment") or {}).get("name", ""), (cl.get("genre") or {}).get("name", "")),
                    "venue": venue.get("name") or city, "city": (venue.get("city") or {}).get("name") or city, "lat": la, "lon": lo,
                    "start": sd.isoformat(timespec="seconds"), "end": sd.isoformat(timespec="seconds"),
                    "image": img, "url": it.get("url"), "summary": (it.get("info") or it.get("pleaseNote") or "")[:300],
                    "price_from": min(prices) if prices else None, "free": None, "audience": [], "tags": [], "source": "Ticketmaster",
                }
                if k in found:                       # même spectacle sur plusieurs dates : une seule fiche, « jusqu'au » la dernière
                    first = found[k]
                    if ev["start"] < first["start"]:
                        ev["end"] = max(first["end"], ev["end"]); found[k] = ev
                    else:
                        first["end"] = max(first["end"], ev["end"])
                else:
                    found[k] = ev
    if errors:
        why = " | ".join(sorted(set(errors)))
        if not answered:                     # toutes les requêtes refusées : on le dit clairement au lieu de « 0 événements »
            raise RuntimeError(f"Ticketmaster refuse toutes les requêtes : {why}")
        print(f"Ticketmaster : {len(errors)} requêtes refusées ({why})", file=sys.stderr)
    return list(found.values())


SPONSORS_FILE = HERE / "sponsors.json"


def apply_sponsors(events):
    """Événements mis en avant par les organisateurs : l'app les affiche en tête avec l'étiquette « Sponsorisé »."""
    try:
        sponsors = json.loads(SPONSORS_FILE.read_text(encoding="utf-8"))["sponsors"]
    except FileNotFoundError:
        return
    ids = {s["id"] for s in sponsors if s.get("jusqu_au", "9999-12-31") >= TODAY.isoformat()}
    n = 0
    for e in events:
        if e["id"] in ids:
            e["sponsored"] = True
            n += 1
    print(f"Sponsorisés: {n} (sur {len(ids)} en cours dans sponsors.json)")


def main():
    events = []
    for name, fn in [("Spa-Francorchamps", spa), ("Manuel", manual), ("Pages web (IA)", pages), ("Dépôt", inbox), ("Propositions de l'app", submissions), ("Facebook (service tiers)", facebook), ("Ticketmaster", ticketmaster)]:
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
    try:
        old = {e["id"]: e for e in json.loads(OUT.read_text())["events"]
               if e.get("image") and not FB_FAKE_IMG.search(e["image"]) and "wikimedia.org" not in e["image"]}
        for e in uniq:                  # on ne reprend que les vraies affiches : les photos libres sont revérifiées
            prev = old.get(e["id"])
            if not e.get("image") and prev:
                e["image"] = prev["image"]
    except Exception:
        pass
    for e in uniq:
        if FB_FAKE_IMG.search(e.get("image") or ""):
            e["image"] = None
        elif re.search(r"\.fbcdn\.net/", e.get("image") or ""):
            _fb_rehost(e)           # adresse Facebook signée, qui expire : on en garde une copie, ou rien si elle a expiré
        # événement relevé par l'extension : si le service tiers a déjà copié son affiche, on la reprend
        for name in (f"fb-{e['id'][4:]}.jpg", f"{e['id']}.jpg") if e["id"].startswith("fbx-") and not e.get("image") else ():
            if (POSTERS / name).exists():
                e["image"] = RAW_BASE + name
                break
    fill_images(uniq, limit=400)                    # ~0,5 s par page (les pages Facebook sont sautées)
    for e in uniq:              # sinon : photo libre du lieu précis, ou rien (l'app montre la photo vérifiée de la catégorie)
        if not e.get("image"):
            got = venue_photo(e.get("venue"), e.get("city", ""))
            if got:
                e["image"], e["image_credit"] = got
    try:
        lieux = places()
        for p in lieux:          # photo du site officiel, sinon photo libre du lieu précis, sinon rien (l'app montre une icône)
            if p.get("website") and own_site(p["name"], p["website"]):
                try:
                    img = best_image(fetch(p["website"], timeout=15), p["website"])
                    p["image"] = img if img and is_image(img) else None
                except Exception:
                    pass
            if not p.get("image"):
                got = venue_photo(p["name"], p.get("city", ""))
                if got:
                    p["image"], p["image_credit"] = got
        (HERE / "places.json").write_text(json.dumps({"generated": datetime.now().isoformat(timespec="seconds"),
                                                      "places": lieux}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Lieux d'activités: {len(lieux)}")
    except Exception as ex:
        print(f"Lieux d'activités: ÉCHEC ({ex}), on garde la liste précédente", file=sys.stderr)
    (HERE / "rapport.json").write_text(json.dumps(
        {"genere": datetime.now().isoformat(timespec="seconds"), "sources": REPORT,
         "avec_image": sum(1 for e in uniq if e.get("image")), "total": len(uniq)},
        ensure_ascii=False, indent=2), encoding="utf-8")
    # fallback_images.json (photos de catégorie) est choisi et vérifié à l'œil : le robot n'y touche plus
    apply_sponsors(uniq)
    OUT.write_text(json.dumps({"generated": datetime.now().isoformat(timespec="seconds"), "events": uniq},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"écrit : {OUT.name}, {len(uniq)} événements")


if __name__ == "__main__":
    if "--ack" in sys.argv:
        acknowledge()
    else:
        main()
