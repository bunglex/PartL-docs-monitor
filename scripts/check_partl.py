#!/usr/bin/env python3
import argparse, json, re, sys, urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import yaml

def abs_url(base, href):
    return urllib.parse.urljoin(base, href)

def fetch_html(url):
    r = requests.get(url, timeout=30, headers={"User-Agent":"PartL-Docs-Monitor/1.0"})
    r.raise_for_status()
    return r.text

def score_anchor(a, rule):
    text = (a.get_text() or "").strip()
    href = a.get("href") or ""
    t = text.lower()
    h = href.lower()

    score = 0

    for s in rule.get("text_includes", []):
        if s.lower() in t: score += 5
    for s in rule.get("url_includes", []):
        if s.lower() in h: score += 6

    for s in rule.get("text_excludes", []):
        if s.lower() in t: score -= 10
    for s in rule.get("url_excludes", []):
        if s.lower() in h: score -= 10

    # Prefer direct PDFs with plausible sizey names
    if h.endswith(".pdf"): score += 2
    if "volume-1" in h: score += 1
    if "volume-2" in h: score += 1
    return score

def pick_pdf(start_url, selector, rule):
    html = fetch_html(start_url)
    soup = BeautifulSoup(html, "lxml")

    anchors = soup.select(selector)
    if not anchors:
        # fall back: any <a> with .pdf
        anchors = soup.select("a[href$='.pdf']")

    ranked = sorted(anchors, key=lambda a: score_anchor(a, rule), reverse=True)

    for a in ranked:
        href = a.get("href") or ""
        if not href.lower().endswith(".pdf"): 
            continue
        # Hard gate: must not contain excludes
        bad = any(s.lower() in href.lower() for s in rule.get("url_excludes", []))
        if bad:
            continue
        txt = (a.get_text() or "")
        badt = any(s.lower() in txt.lower() for s in rule.get("text_excludes", []))
        if badt:
            continue
        # If rule has includes, ensure at least one match
        url_incl = rule.get("url_includes", [])
        text_incl = rule.get("text_includes", [])
        if url_incl or text_incl:
            ok = False
            if url_incl and any(s.lower() in href.lower() for s in url_incl): ok = True
            if text_incl and any(s.lower() in txt.lower() for s in text_incl): ok = True
            if not ok:
                continue
        return abs_url(start_url, href), txt.strip()

    return None, None

def extract_version(text, url, regex):
    if not regex:
        return None
    m = re.search(regex, text or "", flags=re.I)
    if m:
        return (m.group(0) or "").strip()
    # try from URL
    m = re.search(regex, url or "", flags=re.I)
    if m:
        return (m.group(0) or "").strip()
    return None

def process_rule(path: Path):
    rule = yaml.safe_load(path.read_text(encoding="utf-8"))
    start_url = rule["start_url"]
    selector = rule.get("selector", "a[href$='.pdf']")

    pdf_url, link_text = pick_pdf(start_url, selector, rule)
    if not pdf_url:
        raise RuntimeError(f"No PDF found for rule: {path.name}")

    version = extract_version(link_text, pdf_url, rule.get("version_regex"))
    item = {
        "jurisdiction": rule["jurisdiction"],
        "track": rule["track"],
        "name": rule["name"],
        "issued": version or "",
        "version": "v1.0.0",
        "pdf": pdf_url,
        "history": rule.get("history_url", start_url),
        "source": start_url,
        "match_text": link_text,
        "rule_file": path.name,
    }
    return item

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", required=True, help="rules directory")
    ap.add_argument("--out", required=True, help="output JSON path")
    args = ap.parse_args()

    rules_dir = Path(args.rules)
    rule_files = sorted(rules_dir.glob("*.yml"))
    items = []
    for rf in rule_files:
        try:
            items.append(process_rule(rf))
        except Exception as e:
            print(f"[WARN] {rf.name}: {e}", file=sys.stderr)

    # sort for stable table
    items.sort(key=lambda x: (x["jurisdiction"], x["track"]))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"items": items}, indent=2), encoding="utf-8")
    print(f"Wrote {args.out} with {len(items)} items")

if __name__ == "__main__":
    main()
