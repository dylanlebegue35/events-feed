"""Trie le relevé Facebook de la nuit du 24/11/2026 (brut.json : id → {v: ville cherchée, t: lignes de la carte, img})
et écrit liste.json au format de events-feed/import_extension.py.

    python3 trier.py            # statistiques + liste.json
"""
import json, re, subprocess, sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

ICI = Path(__file__).parent
GO = ICI.parent / "go"
FEED_MAIN = Path("/Users/dylanlebegue/Desktop/Studio Apps/events-feed")
AUJ = date(2026, 11, 24)
HORIZON = AUJ + timedelta(days=61)
MOIS = {"janv": 1, "févr": 2, "fevr": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6, "juil": 7, "août": 8, "aout": 8,
        "sept": 9, "oct": 10, "nov": 11, "déc": 12, "dec": 12}
JOURS = {"lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6}
NOM_JOUR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
NOM_MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]

R_DATE = re.compile(r"^(Aujourd’hui|Aujourd'hui|Demain|Ce \w+|En cours|(Lun|Mar|Mer|Jeu|Ven|Sam|Dim)\.?,? \d)", re.I)
R_STATS = re.compile(r"intéressé|participant|annulé|Cet évènement", re.I)
ETRANGER = re.compile(
    r"\b(France|Nederland|Netherlands|Pays-Bas|Deutschland|Germany|Allemagne|Luxemburg|Grand-Duché|Lëtzebuerg)\b|"
    r",\s*Luxembourg\s*$|\bL-\d{4}\b|\b(Lille|Roubaix|Tourcoing|Wattrelos|Halluin|Armenti[eè]res|Maubeuge|Valenciennes|Givet|"
    r"Sedan|Charleville|Longwy|Dunkerque|Maastricht|Vaals|Heerlen|Sittard|Kerkrade|Breda|Eindhoven|Terneuzen|Sluis|Venlo|"
    r"Roermond|Aachen|Aix-la-Chapelle|Monschau|Esch-sur-Alzette|Diekirch|Clervaux|Wiltz|Ettelbruck|Differdange)\b", re.I)
PAS_SORTIE = re.compile(
    r"groupe de parole|cercles? de pardon|pri[eè]re|\bmesse\b|eucharistie|souper de section|candidature|recrutement|"
    r"webinai?re?|\ben ligne\b|\bonline\b|multijoueur|assembl[ée]e g[ée]n[ée]rale|permanence|d[ée]stockage|vente priv[ée]e|"
    r"s[ée]ance d.information|r[ée]union d.information|collecte de sang|don de sang|bloedinzameling|vergadering|"
    r"infosessie|infoavond|opleiding|formation (?:continue|professionnelle)|job ?dating|jobbeurs|"
    r"cours de (?:yoga|pilates|gym|fitness)|aqua ?yoga|pilates|r[ée]trospective interne|test\b|c[ée]l[ée]bration|adoration|"
    r"collecte|excursion|voyage|autocar|\ben car\b|busreis|daguitstap|d[ée]part (?:de|du|en)|\b[àa] [A-ZÉ][\w'’-]+ & ", re.I)

CATS = [
    ("flea", r"brocante|rommelmarkt|vide[- ]?(grenier|dressing)|bourse aux|ruilbeurs|tweedehands|kofferbak"),
    ("fair", r"march[ée] de no[eë]l|kerstmarkt|village de no[eë]l|kerstdorp|march[ée]|markt|salon|foire|beurs|braderie|kermesse|kermis"),
    ("festival", r"festival"),
    ("party", r"soir[ée]e|party|fuif|\bTD\b|\bbal\b|\bDJ\b|clubbing|blind ?test|quiz|remise de pennes|bapt[eê]me|cantus|r[ée]veillon|nouvel an|oudejaar|apéro|afterwork|karaok"),
    ("concert", r"concert|live|optreden|chorale|koor|orchestre|orkest|r[ée]cital|jazz|rock|rap|chanson|tribute|hommage [àa]|gospel"),
    ("theatre", r"th[ée][aâ]tre|theater|toneel|spectacle|voorstelling|com[ée]die|humour|stand[- ]?up|one[- ]?(wo)?man|op[ée]ra|ballet|danse|dans\b|cirque|circus|magie|impro|cabaret|marionnette"),
    ("expo", r"expo|exposition|tentoonstelling|vernissage|finissage|galerie|mus[ée]e|museum"),
    ("cinema", r"cin[ée]|film|projection|filmvoorstelling"),
    ("sport", r"match|tournoi|toernooi|jogging|marathon|trail|\brun\b|course|voetbal|basket|volley|hockey|cyclo|zwem|natation|patinoire|schaats"),
    ("outdoor", r"balade|randonn[ée]e|wandel|promenade|nature|natuur|fiets|v[ée]lo"),
    ("family", r"enfant|kids|kinder|famille|gezin|saint[- ]nicolas|sinterklaas|p[eè]re no[eë]l|kerstman|contes|goûter"),
    ("food", r"d[ée]gustation|proeverij|souper|repas|brunch|petit[- ]d[ée]jeuner|ontbijt|\bfood|bi[eè]re|bier|vin\b|wijn|bbq|barbecue|cuisine|diner|d[îi]ner|zuurkool|choucroute"),
    ("workshop", r"atelier|workshop|stage|cours|initiation|masterclass|creatief"),
    ("talks", r"conf[ée]rence|lezing|d[ée]bat|rencontre|table ronde|causerie|voordracht|lecture"),
    ("geek", r"jeux de soci[ée]t[ée]|jeu|gaming|\bLAN\b|manga|cosplay|bordspel|escape"),
    ("wellness", r"yoga|m[ée]ditation|gong|bien[- ][êe]tre|sophro|relaxation"),
]
LIB = {"flea": "Brocante", "fair": "Marché", "festival": "Festival", "party": "Soirée", "concert": "Concert",
       "theatre": "Spectacle", "expo": "Expo", "cinema": "Séance de cinéma", "sport": "Rendez-vous sportif",
       "outdoor": "Balade", "family": "Sortie en famille", "food": "Rendez-vous gourmand", "workshop": "Atelier",
       "talks": "Rencontre", "geek": "Après-midi jeux", "wellness": "Moment bien-être", "culture": "Événement"}


def jour_rel(s):
    s = s.strip()
    low = s.lower()
    if low.startswith(("aujourd", "en cours")):
        return AUJ
    if low.startswith("demain"):
        return AUJ + timedelta(days=1)
    m = re.match(r"ce (\w+)", low)
    if m and m.group(1) in JOURS:
        delta = (JOURS[m.group(1)] - AUJ.weekday()) % 7
        return AUJ + timedelta(days=delta or 7)
    return None


def une_date(s, annee_defaut=None):
    """« Sam, 12 déc. » / « 11 avr. 2027 » / « Ce samedi » → date."""
    d = jour_rel(s)
    if d:
        return d
    m = re.search(r"(\d{1,2}) ([a-zéû]+)\.?(?: (\d{4}))?", s.lower())
    if not m:
        return None
    mois = MOIS.get(m.group(2)[:4].rstrip(".")) or MOIS.get(m.group(2)[:3])
    if not mois:
        return None
    an = int(m.group(3)) if m.group(3) else (annee_defaut or (2026 if mois >= 11 else 2027))
    try:
        return date(an, mois, int(m.group(1)))
    except ValueError:
        return None


def parse_quand(s):
    """Renvoie (début datetime, fin datetime ou None, heure connue ?)."""
    s = re.sub(r"\s+(CET|CEST|UTC[+−-]\d+)", "", s)
    s = re.sub(r"\s+et \d+ autres? dates?", "", s)
    heure = re.search(r"à (\d{1,2}):(\d{2})", s)
    if " - " in s:                       # « Ven, 27 nov. - 11 déc. » ou « Ven, 19 mars 2027 - 21 mars 2027 »
        a, b = s.split(" - ", 1)
        d1 = une_date(a)
        an = re.search(r"\d{4}", b)
        d2 = une_date(b, int(an.group()) if an else None)
        if d1 and d2 and d2 < d1:
            d2 = date(d2.year + 1, d2.month, d2.day)
        if not d1:
            return None
        h = (int(heure.group(1)), int(heure.group(2))) if heure else None
        st = datetime(d1.year, d1.month, d1.day, *(h or (10, 0)))
        en = datetime(d2.year, d2.month, d2.day, 22, 0) if d2 else None
        return st, en, bool(h)
    d = une_date(s)
    if not d:
        return None
    h = (int(heure.group(1)), int(heure.group(2))) if heure else None
    st = datetime(d.year, d.month, d.day, *(h or (10, 0)))
    return st, None, bool(h)


def interet(stats):
    def num(rx):
        m = re.search(rx, stats)
        if not m:
            return 0
        x = re.sub(r"\s", "", m.group(1)).replace(",", ".")
        try:
            return int(float(x[:-1]) * 1000) if x.upper().endswith("K") else int(float(x))
        except ValueError:
            return 0
    return num(r"(\d[\d.,]*\s?K?)\s*intéressé") + num(r"(\d[\d.,]*\s?K?)\s*participant")


def categorie(titre):
    for c, rx in CATS:
        if re.search(rx, titre, re.I):
            return c
    return "culture"


def resume(cat, titre, ville, st, h_connue, lieu):
    quand = f"le {NOM_JOUR[st.weekday()]} {st.day} {NOM_MOIS[st.month - 1]}"
    if h_connue:
        quand += f" à {st.hour} h" + (f" {st.minute:02d}" if st.minute else "")
    lieu_txt = f", {lieu}" if lieu and len(lieu) < 70 and not lieu.lower().startswith(ville.lower()) else ""
    s = f"{LIB[cat]} à {ville}{lieu_txt}, {quand}. Toutes les infos sur la page Facebook de l'événement."
    return s[:200]


def norm(t):
    return re.sub(r"\W+", "", (t or "").lower())


def connus():
    ids, paires = set(), set()
    subprocess.run(["git", "-C", str(FEED_MAIN), "fetch", "-q", "origin"], capture_output=True, timeout=60)
    r = subprocess.run(["git", "-C", str(FEED_MAIN), "show", "origin/main:events.json"], capture_output=True, timeout=60)
    sources = [json.loads(r.stdout)["events"]] if r.returncode == 0 else []
    for f in (GO / "manual_events.json", GO / "facebook_events.json"):
        d = json.loads(f.read_text())
        sources.append(d["events"] if isinstance(d, dict) else d)
    for evs in sources:
        for e in evs:
            m = re.match(r"fbx?-(\d+)", str(e.get("id") or "")) or re.search(r"/events/(\d+)", e.get("url") or e.get("page_url") or "")
            if m:
                ids.add(m.group(1))
            if e.get("title") and e.get("start"):
                paires.add((norm(e["title"]), str(e["start"])[:10]))
    return ids, paires


def main():
    brut = json.loads((ICI / "brut.json").read_text())
    ids_connus, paires = connus()
    raisons, garde = Counter(), []
    for fid, e in brut.items():
        t = [re.sub(r"[\u00a0\u202f]", " ", x) for x in (e.get("t") or [])]
        if len(t) < 2 or not R_DATE.match(t[0]):
            raisons["carte illisible"] += 1
            continue
        stats = next((x for x in t[1:] if R_STATS.search(x)), "")
        corps = [x for x in t[1:] if x != stats]
        if not corps:
            raisons["carte illisible"] += 1
            continue
        titre = corps[0].strip()
        lieu = corps[-1].strip() if len(corps) >= 2 else None
        if re.search(r"annulé", stats, re.I):
            raisons["annulé"] += 1
            continue
        q = parse_quand(t[0])
        if not q:
            raisons["date illisible"] += 1
            continue
        st, en, h_connue = q
        fin = (en or st).date()
        if fin < AUJ:
            raisons["passé"] += 1
            continue
        if st.date() > HORIZON:
            raisons["dans plus de 2 mois"] += 1
            continue
        if ETRANGER.search(f"{lieu or ''} {titre}"):
            raisons["hors de Belgique"] += 1
            continue
        if PAS_SORTIE.search(titre):
            raisons["pas une sortie"] += 1
            continue
        n = interet(stats)
        if n < 5:
            raisons["moins de 5 intéressés"] += 1
            continue
        if fid in ids_connus or (norm(titre), st.date().isoformat()) in paires:
            raisons["déjà dans Vivyo"] += 1
            continue
        ville = e["v"]
        cat = categorie(titre)
        garde.append({
            "title": titre, "start": st.isoformat(timespec="minutes"), "end": en.isoformat(timespec="minutes") if en else None,
            "venue": lieu, "city": ville, "category": cat, "free": None, "price_from": None,
            "url": f"https://www.facebook.com/events/{fid}/", "image": e.get("img") or None,
            "summary": resume(cat, titre, ville, st, h_connue, lieu), "_interet": n,
        })
    garde.sort(key=lambda x: -x["_interet"])
    (ICI / "liste.json").write_text(json.dumps([{k: v for k, v in g.items() if not k.startswith("_")} for g in garde],
                                               ensure_ascii=False, indent=1))
    print(f"relevés : {len(brut)} · gardés : {len(garde)}")
    for r, n in raisons.most_common():
        print(f"  écartés, {r} : {n}")
    print("par ville :", Counter(g["city"] for g in garde).most_common(60))
    print("par catégorie :", Counter(g["category"] for g in garde).most_common())
    print("avec affiche :", sum(1 for g in garde if g["image"]))
    for g in garde[:12]:
        print("  ", g["start"], g["city"], "·", g["title"][:50], "·", g["category"], "·", g["_interet"])


if __name__ == "__main__":
    main()
