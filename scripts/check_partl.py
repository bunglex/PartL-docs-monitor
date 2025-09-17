#!/usr/bin/env python3
"""
check_partl.py
Crawls rule files and writes a machine-readable registry JSON.

Usage:
  python scripts/check_partl.py --rules rules --out registry/current.json
  (optional) --expected 8   # fail loudly if we fetch too few items
"""

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import yaml


# ---------- helpers ----------

def abs_url(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, href)


def fetch_html(url: str) -> str:
    r = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "PartL-Docs-Monitor/1.0 (+github)"},
    )
    r.raise_for_status()
    return r.text


def extract_version(text: str, url: str, regex: str | None) -> str | None:
    if not regex:
        return None
    m = re.search(regex, text or "", flags=re.I)
    if m:
        return (m.group(0) or "").strip()
    m = re.search(regex, url or "", flags=re.I)
    if m:
        return (m.group(0) or "").strip()
    return None


def _norm_list(v, default=None):
    if v is None:
        return default or []
    if isinstance(v, str):
        return [v]
    return list(v)


def _contains_all(hay: str, needles) -> bool:
    needles = _norm_list(needles)
    return all(n.lower() in hay.lower() for n in needles) if needles else True


def _contains_any(hay: str, needles) -> bool:
    needles = _norm_list(needles)
    return any(n.lower() in hay.lower() for n in needles) if needles else False


def score_anchor(a, rule) -> int:
    """Light heuristic to rank anchors before hard filters."""
    text = (a.get_text() or "").strip()
    href = (a.get("href") or "").lower()
    t = text.lower()

    url_inc_any = _norm_list(rule.get("url_includes")) + _norm_list(rule.get("url_includes_any"))
    text_inc_any = _norm_list(rule.get("text_includes")) + _norm_list(rule.get("text_includes_any"))

    score = 0
    for s in url_inc_any:
        if s.lower() in href:
            score += 6
    for s in text_inc_any:
        if s.lower() in t:
            score += 5

    if href.endswith(".pdf"):
        score += 2
    if "volume-1" in href:
        score += 1
    if "volume-2" in href:
        score += 1
    return score


# ---------- core ----------

def pick_pdf(start_url: str, selector: str, rule: dict) -> tuple[str | None, str | None]:
    html = fetch_html(start_url)
    soup = BeautifulSoup(html, "lxml")

    anchors = soup.select(selector) or soup.select("a[href$='.pdf']") or soup.find_all("a")
    ranked = sorted(anchors, key=lambda a: score_anchor(a, rule), reverse=True)

    # Gather rule constraints (support both old and new key styles)
    url_inc_all = _norm_list(rule.get("url_includes_all"))
    url_inc_any = _norm_list(rule.get("url_includes")) + _norm_list(rule.get("url_includes_any"))
    url_exc_any = _norm_list(rule.get("url_excludes")) + _norm_list(rule.get("url_excludes_any"))

    txt_inc_all = _norm_list(rule.get("text_includes_all"))
    txt_inc_any = _norm_list(rule.get("text_includes")) + _norm_list(rule.get("text_includes_any"))
    txt_exc_any = _norm_list(rule.get("text_excludes")) + _norm_list(rule.get("text_excludes_any"))

    for a in ranked:
        href = a.get("href") or ""
        text = (a.get_text() or "")

        if not href.lower().endswith(".pdf"):
            continue

        # Hard rejections
        if _contains_any(href, url_exc_any) or _contains_any(text, txt_exc_any):
            continue

        # Must satisfy ALL includes if provided
        if not _contains_all(href, url_inc_all):
            continue
        if not _contains_all(text, txt_inc_all):
            continue

        # If any-include lists are present, require at least one match (URL or text separately)
        if url_inc_any and not _contains_any(href, url_inc_any):
            continue
        if txt_inc_any and not _contains_any(text, txt_inc_any):
            continue

        return abs_url(start_url, href), text.strip()

    return None, None


def process_rule(path: Path) -> dict:
    rule = yaml.safe_load(path.read_text(encoding="utf-8"))

    start_url = rule["start_url"]
    selector = rule.get("selector", "a[href$='.pdf']")

    pdf_url, link_text = pick_pdf(start_url, selector, rule)
    if not pdf_url:
        raise RuntimeError("no matching PDF found")

    issued = extract_version(link_text, pdf_url, rule.get("version_regex"))

    return {
        "jurisdiction": rule["jurisdiction"],
        "track": rule["track"],
        "name": rule["name"],
        "issued": issued or "",
        "version": "v1.0.0",
        "pdf": pdf_url,
        "history": rule.get("history_url", start_url),
        "source": start_url,
        "match_text": link_text or "",
        "rule_file": path.name,
    }


def main():
    ap = argparse.ArgumentParser(description="Crawl Part L rules and write current.json")
    ap.add_argument("--rules", required=True, help="Directory containing *.yml rule files")
    ap.add_argument("--out", required=True, help="Path to output JSON (e.g., registry/current.json)")
    ap.add_argument("--expected", type=int, default=0, help="Optional: expected item count")
    args = ap.parse_args()

    rules_dir = Path(args.rules)
    rule_files = sorted(rules_dir.glob("*.yml"))

    items = []
    for rf in rule_files:
        try:
            item = process_rule(rf)
            print(f"[OK] {rf.name} -> {item['pdf']}")
            items.append(item)
        except Exception as e:
            print(f"[MISS] {rf.name}: {e}", file=sys.stderr)

    items.sort(key=lambda x: (x["jurisdiction"], x["track"]))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"items": items}, indent=2), encoding="utf-8")

    print(f"[SUMMARY] wrote {out_path} with {len(items)} items from {len(rule_files)} rules")
    if args.expected and len(items) < args.expected:
        print(f"[ERROR] too few items: {len(items)} (expected ~{args.expected})", file=sys.stderr)
        # Non-zero exit to flag CI
        sys.exit(2)


if __name__ == "__main__":
    main()
