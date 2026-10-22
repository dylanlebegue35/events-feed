"""Importe la liste JSON relevée par l'extension Chrome (Facebook) dans manual_events.json.

    python3 import_extension.py liste.json            # simulation : rien n'est écrit
    python3 import_extension.py liste.json --write    # enregistre

Vérifie les champs, écarte le passé et les doublons (déjà dans events.json ou manual_events.json),
cherche les coordonnées du lieu et refuse un lieu à plus de 40 km de la ville annoncée.
"""
import json, re, sys
from datetime import datetime, timedelta
from pathlib import Path

import build_events as b

HERE = Path(__file__).parent
MANUAL = HERE / "manual_events.json"


def norm(t):
    return re.sub(r"\W+", "", (t or "").lower())


def main():
    items = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    manual = json.loads(MANUAL.read_text(encoding="utf-8"))
    known = {(norm(e["title"]), e["start"][:10]) for e in manual["events"]}
    if (HERE / "events.json").exists():
        known |= {(norm(e["title"]), e["start"][:10]) for e in json.loads((HERE / "events.json").read_text())["events"]}
    kept, skipped = [], []
    for it in items:
        why = None
        try:
            title = (it.get("title") or "").strip()
            sd = datetime.fromisoformat(it["start"])
            ed = datetime.fromisoformat(it["end"]) if it.get("end") else sd + timedelta(hours=3)
            url = it.get("url") or ""
            fid = re.search(r"/events/(\d+)", url)
            city = (it.get("city") or "").strip()
            if not title or not city:
                why = "titre ou ville manquant"
            elif not (url.startswith("https://") and fid):
                why = "lien Facebook invalide"
            elif ed.date() < b.TODAY:
                why = "déjà passé"
            elif (norm(title), sd.date().isoformat()) in known:
                why = "déjà présent"
        except Exception as ex:
            why = f"format invalide ({ex})"
        if why:
            skipped.append((it.get("title"), why))
            continue
        venue = (it.get("venue") or "").strip() or None
        city_pos = b.geocode(None, city)
        pos = b.geocode(venue, city) if venue else None
        if venue and (not pos or pos == city_pos) and "(" in venue:      # « Centre Culturel (Salle X) » : on cherche sans la parenthèse
            pos = b.geocode(re.sub(r"\s*\(.*?\)", "", venue).strip(), city)
        if pos and city_pos and b._km(pos, city_pos) > 40:
            pos = None
        pos = pos or city_pos
        if not pos:
            skipped.append((title, "lieu introuvable"))
            continue
        kept.append({
            "id": "fbx-" + fid.group(1), "title": title,
            "category": it.get("category") if it.get("category") in b.CATEGORIES else "culture",
            "venue": venue or city, "city": city, "lat": pos[0], "lon": pos[1],
            "start": sd.isoformat(timespec="seconds"), "end": ed.isoformat(timespec="seconds"),
            "image": it.get("image") if (it.get("image") or "").startswith("https://") else None,
            "page_url": url, "url": url, "summary": (it.get("summary") or "")[:300],
            "price_from": it.get("price_from") or None, "free": True if (it.get("free") or it.get("price_from") == 0) else None,
            "audience": [], "tags": [], "source": "Facebook (extension)",
            "geo_precise": bool(venue and pos != city_pos),
        })
    print(f"{len(kept)} à ajouter, {len(skipped)} écartés")
    for e in kept:
        print(f"  + {e['start'][:10]} {e['title'][:46]:46} {e['venue'][:30]:30} {'lieu précis' if e['geo_precise'] else 'centre-ville'}")
    for t, w in skipped:
        print(f"  - {str(t)[:46]:46} {w}")
    if "--write" in sys.argv:
        for e in kept:
            e.pop("geo_precise", None)
        manual["events"] += kept
        MANUAL.write_text(json.dumps(manual, ensure_ascii=False, indent=2), encoding="utf-8")
        print("écrit dans manual_events.json")


if __name__ == "__main__":
    main()
