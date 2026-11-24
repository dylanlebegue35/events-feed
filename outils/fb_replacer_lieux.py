"""Replace les événements Facebook du 24/11 tombés au centre de leur ville de recherche : on cherche le lieu tel quel en
Belgique (Nominatim, 1 requête par seconde) ; s'il est à moins de 50 km, on prend son point exact et, s'il est à plus de
10 km du centre, sa vraie commune. Sinon on laisse le centre-ville."""
import json, math, re, subprocess, sys, time
from pathlib import Path
from urllib.parse import quote

ICI = Path(__file__).parent
GO = ICI.parent / "go"
sys.path.insert(0, str(GO))
import build_events as b  # noqa: E402

CACHE = ICI / "regeo-cache.json"
cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}


def km(a, c):
    dl, dg = math.radians(c[0] - a[0]), math.radians(c[1] - a[1])
    x = math.sin(dl / 2) ** 2 + math.cos(math.radians(a[0])) * math.cos(math.radians(c[0])) * math.sin(dg / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(x))


def cherche(q):
    if q in cache:
        return cache[q]
    time.sleep(1.1)
    r = subprocess.run(["curl", "-s", "--max-time", "20", "-A", "StudioAppsBot/1.0 (events-feed)",
                        f"https://nominatim.openstreetmap.org/search?format=json&limit=1&addressdetails=1&countrycodes=be&q={quote(q)}"],
                       capture_output=True)
    try:
        h = json.loads(r.stdout)[0]
        a = h.get("address", {})
        res = [float(h["lat"]), float(h["lon"]), a.get("city") or a.get("town") or a.get("village") or a.get("municipality")]
    except Exception:
        res = None
    cache[q] = res
    CACHE.write_text(json.dumps(cache, ensure_ascii=False))
    return res


f = GO / "manual_events.json"
data = json.loads(f.read_text())
nouveaux = [e for e in data["events"] if str(e.get("summary", "")).endswith("page Facebook de l'événement.")]
precis = deplaces = rate = 0
for i, e in enumerate(nouveaux, 1):
    centre = b.geocode(None, e["city"])
    if not centre or km(centre, (e["lat"], e["lon"])) > 0.2:
        continue                                          # déjà placé précisément
    venue = (e.get("venue") or "").strip()
    if not venue or venue.lower() == e["city"].lower() or len(venue) < 4:
        continue
    q = venue if re.search(r"belg", venue, re.I) else f"{venue}, Belgique"
    hit = cherche(q)
    if not hit and "," in venue:                          # « Salle X, Commune » → la commune seule en dernier recours
        hit = cherche(venue.split(",", 1)[1].strip() + ", Belgique")
    if hit and km(centre, hit[:2]) <= 50:
        e["lat"], e["lon"] = hit[0], hit[1]
        precis += 1
        if hit[2] and km(centre, hit[:2]) > 10:
            e["city"] = hit[2]
            deplaces += 1
    else:
        rate += 1
    if i % 100 == 0:
        print(f"{i}/{len(nouveaux)} · précisés {precis} · autre commune {deplaces} · pas trouvés {rate}", flush=True)
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"fini · précisés {precis} · dont autre commune {deplaces} · toujours au centre {rate}", flush=True)
