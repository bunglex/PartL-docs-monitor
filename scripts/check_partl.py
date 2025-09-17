#!/usr/bin/env python3
"""
check_partl.py — crawl rule files and write registry/current.json
Two-hop logic: start page -> (if needed) follow best-matching child pages, then pick PDF.
"""

import argparse, json, re, sys, urllib.parse
from pathlib import Path
from typing import List, Tuple, Optional
import requests
from bs4 import BeautifulSoup
import yaml


# ---------------- utils ----------------

def abs_url(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, href)

def domain_of(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower()

def fetch_html(url: str) -> str:
    r = requests.get(url, timeout=30, headers={"User-Agent": "PartL-Docs-Monitor/1.0"})
    r.raise_for_status()
    return r.text

def _norm_list(v):
    if not v:
        return []
    return v if isinstance(v, list) else [v]

def contains_any(hay: str, needles: List[str]) -> bool:
    hay = hay.lower()
    return any(n.lower() in hay for n in needles) if needles else False

def contains_all(hay: str, needles: List[str]) -> bool:
    hay = hay.lower()
    return all(n.lower() in hay for n in needles) if needles else True

def extract_version(text: str, url: str, regex: Optional[str]) -> Optional[str]:
    if not regex:
        return None
    m = re.search(regex, text or "", flags=re.I)
    if m: return (m.group(0) or "").strip()
    m = re.search(regex, url or "", flags=re.I)
    if m: return (m.group(0) or "").strip()
    return None


# ---------------- scoring ----------------

def score_link(text: str, href: str, rule: dict) -> int:
    t = (text or "").lower()
    h = (href or "").lower()

    url_inc_any = _norm_list(rule.get("url_includes_any")) + _norm_list(rule.get("url_includes"))
    txt_inc_any = _norm_list(rule.get("text_includes_any")) + _norm_list(rule.get("text_includes"))

    score = 0
    for s in url_inc_any:
        if s.lower() in h: score += 6
    for s in txt_inc_any:
        if s.lower() in t: score += 5

    if h.endswith(".pdf"): score += 3
    if "volume-1" in h: score += 1
    if "volume-2" in h: score += 1
    return score


# ---------------- core finders ----------------

def find_pdf_on_page(page_url: str, soup: BeautifulSoup, rule: dict,
                     context_text: str = "", top_n: int = 50) -> Tuple[Optional[str], Optional[str]]:
    """Search the current page for PDFs, filtered by rule + context (from the link that led here)."""
    anchors = soup.select("a[href$='.pdf']") or []
    anchors = anchors[:top_n]
    url_exc_any = _norm_list(rule.get("url_excludes_any")) + _norm_list(rule.get("url_excludes"))
    txt_exc_any = _norm_list(rule.get("text_excludes_any")) + _norm_list(rule.get("text_excludes"))
    url_inc_any = _norm_list(rule.get("url_includes_any")) + _norm_list(rule.get("url_includes"))
    txt_inc_any = _norm_list(rule.get("text_includes_any")) + _norm_list(rule.get("text_includes"))

    # allow context text (e.g., child link said "Volume 1") to satisfy text includes
    context = context_text or ""

    ranked = sorted(
        anchors,
        key=lambda a: score_link(a.get_text() or "", a.get("href") or "", rule),
        reverse=True,
    )

    for a in ranked:
        href = a.get("href") or ""
        text = a.get_text() or ""

        if contains_any(href, url_exc_any) or contains_any(text, txt_exc_any):
            continue

        # Must match at least one include across URL/text/context if includes are provided.
        any_tokens = (url_inc_any or txt_inc_any)
        if any_tokens:
            any_ok = (contains_any(href, url_inc_any) or
                      contains_any(text, txt_inc_any) or
                      contains_any(context, txt_inc_any))
            if not any_ok:
                continue

        return abs_url(page_url, href), text.strip()

    return None, None


def hop_then_find_pdf(start_url: str, rule: dict, hop_limit: int = 5) -> Tuple[Optional[str], Optional[str]]:
    """
    1) Try to find PDF on start_url.
    2) If none, follow up to hop_limit best non-PDF links that look like the intended volume page.
       Only follow links on the same domain.
    """
    start_html = fetch_html(start_url)
    start_soup = BeautifulSoup(start_html, "lxml")

    # Try direct PDFs first
    pdf, txt = find_pdf_on_page(start_url, start_soup, rule)
    if pdf:
        return pdf, txt

    # Otherwise, pick candidate child pages (not PDFs) and follow
    netloc = domain_of(start_url)
    candidates = []
    for a in start_soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf"):
            continue
        dest = abs_url(start_url, href)
        if domain_of(dest) != netloc:
            continue  # stay on same domain
        score = score_link(a.get_text() or "", href, rule)
        candidates.append((score, dest, a.get_text() or ""))

    candidates.sort(reverse=True)
    for score, dest, text in candidates[:hop_limit]:
        try:
            html = fetch_html(dest)
        except Exception:
            continue
        soup = BeautifulSoup(html, "lxml")
        pdf, txt = find_pdf_on_page(dest, soup, rule, context_text=text)
        if pdf:
            return pdf, txt

    return None, None


def process_rule(path: Path) -> dict:
    rule = yaml.safe_load(path.read_text(encoding="utf-8"))
    start_url = rule["start_url"]

    pdf_url, link_text = hop_then_find_pdf(start_url, rule)
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--expected", type=int, default=0)
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

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"items": items}, indent=2), encoding="utf-8")
    print(f"[SUMMARY] wrote {out} with {len(items)} items from {len(rule_files)} rules")

    if args.expected and len(items) < args.expected:
        print(f"[ERROR] too few items: {len(items)} (expected ~{args.expected})", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
