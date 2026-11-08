"""Essai ponctuel : Haiku 5.5 lit les mêmes pages que le robot. Ne change rien à l'app : écrit seulement test_modele_resultat.json.
À supprimer après l'essai, avec .github/workflows/test-modele.yml.
Relancé le 8/11/2026 avec la nouvelle clé."""
import json, os, re, subprocess, sys, tempfile, time
import build_events as b

MODELE = "claude-haiku-5-5"
PRIX_ENTREE, PRIX_SORTIE = 0.10, 0.50    # $ par million de tokens, prompts de moins de 100 000 tokens
PLAFOND_USD = 0.60                       # l'essai s'arrête au-delà
REGLAGES = {"sans_reflexion": {"thinking": {"type": "disabled"}}, "reflexion": {}}
AUSSI_AVEC_REFLEXION = ("Le Forum (Liège)", "Trix (Anvers)", "Het Depot (Louvain)", "Reflektor (Liège)")
resultat = {"modele": MODELE, "sources": [], "depense_usd": 0.0, "arret": None}


def appel(prompt, extra):
    body = {"model": MODELE, "max_tokens": 16000, "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}], **extra}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(body, f)
        path = f.name
    r = subprocess.run(["curl", "-s", "--max-time", "280", "https://api.anthropic.com/v1/messages",
                        "-H", f"x-api-key: {os.environ['ANTHROPIC_API_KEY']}", "-H", "anthropic-version: 2023-06-01",
                        "-H", "content-type: application/json", "-d", f"@{path}"], capture_output=True)
    os.unlink(path)
    data = json.loads(r.stdout)
    if "content" not in data:
        raise RuntimeError(str(data)[:400])
    texte = "".join(x.get("text", "") for x in data["content"] if x.get("type") == "text")
    return texte, data.get("stop_reason"), data.get("usage") or {}


def lire(s, texte, reglage):
    parts = b.split_page(texte, s["chunk_chars"]) if s.get("chunk_chars") else [texte]
    raw, vus, stops, entree, sortie, debut = [], set(), [], 0, 0, time.time()
    for part in parts:
        reponse, stop, usage = appel(b.PROMPT.format(
            today=b.TODAY.isoformat(), name=s["name"], url=s["url"], city=s.get("city", ""),
            cats=", ".join(b.CATEGORIES), cat_help=b.CATEGORY_HELP, page=part), REGLAGES[reglage])
        stops.append(stop)
        entree += usage.get("input_tokens", 0)
        sortie += usage.get("output_tokens", 0)
        for r in b.parse_json_array(reponse):
            k = (re.sub(r"\W+", "", str(r.get("title") or "").lower()), str(r.get("start"))[:10])
            if k not in vus:
                vus.add(k)
                raw.append(r)
    cout = entree / 1e6 * PRIX_ENTREE + sortie / 1e6 * PRIX_SORTIE
    return {"source": s["name"], "url": s["url"], "reglage": reglage, "raw": raw, "morceaux": len(parts), "stops": stops,
            "entree": entree, "sortie": sortie, "cout_usd": round(cout, 5), "secondes": round(time.time() - debut, 1)}


def sonde(modele):
    """Un mot demandé à ce modèle : la clé et le modèle sont-ils acceptés ? N'écrit jamais la clé."""
    body = {"model": modele, "max_tokens": 5, "messages": [{"role": "user", "content": "OK"}]}
    r = subprocess.run(["curl", "-s", "--max-time", "60", "https://api.anthropic.com/v1/messages",
                        "-H", f"x-api-key: {os.environ['ANTHROPIC_API_KEY']}", "-H", "anthropic-version: 2023-06-01",
                        "-H", "content-type: application/json", "-d", json.dumps(body)], capture_output=True)
    d = json.loads(r.stdout)
    return {"modele": modele, "ok": "content" in d, "erreur": (d.get("error") or {}).get("message", "")[:160]}


def main():
    cfg = json.loads((b.HERE / "sources.json").read_text())["pages"]
    cle = os.environ.get("ANTHROPIC_API_KEY", "")
    resultat["cle"] = {"longueur": len(cle), "espaces_autour": cle != cle.strip()}
    resultat["sonde"] = [sonde("claude-haiku-4-5-20251001"), sonde(MODELE)]
    (b.HERE / "test_modele_resultat.json").write_text(json.dumps(resultat, ensure_ascii=False, indent=1), encoding="utf-8")
    if not resultat["sonde"][1]["ok"]:
        resultat["arret"] = "sonde refusée, essai non lancé"
        (b.HERE / "test_modele_resultat.json").write_text(json.dumps(resultat, ensure_ascii=False, indent=1), encoding="utf-8")
        return
    try:
        for s in cfg:
            if not s.get("enabled", True):
                continue
            try:
                texte = b.to_text(b.fetch(s["url"]), s["url"])
            except Exception as ex:
                resultat["sources"].append({"source": s["name"], "erreur_page": str(ex)[:200]})
                continue
            if s.get("ignore_lines"):
                texte = "\n".join(l for l in texte.splitlines() if not re.match(s["ignore_lines"], l.strip()))
            for reglage in REGLAGES:
                if reglage == "reflexion" and s["name"] not in AUSSI_AVEC_REFLEXION:
                    continue
                if resultat["depense_usd"] > PLAFOND_USD:
                    resultat["arret"] = "plafond atteint"
                    return
                ligne = lire(s, texte, reglage)
                resultat["sources"].append(ligne)
                resultat["depense_usd"] = round(resultat["depense_usd"] + ligne["cout_usd"], 5)
                print(s["name"], reglage, len(ligne["raw"]), ligne["stops"], ligne["cout_usd"], ligne["secondes"], flush=True)
    except Exception as ex:                 # modèle refusé, crédit vide… : on s'arrête tout de suite et on garde l'explication
        resultat["arret"] = str(ex)[:400]
    finally:
        (b.HERE / "test_modele_resultat.json").write_text(json.dumps(resultat, ensure_ascii=False, indent=1), encoding="utf-8")


main()
