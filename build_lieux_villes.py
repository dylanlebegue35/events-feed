"""Lieux permanents des grandes villes belges (« city trip ») pour l'onglet « Quand tu veux » de Vivyo.

Sources libres et gratuites, sans compte ni clé :
- Wikidata (CC0) : musées et incontournables, avec leur photo Wikimedia Commons (auteur et licence gardés) ;
- OpenStreetMap (ODbL, « © OpenStreetMap ») : activités (padel, escalade, bowling, karting, saunas, patinoires…),
  adresses et horaires ;
- les lieux choisis à la main (bars, brunchs, insolites) restent dans lieux_choisis.json.
Jamais Google Maps, Facebook ou Instagram : copier leurs données est interdit.

Règle de Dylan : un lieu sans vraie photo du lieu n'est pas affiché par l'app. On garde donc seulement
la photo du site officiel du lieu ou une photo Commons du lieu précis, vérifiée (c'est bien une image).

Écrit lieux_villes.json, que build_events.py (build_places) ajoute à places.json. À relancer de temps en temps
(une fois par mois suffit) : python3 build_lieux_villes.py
"""
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from build_events import best_image, own_site, is_image, OK_LICENSES, BAD_TITLE, _km  # noqa: E402

OUT = HERE / "lieux_villes.json"
CACHE_FILE = HERE / "lieux_villes_cache.json"
REVIEW_FILE = HERE / "lieux_villes_revue.json"
UA = "VivyoLieux/1.0 (+https://vivyo.app)"

# Grandes villes : centre et rayon « en ville » (km). Les activités (karting, padel…) vont jusqu'à ACT_KM.
CITIES = [
    ("Bruxelles", 50.8467, 4.3525, 7.5), ("Anvers", 51.2194, 4.4025, 6), ("Gand", 51.0543, 3.7174, 5.5),
    ("Bruges", 51.2093, 3.2247, 4.5), ("Liège", 50.6326, 5.5797, 6), ("Namur", 50.4674, 4.8718, 5),
    ("Charleroi", 50.4108, 4.4446, 6), ("Mons", 50.4542, 3.9567, 4.5), ("Louvain", 50.8798, 4.7005, 4.5),
    ("Louvain-la-Neuve", 50.6681, 4.6118, 3.5), ("Malines", 51.0259, 4.4776, 4.5), ("Hasselt", 50.9307, 5.3325, 4.5),
    ("Ostende", 51.2154, 2.9286, 4.5), ("Courtrai", 50.8279, 3.2649, 4.5), ("Tournai", 50.6056, 3.3878, 4.5),
]
ACT_KM = 12

# (clé OSM, valeur) -> (type, libellé). Mêmes types que build_events.places(), plus padel, saunas, patinoires, piscines.
OSM_KINDS = {
    ("tourism", "museum"): ("museum", "Musée"),
    ("leisure", "bowling_alley"): ("bowling", "Bowling"),
    ("leisure", "escape_game"): ("escape", "Escape game"),
    ("sport", "karting"): ("karting", "Karting"),
    ("sport", "laser_tag"): ("laser", "Laser game"),
    ("leisure", "trampoline_park"): ("trampoline", "Trampoline park"),
    ("sport", "climbing"): ("climbing", "Escalade"),
    ("sport", "padel"): ("padel", "Padel"),
    ("leisure", "sauna"): ("sauna", "Sauna & spa"),
    ("amenity", "public_bath"): ("sauna", "Sauna & spa"),
    ("leisure", "ice_rink"): ("icerink", "Patinoire"),
    ("sport", "billiards"): ("billiards", "Billard"),
    ("leisure", "amusement_arcade"): ("arcade", "Salle d'arcade"),
    ("leisure", "miniature_golf"): ("minigolf", "Mini-golf"),
    ("leisure", "water_park"): ("waterpark", "Parc aquatique"),
    ("tourism", "theme_park"): ("park", "Parc de loisirs"),
    ("tourism", "zoo"): ("zoo", "Parc animalier"),
    ("tourism", "aquarium"): ("zoo", "Parc animalier"),
}
IN_TOWN = {"museum"}            # ces types-là : seulement « en ville » ; les autres : jusqu'à ACT_KM
NEEDS_SITE = {"padel", "sauna", "zoo", "park", "billiards"}   # sans site web : souvent privé, fermé ou minuscule
SKIP_NAME = re.compile(r"casino|poker|paris sportifs|loto|privé|private|école|school|schule|lycée|athénée|institut|"
                       r"hôtel|hotel|fitness|basic.fit|kiné|kine|thermes? de|sex|massage|naturist|naturis|gay|men only", re.I)

_cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}
# une image vérifiée peut être remplacée ensuite par son site : on revérifie toutes les images à chaque passage
_cache = {k: v for k, v in _cache.items() if not k.startswith("ok|")}


def save_cache():
    CACHE_FILE.write_text(json.dumps(_cache, ensure_ascii=False, indent=0))


def curl_json(url, data=None, tries=3, timeout=180):
    for k in range(tries):
        cmd = ["curl", "-s", "--max-time", str(timeout), "-A", UA, "-H", "Accept: application/json"]
        cmd += ["--data-urlencode", "data=" + data, url] if data else [url]
        r = subprocess.run(cmd, capture_output=True)
        try:
            return json.loads(r.stdout)
        except Exception:
            time.sleep(10 * (k + 1))
    raise RuntimeError(f"pas de réponse lisible de {url[:60]}")


def nearest_city(lat, lon, max_km=None):
    best = min(CITIES, key=lambda c: _km([lat, lon], [c[1], c[2]]))
    d = _km([lat, lon], [best[1], best[2]])
    limit = best[3] if max_km is None else max_km
    return (best[0], d) if d <= limit else (None, d)


# ------------------------------------------------------------------ OpenStreetMap
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]


def osm_city(name, lat, lon, town_km):
    key = f"osm|{name}"
    if key in _cache and _cache[key].get("day") == datetime.now().strftime("%Y-%m-%d"):
        return _cache[key]["elements"]
    stmts = []
    for (k, v), (kind, _) in OSM_KINDS.items():
        r = town_km if kind in IN_TOWN else ACT_KM
        stmts.append(f'nwr(area.be)(around:{int(r * 1000)},{lat},{lon})["{k}"="{v}"];')
    q = '[out:json][timeout:170];area["ISO3166-1"="BE"][admin_level=2]->.be;(' + "".join(stmts) + ");out tags center;"
    last = None
    for url in OVERPASS:
        try:
            data = curl_json(url, q, tries=2)
            if "elements" in data:
                _cache[key] = {"day": datetime.now().strftime("%Y-%m-%d"), "elements": data["elements"]}
                save_cache()
                return data["elements"]
            last = data.get("remark")
        except Exception as ex:
            last = ex
        time.sleep(15)
    raise RuntimeError(f"OpenStreetMap indisponible pour {name} ({last})")


def osm_address(t):
    street = " ".join(x for x in (t.get("addr:street"), t.get("addr:housenumber")) if x)
    town = " ".join(x for x in (t.get("addr:postcode"), t.get("addr:city")) if x)
    return ", ".join(x for x in (street, town) if x) or None


def osm_places():
    out = {}
    for name, lat, lon, town_km in CITIES:
        try:
            els = osm_city(name, lat, lon, town_km)
        except Exception as ex:
            print(f"  ! {name} : {ex}", file=sys.stderr)
            continue
        n = 0
        for el in els:
            t = el.get("tags", {})
            nm = (t.get("name:fr") or t.get("name") or "").strip()
            plat = el.get("lat") or el.get("center", {}).get("lat")
            plon = el.get("lon") or el.get("center", {}).get("lon")
            if not nm or plat is None or SKIP_NAME.search(nm) or t.get("access") in ("private", "no", "members"):
                continue
            if t.get("lgbtq") in ("primary", "only") or t.get("male") == "only" or t.get("nudism") == "obligatory":
                continue             # saunas pour adultes (lieux de rencontre) : pas pour une app dès 16 ans
            kind = next((OSM_KINDS[(k, v)] for (k, v) in OSM_KINDS if t.get(k) == v), None)
            if not kind:
                continue
            if t.get("disused") or t.get("abandoned") or "disused:amenity" in t:
                continue
            if kind[0] == "climbing" and not (t.get("leisure") in ("sports_centre", "fitness_centre")
                                              or t.get("indoor") == "yes" or "building" in t):
                continue             # un rocher n'est pas une sortie en ville : seulement les salles
            if kind[0] == "sauna" and t.get("amenity") == "public_bath" and t.get("bath:type") not in (None, "thermal", "hammam", "onsen"):
                continue
            site = t.get("website") or t.get("contact:website") or t.get("url")
            if site and not site.startswith("http"):
                site = "https://" + site
            if kind[0] in NEEDS_SITE and not site:
                continue
            city, _ = nearest_city(plat, plon, max_km=ACT_KM + 1)
            pid = f"osm-{el['type']}-{el['id']}"
            out[pid] = {
                "id": pid, "name": nm, "kind": kind[0], "kind_label": kind[1],
                "city": city or t.get("addr:city") or name, "address": osm_address(t),
                "lat": plat, "lon": plon, "website": site, "hours": t.get("opening_hours"),
                "image": None, "wikidata": t.get("wikidata"), "osm_image": t.get("image") or t.get("wikimedia_commons"),
            }
            n += 1
        print(f"  {name} : {n} lieux OpenStreetMap")
        time.sleep(4)            # politesse envers les serveurs gratuits d'OpenStreetMap
    return out


# ------------------------------------------------------------------ Wikidata
def sparql(query):
    url = "https://query.wikidata.org/sparql?format=json&query=" + quote(query)
    return curl_json(url, timeout=120)["results"]["bindings"]


MUSEUMS_Q = """SELECT ?item ?itemLabel ?coord ?image ?site ?links WHERE {
  ?item wdt:P31/wdt:P279* wd:Q33506 ; wdt:P17 wd:Q31 ; wdt:P625 ?coord .
  FILTER NOT EXISTS { ?item wdt:P576 ?end } FILTER NOT EXISTS { ?item wdt:P3999 ?closed }
  OPTIONAL { ?item wdt:P18 ?image } OPTIONAL { ?item wdt:P856 ?site }
  ?item wikibase:sitelinks ?links .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "fr,nl,en". } }"""


NOT_VISIT = re.compile(r"^(ancienne )?maison (personnelle )?et atelier|^maison personnelle", re.I)


def wd_coord(s):
    m = re.match(r"Point\(([-\d.]+) ([-\d.]+)\)", s)
    return (float(m.group(2)), float(m.group(1))) if m else (None, None)


def wikidata_museums():
    rows = sparql(MUSEUMS_Q)
    items = {}
    for r in rows:
        qid = r["item"]["value"].rsplit("/", 1)[-1]
        lat, lon = wd_coord(r["coord"]["value"])
        if lat is None:
            continue
        label = r["itemLabel"]["value"]
        if re.fullmatch(r"Q\d+", label) or NOT_VISIT.search(label):
            continue                                  # pas de nom lisible, ou maison d'artiste privée (rangée « musée » par erreur)
        it = items.setdefault(qid, {"qid": qid, "name": label, "lat": lat, "lon": lon, "links": int(r["links"]["value"]),
                                    "images": [], "site": None})
        if "image" in r and r["image"]["value"] not in it["images"]:
            it["images"].append(r["image"]["value"])
        if "site" in r and not it["site"]:
            it["site"] = r["site"]["value"]
    return items


# Incontournables : les lieux les plus connus de chaque ville (nombre de langues de Wikipédia), seulement des types
# qu'on visite (place, cathédrale, beffroi, parc, château…). Les musées sont à part ; jamais une gare, une école,
# une institution, un événement ou une commune.
SIGHTS_Q = """SELECT ?item ?itemLabel ?coord ?links (SAMPLE(?img) AS ?image) (SAMPLE(?s) AS ?site)
  (GROUP_CONCAT(DISTINCT ?cl; separator="|") AS ?classes) WHERE {
  SERVICE wikibase:around { ?item wdt:P625 ?coord . bd:serviceParam wikibase:center "Point(%LON% %LAT%)"^^geo:wktLiteral .
                            bd:serviceParam wikibase:radius "%R%" . }
  ?item wdt:P18 ?img ; wikibase:sitelinks ?links . FILTER(?links >= %MIN%)
  FILTER NOT EXISTS { ?item wdt:P576 ?end }
  OPTIONAL { ?item wdt:P856 ?s }
  ?item wdt:P31 ?c . ?c rdfs:label ?cl FILTER(LANG(?cl)="en")
  SERVICE wikibase:label { bd:serviceParam wikibase:language "fr,nl,en". }
} GROUP BY ?item ?itemLabel ?coord ?links ORDER BY DESC(?links) LIMIT 250"""
SIGHT_OK = re.compile(r"grand place|town square|square|plaza|market square|cathedral|basilica|abbey|beguinage|béguinage|"
                      r"belfry|palace|château|castle|citadel|fort\b|fortress|town hall|city hall|urban park|\bpark\b|"
                      r"botanical garden|garden|zoo|aquarium|amusement park|theme park|opera house|theatre|concert hall|"
                      r"world heritage site|shopping arcade|arcade|covered passage|observation tower|tower|monument|"
                      r"memorial|landmark|tourist attraction|statue|sculpture|fountain|lake|viewpoint|harbour|"
                      r"beach|promenade|lighthouse|windmill|mill|atomium|exhibition hall|historic site|art nouveau|"
                      r"townhouse|manor house|mansion|gate|city gate|bridge|cemetery|church building", re.I)
SIGHT_NO = re.compile(r"municipality|city\b|big city|village|region|province|arrondissement|district|neighbo|"
                      r"university|school|college|academy|conservatory|institute|hospital|railway|station|metro|tram|"
                      r"government|parliament|senate|ministry|embassy|court of|headquarters|office building|"
                      r"organization|company|business|bank|broadcast|newspaper|library|archive|event|attack|treaty|"
                      r"war|battle|conference|festival|team|club|stadium|arena|prison|barracks|military|skyscraper|"
                      r"museum|gallery|hotel|restaurant|brewery|shop\b|street|road|motorway|stream|river\b|"
                      r"mosque|synagogue|temple|cemetery|administration|tower block|office", re.I)


def wikidata_sights():
    out = {}
    for name, lat, lon, town_km in CITIES:
        minlinks = 8 if name in ("Bruxelles", "Anvers", "Gand", "Bruges", "Liège") else 5
        q = SIGHTS_Q.replace("%LON%", str(lon)).replace("%LAT%", str(lat)).replace("%R%", str(town_km)).replace("%MIN%", str(minlinks))
        try:
            rows = sparql(q)
        except Exception as ex:
            print(f"  ! incontournables {name} : {ex}", file=sys.stderr)
            continue
        n = 0
        for r in rows:
            label, classes = r["itemLabel"]["value"], r["classes"]["value"]
            if re.fullmatch(r"Q\d+", label) or not SIGHT_OK.search(classes) or SIGHT_NO.search(classes):
                continue
            if re.search(r"church building", classes, re.I) and not re.search(r"cathedral|basilica|abbey|heritage", classes, re.I) \
                    and int(r["links"]["value"]) < 30:
                continue          # une église ordinaire n'est pas un incontournable
            plat, plon = wd_coord(r["coord"]["value"])
            if plat is None:
                continue
            qid = r["item"]["value"].rsplit("/", 1)[-1]
            out["wd-" + qid] = {"id": "wd-" + qid, "name": label[0].upper() + label[1:], "kind": "sight",
                                "kind_label": "Incontournable", "city": name, "address": None, "lat": plat, "lon": plon,
                                "website": r.get("site", {}).get("value"), "hours": None, "image": None,
                                "_images": [r["image"]["value"]], "_links": int(r["links"]["value"])}
            n += 1
            if n >= 15:
                break
        print(f"  {name} : {n} incontournables")
        time.sleep(2)
    return out


def commons_meta(file_urls):
    """Pour des fichiers Commons (adresse Special:FilePath) : miniature 1000 px, auteur et licence. Gardé en cache."""
    todo = [u for u in file_urls if "cm|" + u not in _cache]
    for i in range(0, len(todo), 40):
        batch = todo[i:i + 40]
        titles = "|".join("File:" + unquote(u.rsplit("/", 1)[-1]).replace("_", " ") for u in batch)
        url = ("https://commons.wikimedia.org/w/api.php?action=query&format=json&prop=imageinfo"
               "&iiprop=url|size|extmetadata&iiurlwidth=1000&titles=" + quote(titles))
        try:
            data = curl_json(url, timeout=60)
        except Exception as ex:
            print(f"  ! Commons : {ex}", file=sys.stderr)
            continue
        norm = {n["to"]: n["from"] for n in data.get("query", {}).get("normalized", [])}
        by_title = {}
        for p in data.get("query", {}).get("pages", {}).values():
            ii = (p.get("imageinfo") or [{}])[0]
            meta = ii.get("extmetadata", {})
            lic = meta.get("LicenseShortName", {}).get("value", "")
            artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip() or "auteur inconnu"
            ok = (bool(ii.get("thumburl")) and bool(OK_LICENSES.match(lic)) and not BAD_TITLE.search(p.get("title", ""))
                  and ii.get("width", 0) >= 800 and ii.get("height", 0) >= 450)
            by_title[norm.get(p.get("title"), p.get("title"))] = (
                {"url": ii["thumburl"], "credit": f"{artist[:60]} / Wikimedia Commons, {lic}"} if ok else None)
        for u in batch:
            t = "File:" + unquote(u.rsplit("/", 1)[-1]).replace("_", " ")
            _cache["cm|" + u] = by_title.get(t)
        save_cache()
        time.sleep(1)
    return {u: _cache.get("cm|" + u) for u in file_urls}


# ------------------------------------------------------------------ photos des sites officiels
LOCAL_HOST = re.compile(r"^https?://(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.|0\.|\[|[^/]*\.local\b)", re.I)


def site_photo(name, site):
    key = "site|" + site
    if key in _cache:
        return _cache[key]
    img = None
    if not LOCAL_HOST.match(site) and own_site(name, site):
        r = subprocess.run(["curl", "-sL", "--max-time", "15", "--max-redirs", "3", "--proto", "=http,https", "--proto-redir",
                            "=http,https", "--max-filesize", "3000000", "-A", "Mozilla/5.0 (Vivyo)", "--", site], capture_output=True)
        page = r.stdout.decode("utf-8", "ignore")
        try:
            img = best_image(page, site) if page else None
        except Exception:
            img = None
        if img and not is_image(img):
            img = None
    _cache[key] = img
    return img


def checked(url):
    if re.match(r"https://(upload|thumb)\.wikimedia\.org/", url):
        return True               # miniature donnée par l'API de Commons : pas besoin de la retélécharger (et Commons freine)
    key = "ok|" + url
    if key not in _cache:
        _cache[key] = is_image(url)
    return _cache[key]


# ------------------------------------------------------------------ assemblage
def main():
    started = time.time()
    print("Musées (Wikidata)…")
    museums = wikidata_museums()
    print(f"  {len(museums)} musées en Belgique")
    print("Incontournables (Wikidata)…")
    sights = wikidata_sights()
    print("OpenStreetMap, ville par ville…")
    osm = osm_places()

    lieux = dict(sights)
    # 1) musées Wikidata situés « en ville »
    for qid, m in museums.items():
        city, _ = nearest_city(m["lat"], m["lon"])
        if city:
            lieux["wd-" + qid] = {"id": "wd-" + qid, "name": m["name"], "kind": "museum", "kind_label": "Musée", "city": city,
                                  "address": None, "lat": m["lat"], "lon": m["lon"], "website": m["site"], "hours": None,
                                  "image": None, "_images": m["images"], "_links": m["links"]}
    # 2) OpenStreetMap : un musée qui a son Wikidata complète la fiche (adresse, horaires, site) ; le reste s'ajoute
    for pid, p in osm.items():
        q = p.pop("wikidata", None)
        if q and "wd-" + q in lieux:
            w = lieux["wd-" + q]
            w["address"] = w["address"] or p["address"]
            w["hours"] = w["hours"] or p["hours"]
            w["website"] = w["website"] or p["website"]
            continue
        if q and q in museums and museums[q]["images"]:
            p["_images"] = museums[q]["images"]
        dup = next((x for x in lieux.values() if x["kind"] == p["kind"] and _km([x["lat"], x["lon"]], [p["lat"], p["lon"]]) < 0.15), None)
        if dup:
            dup["address"] = dup["address"] or p["address"]
            dup["hours"] = dup["hours"] or p["hours"]
            continue
        lieux[pid] = p

    # revue à l'œil (lieux_villes_revue.json) : lieux exclus, et images de site refusées (logo, texte, affiche…)
    revue = json.loads(REVIEW_FILE.read_text()) if REVIEW_FILE.exists() else {}
    for k in revue.get("exclus", []):
        lieux.pop(k, None)
    refused = set(revue.get("photo_refusee", []))

    # 3) photos : Commons (photo du lieu dans Wikidata), sinon tag image d'OSM, sinon site officiel
    files = [u for x in lieux.values() for u in x.get("_images", [])[:2]]
    meta = commons_meta(files)
    print("Photos des sites officiels…")
    n_site = 0
    for x in lieux.values():
        if x["id"] in refused:
            x.pop("osm_image", None)
            continue
        for u in x.get("_images", [])[:2]:
            m = meta.get(u)
            if m and checked(m["url"]):
                x["image"], x["image_credit"] = m["url"], m["credit"]
                break
        x.pop("osm_image", None)     # le tag « image » d'OSM n'est pas utilisé : n'importe qui peut y mettre n'importe quoi
        if not x["image"] and x.get("website"):
            img = site_photo(x["name"], x["website"])
            n_site += 1
            if n_site % 50 == 0:
                save_cache()
                print(f"  {n_site} sites lus")
            if img:
                x["image"] = img
    save_cache()

    # 4) ordre : par ville, les musées les plus connus d'abord (nombre de langues de Wikipédia)
    out = []
    for x in lieux.values():
        # un site glissé dans OpenStreetMap par n'importe qui : gardé seulement si son adresse ressemble au nom du lieu
        if x["id"].startswith("osm-") and x.get("website") and not own_site(x["name"], x["website"]):
            x["website"] = None
        # saunas : seulement ceux relus à l'œil (lieux_villes_revue.json, « saunas_ok ») ; un nouveau attend la revue
        if x["kind"] == "sauna" and x["id"] not in revue.get("saunas_ok", []):
            continue
        # données venues d'ailleurs : seulement des liens web normaux (pas javascript:, tel:, une autre app…)
        if x.get("website") and not re.match(r"https?://[^\s\"<>]+$", x["website"]):
            x["website"] = None
        if x.get("image") and not re.match(r"https://[^\s\"<>]+$", x["image"]):
            x["image"] = None
        for f in ("name", "address", "hours"):
            if isinstance(x.get(f), str):
                x[f] = re.sub(r"[\x00-\x1f\x7f]", " ", x[f]).strip()[:200] or None
        if not x.get("name"):
            continue
        x.pop("_images", None)
        links = x.pop("_links", 0)
        x.update({"pitch": None, "offer": None, "partner": False, "instagram": None})
        x["_rank"] = -links
        out.append(x)
    order = {c[0]: i for i, c in enumerate(CITIES)}
    out.sort(key=lambda x: (order.get(x["city"], 99), x["kind"], x["_rank"], x["name"]))
    for x in out:
        x.pop("_rank")
    OUT.write_text(json.dumps({
        "generated": datetime.now().isoformat(timespec="seconds"),
        "_aide": "Généré par build_lieux_villes.py (Wikidata + OpenStreetMap). Ne pas modifier à la main : "
                 "les lieux choisis à la main vont dans lieux_choisis.json.",
        "lieux": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    with_img = sum(1 for x in out if x.get("image"))
    print(f"{len(out)} lieux, dont {with_img} avec une vraie photo, en {int(time.time() - started)} s → {OUT.name}")


if __name__ == "__main__":
    main()
