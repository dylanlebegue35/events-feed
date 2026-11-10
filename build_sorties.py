#!/usr/bin/env python3
"""Fabrique les pages « Que faire à <ville> ce week-end ? » de vivyo.app à partir de events.json.

    python3 build_sorties.py                     # écrit sorties/<ville>.html et sorties/index.html
    python3 build_sorties.py --maintenant 2026-11-13T18:00   # pour tester un autre moment

Le robot lance ce script après build_events.py, 4 fois par jour. Le Worker vivyo-sorties sert
ensuite ces pages sous https://vivyo.app/sorties/<ville>.

Tout ce qui vient des événements (titres, lieux, liens) est échappé : ces textes viennent de pages
web lues par l'IA et ne doivent jamais pouvoir injecter du HTML dans vivyo.app.
"""
import argparse
import html
import json
import math
import re
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ICI = Path(__file__).resolve().parent
SORTIE = ICI / "sorties"
BRUXELLES = ZoneInfo("Europe/Brussels")
SITE = "https://vivyo.app"

# ville : (nom affiché, latitude, longitude, rayon en km). Une ville ajoutée ici doit aussi
# être ajoutée dans VILLES de sorties-worker/worker.js (dépôt studio-apps), sinon sa page reste en 404.
VILLES = {
    "liege": ("Liège", 50.6326, 5.5797, 12),
    "bruxelles": ("Bruxelles", 50.8467, 4.3525, 10),
    "namur": ("Namur", 50.4674, 4.8718, 12),
    "mons": ("Mons", 50.4542, 3.9567, 12),
    "charleroi": ("Charleroi", 50.4108, 4.4446, 12),
    "louvain-la-neuve": ("Louvain-la-Neuve", 50.6681, 4.6118, 8),
}

CATEGORIES = {
    "concert": "Concert", "theatre": "Théâtre", "party": "Soirée", "fair": "Salon", "sport": "Sport",
    "food": "Gastronomie", "cinema": "Cinéma", "family": "En famille", "talks": "Rencontre",
    "workshop": "Atelier", "culture": "Culture", "outdoor": "Plein air", "festival": "Festival",
    "geek": "Geek", "flea": "Brocante", "expo": "Expo", "motorsport": "Sport auto", "wellness": "Bien-être",
}
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
        "octobre", "novembre", "décembre"]
HEURE_INCONNUE = "T10:00:00"   # le robot met 10 h quand la page ne donne pas l'heure
MAX_PAR_PAGE = 60
MAX_EN_CE_MOMENT = 6


def e(texte):
    return html.escape(str(texte or ""), quote=True)


def lien_sur(url, https_seulement=False):
    """Uniquement http(s), sinon rien : un lien javascript: ou data: ne doit jamais sortir."""
    if not isinstance(url, str) or len(url) > 2000:
        return None
    url = url.strip()
    ok = url.startswith("https://") or (not https_seulement and url.startswith("http://"))
    return url if ok and not any(c in url for c in '"<> \n\r\t') else None


COURRIEL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def texte(valeur, maxi):
    """Un texte propre : jamais autre chose qu'une chaîne, sans caractères de contrôle ni d'inversion
    du sens d'écriture, et pas plus long que maxi."""
    if not isinstance(valeur, str):
        return ""
    propre = "".join(" " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in valeur)
    propre = " ".join(propre.split())
    return propre if len(propre) <= maxi else propre[:maxi - 1].rstrip() + "…"


def nombre(valeur, mini, maxi):
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)) or not math.isfinite(valeur):
        return None
    return valeur if mini <= valeur <= maxi else None


def nettoyer(brut):
    """Recopie seulement les champs utiles, au bon type. Tout le reste est ignoré."""
    if not isinstance(brut, dict):
        return None
    ev = {
        "title": texte(brut.get("title"), 140),
        "venue": texte(brut.get("venue"), 100),
        "city": texte(brut.get("city"), 60),
        "category": brut.get("category") if isinstance(brut.get("category"), str) else "",
        "lat": nombre(brut.get("lat"), 49, 52),
        "lon": nombre(brut.get("lon"), 2, 7),
        "start": brut.get("start") if isinstance(brut.get("start"), str) else "",
        "end": brut.get("end") if isinstance(brut.get("end"), str) else None,
        "url": brut.get("url") if isinstance(brut.get("url"), str) else None,
        "page_url": brut.get("page_url") if isinstance(brut.get("page_url"), str) else None,
        "image": brut.get("image") if isinstance(brut.get("image"), str) else None,
        "image_credit": bool(brut.get("image_credit")),
        "summary": COURRIEL.sub("", texte(brut.get("summary"), 300)).strip(),
        "free": brut.get("free") is True,
        "price_from": nombre(brut.get("price_from"), 0.01, 10000),
    }
    return ev if ev["title"] and ev["lat"] is not None and ev["lon"] is not None and ev["start"] else None


def km(lat1, lon1, lat2, lon2):
    r = math.radians
    x = r(lon2 - lon1) * math.cos(r((lat1 + lat2) / 2))
    return 6371 * math.hypot(x, r(lat2 - lat1))


def lire_date(texte):
    try:
        d = datetime.fromisoformat(str(texte)[:19])
        d = d.replace(tzinfo=BRUXELLES) if d.tzinfo is None else d.astimezone(BRUXELLES)
        d + timedelta(days=4)   # une date au bord du calendrier ferait planter les calculs plus loin
        return d
    except (ValueError, TypeError, OverflowError):
        return None


def fenetre(maintenant):
    """Du vendredi 0 h au dimanche soir ; le week-end même, à partir de maintenant."""
    jour = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
    if maintenant.weekday() <= 3:
        debut = jour + timedelta(days=4 - maintenant.weekday())
    else:
        debut = jour - timedelta(days=maintenant.weekday() - 4)
    fin = debut + timedelta(days=3) - timedelta(seconds=1)
    return debut, fin


def jour_long(d):
    return f"{JOURS[d.weekday()]} {d.day}{'er' if d.day == 1 else ''} {MOIS[d.month - 1]}"


def heure(d):
    return f"{d.hour} h" if d.minute == 0 else f"{d.hour} h {d.minute:02d}"


def prix(ev):
    if ev.get("free") is True:
        return "Gratuit"
    p = ev.get("price_from")
    if p:
        return f"Dès {p:g} €".replace(".", ",")
    return ""


def choisir(evenements, ville, maintenant):
    nom, lat, lon, rayon = VILLES[ville]
    debut, fin = fenetre(maintenant)
    weekend, en_ce_moment, vus = [], [], set()
    for brut in evenements:
        try:
            item = _choisir_un(brut, lat, lon, rayon, debut, fin, maintenant, vus)
        except Exception as erreur:   # une fiche bizarre ne doit jamais empêcher les autres pages
            print("Fiche ignorée :", type(erreur).__name__, file=sys.stderr)
            continue
        if item:
            (en_ce_moment if (item["fin"] - item["debut"]).days > 3 else weekend).append(item)
    weekend.sort(key=lambda i: (i["jour"], not i["heure_connue"], i["debut"], i["ev"]["title"]))
    en_ce_moment.sort(key=lambda i: i["fin"])
    return weekend[:MAX_PAR_PAGE], en_ce_moment[:MAX_EN_CE_MOMENT], debut, fin


def _choisir_un(brut, lat, lon, rayon, debut, fin, maintenant, vus):
    ev = nettoyer(brut)
    if not ev or km(lat, lon, ev["lat"], ev["lon"]) > rayon:
        return None
    s, f = lire_date(ev["start"]), lire_date(ev["end"] or ev["start"])
    if not s:
        return None
    f = max(f or s, s)
    connue = not ev["start"].endswith(HEURE_INCONNUE)
    fin_reelle = f if f > s else (s + timedelta(hours=3) if connue else s.replace(hour=23, minute=59))
    if s > fin or fin_reelle < max(debut, maintenant):
        return None
    cle = (ev["title"].lower(), s.date(), ev["venue"].lower())
    if cle in vus:
        return None
    vus.add(cle)
    return {"ev": ev, "debut": s, "fin": f, "heure_connue": connue,
            "jour": max(s.date(), max(debut, maintenant).date())}


def carte(item, ville_nom):
    ev = item["ev"]
    titre = e(ev["title"].strip())
    cat = CATEGORIES.get(ev.get("category"), "Sortie")
    lieu = (ev.get("venue") or "").strip()
    ville_ev = (ev.get("city") or "").strip()
    if not lieu or lieu.lower() == ville_ev.lower():
        lieu = ville_ev or ville_nom
    elif ville_ev and ville_ev.lower() not in lieu.lower() and ville_ev.lower() != ville_nom.lower():
        lieu = f"{lieu}, {ville_ev}"
    infos = []
    if item["heure_connue"] and item["debut"].date() == item["jour"]:
        infos.append(heure(item["debut"]))
    infos.append(lieu)
    p = prix(ev)
    lien = lien_sur(ev.get("url") or ev.get("page_url"))
    image = lien_sur(ev.get("image"), https_seulement=True) if not ev.get("image_credit") else None
    vignette = (f'<img class="vi" src="{e(image)}" alt="" width="88" height="88" loading="lazy" '
                f'referrerpolicy="no-referrer">') if image else f'<span class="vi vide">{e(cat[:1])}</span>'
    corps = (f'<p class="cat">{e(cat)}{" · " + e(p) if p else ""}</p>'
             f'<h3>{titre}</h3><p class="infos">{" · ".join(e(i) for i in infos)}</p>')
    if lien:
        return (f'<li><a class="ev" href="{e(lien)}" target="_blank" rel="nofollow noopener">'
                f'{vignette}<div>{corps}<span class="voir">Voir l\'événement ↗</span></div></a></li>')
    return f'<li><div class="ev">{vignette}<div>{corps}</div></div></li>'


def date_ld(item, cle):
    d = item[cle]
    return d.date().isoformat() if not item["heure_connue"] else d.isoformat()


def evenement_ld(item, ville_nom):
    ev = item["ev"]
    lien = lien_sur(ev.get("url") or ev.get("page_url"))
    d = {
        "@type": "Event",
        "name": ev["title"].strip(),
        "startDate": date_ld(item, "debut"),
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "location": {"@type": "Place", "name": (ev.get("venue") or ev.get("city") or ville_nom).strip(),
                     "address": {"@type": "PostalAddress", "addressLocality": (ev.get("city") or ville_nom).strip(),
                                 "addressCountry": "BE"}},
    }
    if item["fin"] > item["debut"]:
        d["endDate"] = date_ld(item, "fin")
    if lien:
        d["url"] = lien
    image = lien_sur(ev.get("image"), https_seulement=True)
    if image and not ev.get("image_credit"):
        d["image"] = [image]
    if ev.get("summary"):
        d["description"] = str(ev["summary"])[:300]
    if ev.get("free") is True:
        d["isAccessibleForFree"] = True
    elif ev.get("price_from"):
        d["offers"] = {"@type": "Offer", "price": ev["price_from"], "priceCurrency": "EUR"}
        if lien:
            d["offers"]["url"] = lien
    return d


def json_ld(donnees):
    # aucun « < », « > » ou « & » brut dans un <script> : un titre ne doit jamais pouvoir refermer la balise
    texte = json.dumps(donnees, ensure_ascii=False, separators=(",", ":"))
    return texte.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


STYLE = """
:root{--green:#0A7F55;--ink:#1D1D1F;--muted:#6E6E73;--soft:#F5F5F7;--line:#D2D2D7}
*{box-sizing:border-box;margin:0}
body{font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,"Helvetica Neue",Arial,sans-serif;color:var(--ink);background:#fff;-webkit-font-smoothing:antialiased;line-height:1.4}
a{color:inherit}
.nav{display:flex;justify-content:space-between;align-items:center;max-width:860px;margin:0 auto;padding:16px 20px}
.logo{font-weight:800;font-size:22px;letter-spacing:-.04em;text-decoration:none}
.nav .app{font-size:15px;font-weight:600;color:var(--green);text-decoration:none}
header.top{max-width:860px;margin:0 auto;padding:28px 20px 8px}
.kicker{font-size:16px;font-weight:600;color:var(--green)}
h1{font-size:clamp(34px,6vw,58px);line-height:1.04;font-weight:700;letter-spacing:-.04em;margin-top:6px}
.lead{color:var(--muted);font-size:clamp(18px,2.2vw,21px);margin-top:14px}
.maj{color:var(--muted);font-size:14px;margin-top:6px}
main{max-width:860px;margin:0 auto;padding:8px 20px 40px}
h2{font-size:clamp(24px,3.4vw,32px);letter-spacing:-.03em;margin:40px 0 10px}
ul.liste{list-style:none;padding:0}
ul.liste li{border-top:1px solid var(--line)}
.ev{display:flex;gap:16px;padding:16px 0;text-decoration:none;align-items:flex-start}
a.ev:hover h3{color:var(--green)}
.vi{flex:none;width:88px;height:88px;border-radius:14px;object-fit:cover;background:var(--soft)}
.vide{display:flex;align-items:center;justify-content:center;font-size:30px;font-weight:700;color:var(--green)}
.cat{font-size:13px;font-weight:600;color:var(--green);text-transform:uppercase;letter-spacing:.04em}
h3{font-size:19px;line-height:1.25;margin-top:3px;letter-spacing:-.01em}
.infos{color:var(--muted);font-size:15px;margin-top:4px}
.voir{display:inline-block;font-size:14px;font-weight:600;color:var(--green);margin-top:6px}
.vide-msg{color:var(--muted);font-size:18px;padding:16px 0}
.app-cta{background:#000;color:#F5F5F7;border-radius:28px;padding:36px 28px;margin:48px 0 0;text-align:center}
.app-cta h2{text-transform:none;margin:0;color:#fff}
.app-cta p{color:#A1A1A6;font-size:18px;margin-top:10px}
.bouton{display:inline-block;margin-top:20px;background:var(--green);color:#fff;font-weight:600;font-size:17px;padding:14px 24px;border-radius:999px;text-decoration:none}
.villes{display:flex;flex-wrap:wrap;gap:10px;margin-top:14px;padding:0;list-style:none}
.villes a{display:inline-block;background:var(--soft);border-radius:999px;padding:10px 16px;font-weight:600;text-decoration:none}
.villes a:hover{color:var(--green)}
.note{color:var(--muted);font-size:14px;margin-top:28px}
footer{border-top:1px solid var(--line);max-width:860px;margin:0 auto;padding:22px 20px 40px;color:var(--muted);font-size:14px;display:flex;flex-wrap:wrap;gap:8px 18px}
footer a{text-decoration:none}
@media (max-width:520px){.vi{width:72px;height:72px}h3{font-size:17px}}
"""


def page(titre, description, chemin, corps, ld):
    url = SITE + chemin
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(titre)}</title>
<meta name="description" content="{e(description)}">
<link rel="canonical" href="{e(url)}">
<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta property="og:type" content="website">
<meta property="og:title" content="{e(titre)}">
<meta property="og:description" content="{e(description)}">
<meta property="og:url" content="{e(url)}">
<meta property="og:image" content="{SITE}/images/og-image.png">
<meta property="og:locale" content="fr_BE">
<meta property="og:site_name" content="Vivyo">
<script type="application/ld+json">{json_ld(ld)}</script>
<style>{STYLE}</style>
</head>
<body>
<nav class="nav"><a class="logo" href="{SITE}/">vivyo</a><a class="app" href="{SITE}/#inscription">L'app arrive sur iPhone</a></nav>
{corps}
<footer><span>© {datetime.now(BRUXELLES).year} Vivyo · Belgique</span><a href="{SITE}/">Accueil</a><a href="{SITE}/sorties">Toutes les villes</a><a href="{SITE}/pro">Organisateurs</a><a href="{SITE}/conditions">Conditions</a><a href="{SITE}/confidentialite">Confidentialité</a><a href="mailto:contact@vivyo.app">contact@vivyo.app</a></footer>
</body>
</html>
"""


def bloc_app():
    return (f'<section class="app-cta"><h2>Toutes les sorties près de toi, dans une app.</h2>'
            f'<p>Vivyo arrive bientôt sur iPhone : la carte, tes favoris et les rappels, gratuitement.</p>'
            f'<a class="bouton" href="{SITE}/#inscription">Être prévenu de la sortie</a></section>')


def autres_villes(sauf=None):
    liens = "".join(f'<li><a href="{SITE}/sorties/{v}">{e(VILLES[v][0])}</a></li>' for v in VILLES if v != sauf)
    return liens


def page_ville(ville, evenements, maintenant):
    nom = VILLES[ville][0]
    weekend, en_ce_moment, debut, fin = choisir(evenements, ville, maintenant)
    dates = f"du {debut.day} au {fin.day} {MOIS[fin.month - 1]}" if debut.month == fin.month else \
        f"du {debut.day} {MOIS[debut.month - 1]} au {fin.day} {MOIS[fin.month - 1]}"
    n = len(weekend)
    titre = f"Que faire à {nom} ce week-end ? Sorties {dates} · Vivyo"
    description = (f"{n} sortie{'s' if n > 1 else ''} à {nom} ce week-end ({dates}) : concerts, soirées, théâtre, "
                   f"expos et bons plans. Le programme est mis à jour 4 fois par jour.")
    jours = {}
    for item in weekend:
        jours.setdefault(item["jour"], []).append(item)
    sections = "".join(f'<h2>{e(jour_long(datetime.combine(j, datetime.min.time())).capitalize())}</h2>'
                       f'<ul class="liste">{"".join(carte(i, nom) for i in items)}</ul>'
                       for j, items in jours.items())
    if not sections:
        sections = (f'<p class="vide-msg">Le programme de ce week-end à {e(nom)} arrive bientôt. '
                    f'Reviens jeudi : la page se met à jour toute seule.</p>')
    if en_ce_moment:
        sections += ('<h2>À voir en ce moment</h2><ul class="liste">'
                     + "".join(carte(i, nom) for i in en_ce_moment) + "</ul>")
    maj = f"{jour_long(maintenant)} à {heure(maintenant)}"
    corps = f"""<header class="top">
<p class="kicker">Sorties à {e(nom)}</p>
<h1>Que faire à {e(nom)} ce week-end ?</h1>
<p class="lead">{e(dates[0].upper() + dates[1:])} {fin.year} · {n} sortie{"s" if n > 1 else ""} : concerts, soirées, théâtre, expos et bons plans.</p>
<p class="maj">Mis à jour le {e(maj)}.</p>
</header>
<main>
{sections}
{bloc_app()}
<h2>Ce week-end, ailleurs en Belgique</h2>
<ul class="villes">{autres_villes(ville)}</ul>
<p class="note">Les infos viennent des organisateurs et de sources publiques. Les horaires et les prix peuvent changer : vérifie toujours sur la page de l'événement avant de te déplacer. Tu organises quelque chose à {e(nom)} ? <a href="{SITE}/pro">Fais-le connaître sur Vivyo</a>.</p>
</main>"""
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Vivyo", "item": SITE + "/"},
            {"@type": "ListItem", "position": 2, "name": "Sorties", "item": SITE + "/sorties"},
            {"@type": "ListItem", "position": 3, "name": nom, "item": f"{SITE}/sorties/{ville}"}]},
        {"@type": "ItemList", "name": f"Sorties à {nom} ce week-end",
         "itemListElement": [{"@type": "ListItem", "position": k + 1, "item": evenement_ld(i, nom)}
                             for k, i in enumerate(weekend)]}]}
    return page(titre, description, f"/sorties/{ville}", corps, ld), n


def page_index(comptes, maintenant):
    debut, fin = fenetre(maintenant)
    lignes = "".join(f'<li><a href="{SITE}/sorties/{v}">{e(VILLES[v][0])} · {comptes[v]}</a></li>' for v in VILLES)
    corps = f"""<header class="top">
<p class="kicker">Sorties en Belgique</p>
<h1>Que faire ce week-end ?</h1>
<p class="lead">Du {debut.day} au {fin.day} {MOIS[fin.month - 1]} : choisis ta ville. Concerts, soirées, théâtre, expos et bons plans, mis à jour 4 fois par jour.</p>
</header>
<main>
<ul class="villes">{lignes}</ul>
{bloc_app()}
</main>"""
    ld = {"@context": "https://schema.org", "@type": "CollectionPage", "name": "Que faire ce week-end en Belgique ?",
          "url": SITE + "/sorties"}
    return page("Que faire ce week-end en Belgique ? Sorties par ville · Vivyo",
                "Les sorties du week-end à Liège, Bruxelles, Namur, Mons, Charleroi et Louvain-la-Neuve : "
                "concerts, soirées, théâtre et expos, mis à jour 4 fois par jour.", "/sorties", corps, ld)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--maintenant", help="date et heure à simuler, ex. 2026-11-13T18:00")
    p.add_argument("--events", default=str(ICI / "events.json"))
    p.add_argument("--sortie", default=str(SORTIE))
    a = p.parse_args()
    maintenant = lire_date(a.maintenant) if a.maintenant else datetime.now(BRUXELLES)
    evenements = json.loads(Path(a.events).read_text(encoding="utf-8")).get("events", [])
    dossier = Path(a.sortie)
    dossier.mkdir(exist_ok=True)
    comptes, ratees = {}, []
    for ville in VILLES:
        try:
            contenu, comptes[ville] = page_ville(ville, evenements, maintenant)
            (dossier / f"{ville}.html").write_text(contenu, encoding="utf-8")
        except Exception as erreur:
            comptes[ville] = 0
            ratees.append(f"{ville} ({type(erreur).__name__}: {erreur})")
    (dossier / "index.html").write_text(page_index(comptes, maintenant), encoding="utf-8")
    print("Pages sorties :", ", ".join(f"{VILLES[v][0]} {n}" for v, n in comptes.items()))
    if ratees:
        print("Pages non refaites :", "; ".join(ratees), file=sys.stderr)
        sys.exit(1)   # l'étape apparaît en rouge dans GitHub, sans bloquer les événements


if __name__ == "__main__":
    main()
