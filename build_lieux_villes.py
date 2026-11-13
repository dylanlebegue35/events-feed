"""Lieux permanents des grandes villes belges (« city trip ») pour l'onglet « Quand tu veux » de Vivyo.

Sources libres et gratuites, sans compte ni clé :
- Wikidata (CC0) : musées et incontournables, avec leur photo Wikimedia Commons (auteur et licence gardés) ;
- OpenStreetMap (ODbL, « © OpenStreetMap ») : activités (padel, escalade, bowling, karting, saunas, patinoires…),
  clubs, cinémas, salles de concert, théâtres,
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
import unicodedata
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
    # villes moyennes et touristiques (un guide n'apparaît dans l'app qu'à partir de 8 lieux avec photo)
    ("Genk", 50.9650, 5.5000, 4.5), ("Alost", 50.9378, 4.0403, 4), ("Saint-Nicolas", 51.1650, 4.1430, 4),
    ("Verviers", 50.5891, 5.8623, 4), ("Arlon", 49.6833, 5.8167, 3.5), ("Wavre", 50.7170, 4.6010, 3.5),
    ("Nivelles", 50.5980, 4.3290, 3), ("Turnhout", 51.3227, 4.9447, 4), ("Roulers", 50.9469, 3.1227, 4),
    ("La Louvière", 50.4800, 4.1870, 4), ("Ypres", 50.8503, 2.8850, 3.5), ("Knokke", 51.3495, 3.2870, 4),
    ("Spa", 50.4920, 5.8640, 3), ("Dinant", 50.2606, 4.9122, 3), ("Durbuy", 50.3530, 5.4560, 2.5),
    ("Waterloo", 50.7150, 4.3990, 3),
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
    ("amenity", "nightclub"): ("club", "Club"),
    ("amenity", "cinema"): ("cinema", "Cinéma"),
    ("amenity", "music_venue"): ("concert", "Salle de concert"),
    ("amenity", "theatre"): ("theatre", "Théâtre"),
    ("amenity", "karaoke_box"): ("karaoke", "Karaoké"),
}
# tous les bars (OpenStreetMap) seulement dans ces villes, jusqu'à ce rayon (km) : ailleurs, les bars sont choisis à la main
BAR_CITIES = {"Liège": 6.5}
BAR_KINDS = {("amenity", "bar"): ("bar", "Bar"), ("amenity", "pub"): ("bar", "Bar"), ("amenity", "biergarten"): ("bar", "Bar")}
IN_TOWN = {"museum", "theatre"}  # ces types-là : seulement « en ville » ; les autres : jusqu'à ACT_KM
# sans site web : souvent privé, fermé ou minuscule
NEEDS_SITE = {"padel", "sauna", "zoo", "park", "billiards", "club", "cinema", "concert", "theatre", "karaoke"}
# s'applique au texte « à plat » (sans accents ni caractères invisibles, voir _flat) : « Str<invisible>ip » ne passe pas
SKIP_NAME = re.compile(r"casino|poker|paris sportifs|loto|prive|private|ecole|school|schule|lycee|athenee|institut|"
                       r"\bhotel\b(?! de ville)|fitness|basic.fit|kinesith|\bkine\b|thermes? de|(?<![a-z])sex|massage|"
                       r"strip|gentlem[ae]n|go-?go|peep|swing|libertin|echangis|erot|lap ?danc|cabaret priv|porn|xxx|"
                       r"bdsm|fetish|escort|bordel|bordeel|playboy|adult|hostess|\bseks|naakt|pole ?danc|table ?danc|red ?light|darkroom|"
                       r"cruising|lingerie|cam ?girl|naturis|men only|\bnazi|hitler|fuck", re.I)
# tags OSM qui demandent une relecture à la main, quel que soit le type retenu (un club qui se dit aussi « bowling »…)
REVIEW_TAGS = {("amenity", "nightclub"), ("amenity", "karaoke_box"), ("leisure", "sauna"), ("amenity", "public_bath"),
               ("leisure", "adult_gaming_centre"), ("amenity", "hookah_lounge")}
NEVER_TAGS = {("amenity", "stripclub"), ("amenity", "swingerclub"), ("amenity", "brothel"), ("shop", "erotic"),
              ("amenity", "love_hotel"), ("amenity", "casino"), ("amenity", "gambling")}
# types où l'alcool et la nuit dominent : affichés seulement une fois relus à la main (lieux_villes_revue.json, « clubs_ok »)
NEEDS_REVIEW = {"club", "karaoke"}
MAX_PER_KIND = 250                  # lieux d'un même type dans une même ville, au plus
SAUNA_SKIP = re.compile(r"gay|for men|hommes", re.I)
SCRIPTS = re.compile(r"[\u0370-\u03ff\u0400-\u04ff]")    # grec, cyrillique : « Ѕtrip » avec un S cyrillique   # saunas de rencontre (seulement pour les saunas)


def _flat(text):
    """Texte comparable : compatibilité Unicode (ｓｅｘ → sex), sans accents ni caractères invisibles, en minuscules."""
    t = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in t if unicodedata.category(c) not in ("Mn", "Cf", "Cc")).casefold()

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
    if name in BAR_CITIES:
        for (k, v) in BAR_KINDS:
            stmts.append(f'nwr(area.be)(around:{int(BAR_CITIES[name] * 1000)},{lat},{lon})["{k}"="{v}"];')
    q = '[out:json][timeout:170];area["ISO3166-1"="BE"][admin_level=2]->.be;(' + "".join(stmts) + ");out tags center;"
    last = None
    for url in OVERPASS:
        try:
            data = curl_json(url, q, tries=2)
            if "elements" in data and (data["elements"] or not data.get("remark")):   # vide + remarque = délai dépassé
                _cache[key] = {"day": datetime.now().strftime("%Y-%m-%d"), "elements": data["elements"]}
                save_cache()
                return data["elements"]
            last = data.get("remark")
        except Exception as ex:
            last = ex
        time.sleep(15)
    raise RuntimeError(f"OpenStreetMap indisponible pour {name} ({last})")


_FILLER = re.compile(r"\b(le|la|les|l|the|de|du|des|het|de|cafe|bar|pub|brasserie|taverne)\b")


def _same_place(a, b, km):
    """Même lieu : au même point (moins de 30 m), ou un nom qui se recoupe à moins de 300 m (« Le Cabinet » / « Cabinet »)."""
    if km < 0.01:
        return True
    if km >= 0.3:
        return False
    x, y = (re.sub(r"\s+", " ", _FILLER.sub(" ", re.sub(r"[^a-z0-9 ]", " ", _flat(n)))).strip() for n in (a, b))
    short, long_ = sorted((x, y), key=len)
    return bool(short) and (x == y or (len(short) >= 5 and len(short) >= 0.5 * len(long_) and short in long_))


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
            # les noms seulement : une description (« pour enfants et adultes ») ferait sauter Walibi
            texts = " | ".join(str(t.get(f) or "") for f in ("name", "name:fr", "name:nl", "name:en", "alt_name", "brand"))
            if not nm or plat is None or SKIP_NAME.search(_flat(texts)) or t.get("access") in ("private", "no", "members"):
                continue
            if SCRIPTS.search(texts) or any(t.get(k) == v for (k, v) in NEVER_TAGS):
                continue             # alphabet mélangé (lettres déguisées) ou lieu pour adultes
            kind = next((OSM_KINDS[(k, v)] for (k, v) in OSM_KINDS if t.get(k) == v), None)
            if not kind and name in BAR_CITIES:
                kind = next((BAR_KINDS[(k, v)] for (k, v) in BAR_KINDS if t.get(k) == v), None)
            if not kind:
                continue
            if kind[0] == "sauna" and (t.get("lgbtq") in ("primary", "only") or t.get("male") == "only"
                                       or t.get("nudism") == "obligatory" or SAUNA_SKIP.search(_flat(texts))):
                continue             # saunas pour adultes (lieux de rencontre) : pas pour une app dès 16 ans
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
            review = kind[0] not in NEEDS_REVIEW and kind[0] != "sauna" and any(t.get(k) == v for (k, v) in REVIEW_TAGS)
            out[pid] = {"_review": review,
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


MUSEUMS_Q = """SELECT ?item ?itemLabel ?coord ?image ?site ?links ?admLabel WHERE {
  ?item wdt:P31/wdt:P279* wd:Q33506 ; wdt:P17 wd:Q31 ; wdt:P625 ?coord .
  FILTER NOT EXISTS { ?item wdt:P576 ?end } FILTER NOT EXISTS { ?item wdt:P3999 ?closed }
  OPTIONAL { ?item wdt:P18 ?image } OPTIONAL { ?item wdt:P856 ?site } OPTIONAL { ?item wdt:P131 ?adm }
  ?item wikibase:sitelinks ?links .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "fr,nl,en". } }"""


NOT_VISIT = re.compile(r"^(ancienne )?maison (personnelle )?et atelier|^maison personnelle", re.I)


def wd_coord(s):
    m = re.match(r"Point\(([-\d.]+) ([-\d.]+)\)", s)
    return (float(m.group(2)), float(m.group(1))) if m else (None, None)


def wikidata_photos(qids):
    """Photo Commons (P18) des lieux OpenStreetMap reliés à Wikidata (salles de concert, cinémas, théâtres…)."""
    qids = sorted({q for q in qids if isinstance(q, str) and re.fullmatch(r"Q[0-9]{1,12}", q)})
    out = {}
    for i in range(0, len(qids), 150):
        vals = " ".join("wd:" + q for q in qids[i:i + 150])
        try:
            # ordre fixe : la même photo à chaque passage (sinon un 2e P18 ajouté par n'importe qui pourrait passer devant)
            rows = sparql("SELECT ?item ?image WHERE { VALUES ?item { " + vals + " } ?item wdt:P18 ?image . } ORDER BY ?item ?image")
        except Exception as ex:
            print(f"  ! Wikidata (photos) : {ex}", file=sys.stderr)
            continue
        for r in rows:
            out.setdefault(r["item"]["value"].rsplit("/", 1)[-1], []).append(r["image"]["value"])
    return out


def wikidata_museums():
    rows = sparql(MUSEUMS_Q)
    items = {}
    for r in rows:
        qid = r["item"]["value"].rsplit("/", 1)[-1]
        lat, lon = wd_coord(r["coord"]["value"])
        if lat is None:
            continue
        label = r["itemLabel"]["value"]
        if re.fullmatch(r"Q[0-9]+", label) or NOT_VISIT.search(label):
            continue                                  # pas de nom lisible, ou maison d'artiste privée (rangée « musée » par erreur)
        it = items.setdefault(qid, {"qid": qid, "name": label, "lat": lat, "lon": lon, "links": int(r["links"]["value"]),
                                    "images": [], "site": None, "town": None})
        town = r.get("admLabel", {}).get("value", "")
        if town and not it["town"] and not re.fullmatch(r"Q[0-9]+", town):
            it["town"] = re.sub(r"[\x00-\x1f\x7f]", " ", town).strip()[:60]
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
                      r"mosque|synagogue|temple|cemetery|administration|tower block|office|water tower|residential|science park|"
                     r"exhibition|transmitter|business park|industrial|factory", re.I)


# Brasseries qu'on peut visiter (visite guidée, salle de dégustation ou taverne sur place), choisies à la main :
# Wikidata ne dit pas si une brasserie reçoit le public. Valeur = nom affiché (None : celui de Wikidata).
BREWERIES = {
    "Q2345368": "Brasserie Cantillon",          # Bruxelles, musée bruxellois de la gueuze
    "Q97275006": "Brussels Beer Project",       # bar de la rue Dansaert
    "Q2754830": "Brasserie Timmermans",
    "Q1227102": "Oud Beersel",
    "Q2684279": "De Koninck, Antwerp City Brewery",
    "Q1179933": "Brouwerij De Halve Maan",
    "Q101422066": "Bourgogne des Flandres",
    "Q2473159": "Brouwerij Het Anker",
    "Q123133196": "Brasserie C",
    "Q105635811": "Kasteel Brouwerij Vanhonsebrouck",
    "Q2320696": "Brouwerij De Brabandere",
    "Q2164364": "Brouwerij Omer Vander Ghinste",
    "Q14622653": "Brasserie St-Feuillien",
    "Q3095874": "Brouwerij Hof ten Dormaal",
    "Q60849584": "Moulin-brasserie de l'abbaye de Floreffe",
}
BREW_KM = 15


def wikidata_breweries():
    q = ("SELECT ?item ?coord ?image ?site ?links WHERE { VALUES ?item { %s } ?item wdt:P625 ?coord ; wikibase:sitelinks ?links . "
         "OPTIONAL { ?item wdt:P18 ?image } OPTIONAL { ?item wdt:P856 ?site } }") % " ".join("wd:" + k for k in BREWERIES)
    out = {}
    for r in sparql(q):
        qid = r["item"]["value"].rsplit("/", 1)[-1]
        lat, lon = wd_coord(r["coord"]["value"])
        city = nearest_city(lat, lon, BREW_KM)[0] if lat is not None else None
        if not city:
            continue
        x = out.setdefault("wd-" + qid, {"id": "wd-" + qid, "name": BREWERIES[qid], "kind": "brewery",
                                         "kind_label": "Brasserie à visiter", "city": city, "address": None, "lat": lat,
                                         "lon": lon, "website": None, "hours": None, "image": None, "_images": [],
                                         "_links": int(r["links"]["value"])})
        if "image" in r and r["image"]["value"] not in x["_images"]:
            x["_images"].append(r["image"]["value"])
        if "site" in r and not x["website"]:
            x["website"] = r["site"]["value"].replace("http://", "https://", 1)
    print(f"  {len(out)} brasseries à visiter")
    return out


def wikidata_sights():
    out = {}
    for name, lat, lon, town_km in CITIES:
        minlinks = 8 if name in ("Bruxelles", "Anvers", "Gand", "Bruges", "Liège") else 3    # villes moyennes : moins connues à l'étranger
        q = SIGHTS_Q.replace("%LON%", str(lon)).replace("%LAT%", str(lat)).replace("%R%", str(town_km)).replace("%MIN%", str(minlinks))
        try:
            rows = sparql(q)
        except Exception as ex:
            print(f"  ! incontournables {name} : {ex}", file=sys.stderr)
            continue
        n = 0
        for r in rows:
            label, classes = r["itemLabel"]["value"], r["classes"]["value"]
            if re.fullmatch(r"Q[0-9]+", label) or not SIGHT_OK.search(classes) or SIGHT_NO.search(classes):
                continue
            if re.search(r"church building", classes, re.I) and not re.search(r"cathedral|basilica|abbey|heritage", classes, re.I) \
                    and int(r["links"]["value"]) < 30:
                continue          # une église ordinaire n'est pas un incontournable
            plat, plon = wd_coord(r["coord"]["value"])
            if plat is None:
                continue
            qid = r["item"]["value"].rsplit("/", 1)[-1]
            if any(x["city"] == name and x["name"].lower() == label.lower() and _km([x["lat"], x["lon"]], [plat, plon]) < 0.3
                   for x in out.values()):
                continue          # même lieu deux fois (deux éléments Wikidata pour la même porte)
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
    print("Brasseries à visiter (Wikidata)…")
    breweries = wikidata_breweries()
    print("OpenStreetMap, ville par ville…")
    osm = osm_places()

    lieux = dict(sights)
    lieux.update(breweries)
    # 1) musées Wikidata : ceux « en ville » vont dans le guide de la ville, les autres gardent leur commune
    #    (l'app les montre « autour de moi » ; une commune devient un guide à partir de 8 lieux avec photo)
    for qid, m in museums.items():
        city, _ = nearest_city(m["lat"], m["lon"])
        city = city or m.get("town")
        if city:
            lieux["wd-" + qid] = {"id": "wd-" + qid, "name": m["name"], "kind": "museum", "kind_label": "Musée", "city": city,
                                  "address": None, "lat": m["lat"], "lon": m["lon"], "website": m["site"], "hours": None,
                                  "image": None, "_images": m["images"], "_links": m["links"]}
    try:
        chosen = json.loads((HERE / "lieux_choisis.json").read_text())["lieux"]
    except Exception:
        chosen = []
    # 2) OpenStreetMap : un musée qui a son Wikidata complète la fiche (adresse, horaires, site) ; le reste s'ajoute
    wd_imgs = wikidata_photos(p.get("wikidata") for p in osm.values() if p.get("wikidata") not in museums)
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
        elif q and wd_imgs.get(q):
            p["_images"] = wd_imgs[q]
        if p["kind"] == "bar" and any(c.get("kind") == "bar" and c.get("lat") is not None and c.get("lon") is not None
                                      and _same_place(c.get("name"), p["name"], _km([c["lat"], c["lon"]], [p["lat"], p["lon"]]))
                                      for c in chosen):
            continue             # déjà choisi à la main (lieux_choisis.json), avec sa vraie photo et son texte
        dup = next((x for x in lieux.values() if x["kind"] == p["kind"] and (
            _same_place(x["name"], p["name"], _km([x["lat"], x["lon"]], [p["lat"], p["lon"]])) if p["kind"] == "bar"
            else _km([x["lat"], x["lon"]], [p["lat"], p["lon"]]) < 0.15)), None)
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
    # meilleure photo Commons choisie à l'œil (celle de Wikidata est un logo, une bouteille, un portrait…)
    for k, f in revue.get("photo_choisie", {}).items():
        if k in lieux and isinstance(f, str) and f.startswith("http://commons.wikimedia.org/wiki/Special:FilePath/"):
            lieux[k]["_images"] = [f] + [u for u in lieux[k].get("_images", []) if u != f]

    # photo du site officiel choisie à l'œil sur une planche (celle que le robot prendrait seul est un logo, une affiche…).
    # Elle remplace tout le reste ; si elle disparaît du site, le lieu n'a plus de photo (jamais un retour au choix automatique)
    site_choice = {k: u for k, u in revue.get("photo_site", {}).items()
                   if isinstance(k, str) and isinstance(u, str) and re.match(r"https://[^\s\"<>]+$", u) and not LOCAL_HOST.match(u)}

    # 3) photos : Commons (photo du lieu dans Wikidata), sinon tag image d'OSM, sinon site officiel
    files = [u for x in lieux.values() for u in x.get("_images", [])[:2]]
    meta = commons_meta(files)
    print("Photos des sites officiels…")
    n_site = 0
    for x in lieux.values():
        if x["id"] in site_choice:
            x.pop("osm_image", None)
            x["image"] = site_choice[x["id"]] if checked(site_choice[x["id"]]) else None
            x["image_credit"] = None
            continue
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
    out, pending, n_kind = [], [], {}
    for x in lieux.values():
        # un site glissé dans OpenStreetMap ou Wikidata par n'importe qui : gardé seulement si son domaine ressemble au nom
        # du lieu ou a été relu à la main (lieux_villes_revue.json, « sites_ok »)
        if x["id"].startswith(("osm-", "wd-")) and x.get("website") and not own_site(x["name"], x["website"]):
            x["website"] = None
        # saunas : seulement ceux relus à l'œil (lieux_villes_revue.json, « saunas_ok ») ; un nouveau attend la revue
        if x["kind"] == "sauna" and x["id"] not in revue.get("saunas_ok", []):
            continue
        # clubs et karaokés (ou un lieu qui en porte le tag) : seulement ceux relus à la main (« clubs_ok »)
        if (x["kind"] in NEEDS_REVIEW or x.pop("_review", False)) and x["id"] not in revue.get("clubs_ok", []):
            pending.append(x)
            continue
        x.pop("_review", None)
        if not (49.49 <= x["lat"] <= 51.51 and 2.54 <= x["lon"] <= 6.41):
            continue             # hors de Belgique (coordonnées fausses dans Wikidata ou OpenStreetMap)
        n_kind[(x["city"], x["kind"])] = n_kind.get((x["city"], x["kind"]), 0) + 1
        if n_kind[(x["city"], x["kind"])] > MAX_PER_KIND:
            continue             # plafond : des centaines de faux lieux ne doivent pas gonfler le fichier de l'app
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
    # relecture : tout ce qui est nouveau ou a changé (nom, ville, site, photo) depuis la version enregistrée dans git
    sig = lambda x: (x["id"], x.get("name"), x.get("city"), x.get("website"), x.get("image"))
    try:
        committed = subprocess.run(["git", "-C", str(HERE), "show", "HEAD:lieux_villes.json"], capture_output=True, text=True).stdout
        before = {sig(x) for x in json.loads(committed)["lieux"]}
    except Exception:
        before = set()
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
    # Wikidata et OpenStreetMap sont modifiables par n'importe qui : chaque nouveau lieu avec photo se relit à l'œil
    # (planche) avant d'être poussé ; les refus vont dans lieux_villes_revue.json
    fresh = [x for x in out if sig(x) not in before]
    print(f"{sum(1 for x in fresh if x.get('image'))} lieux avec photo à relire (nouveaux ou changés depuis git) :")
    for x in fresh:
        if x.get("image"):
            print(f"  {x['city']} · {x['kind']} · {x['name']} · {x['image']}")
    print(f"{sum(1 for x in fresh if not x.get('image'))} lieux sans photo à relire (nom, ville, site) :")
    for x in fresh:
        if not x.get("image"):
            print(f"  {x['id']} · {x['city']} · {x['kind']} · {x['name']} · {x.get('website')}")
    print(f"{len(pending)} clubs ou karaokés en attente de relecture (« clubs_ok ») :")
    for x in pending:
        print(f"  {x['id']} · {x['city']} · {x['kind']} · {x['name']} · {x.get('website')}")
    print(f"{len(out)} lieux, dont {with_img} avec une vraie photo, en {int(time.time() - started)} s → {OUT.name}")


if __name__ == "__main__":
    main()
