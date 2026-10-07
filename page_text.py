"""Transforme une page web en texte lisible par une IA : texte + liens + images utiles."""
import html, re, subprocess
from urllib.parse import urljoin


def fetch(url, timeout=30):
    r = subprocess.run(["curl", "-sL", "--max-time", str(timeout), "-A",
                        "Mozilla/5.0 (compatible; StudioAppsBot/1.0; +events-feed)", "--", url], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"curl {r.returncode} pour {url}")
    return r.stdout.decode("utf-8", errors="ignore")


def to_text(page, base, limit=60000):
    page = re.sub(r"<(script|style|noscript|svg|nav|footer|header)\b.*?</\1>", " ", page, flags=re.S | re.I)
    og = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page, re.I)
    # garde les liens et images sous forme [texte](url) / ![](url)
    page = re.sub(r'<img[^>]+(?:data-src|src)=["\']([^"\']+)["\'][^>]*>',
                  lambda m: f" ![img]({urljoin(base, m.group(1))}) ", page, flags=re.I)
    page = re.sub(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                  lambda m: f" [{re.sub('<[^>]+>', ' ', m.group(2)).strip()}]({urljoin(base, m.group(1))}) ",
                  page, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", page))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    head = f"[image principale de la page : {urljoin(base, og.group(1))}]\n" if og else ""
    return (head + text)[:limit]
