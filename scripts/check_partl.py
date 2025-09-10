# Robust checker: 2-hop crawl (collection -> detail page -> PDF), archive + diff
import os, re, json, datetime, difflib, pathlib, time, hashlib, tempfile, sys
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup as BS

# ---- Sites (collection pages) ----
SITES = [
  {
    "jurisdiction": "england",
    "name": "Part L Vol.1 (Dwellings)",
    "collection": "https://www.gov.uk/government/collections/approved-documents",
    # words that should appear in the *detail page link* text or URL
    "detail_patterns": [r"approved document l.*volume ?1", r"part l.*dwellings"],
  },
  {
    "jurisdiction": "wales",
    "name": "Part L Vol.1 (Dwellings)",
    "collection": "https://www.gov.wales/building-regulations-approved-documents",
    "detail_patterns": [r"approved document l.*volume ?1", r"part l.*dwellings"],
  },
  {
    "jurisdiction": "scotland",
    "name": "Technical Handbook (Domestic) – Section 6 Energy",
    "collection": "https://www.gov.scot/collections/building-standards/",
    "detail_patterns": [r"technical handbook.*domestic", r"section *6.*energy"],
  },
  {
    "jurisdiction": "northern_ireland",
    "name": "Technical Booklet F1 – Dwellings",
    "collection": "https://www.finance-ni.gov.uk/topics/building-regulations/technical-booklets",
    "detail_patterns": [r"technical booklet f1", r"booklet f1.*dwellings"],
  },
  {
    "jurisdiction": "ireland",
    "name": "TGD L – Dwellings (Part L)",
    "collection": "https://www.gov.ie/en/publication/07b29-technical-guidance-document-l-conservation-of-fuel-and-energy-dwellings/",
    "detail_patterns": [r"technical guidance document l", r"tgd l.*dwellings"],
  },
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/123.0 Safari/537.36 PartL-monitor/1.0")
HEADERS = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-GB,en;q=0.9"}

def log(msg): print(msg, flush=True)

def slugify(s):
  return re.sub(r"-{2,}", "-", re.sub(r"[^a-zA-Z0-9]+", "-", s.strip().lower())).strip("-")

def sha256_bytes(b): return hashlib.sha256(b).hexdigest()

def fetch(url, base=None, retries=3, stream=False):
  if base and not url.startswith(("http://","https://")):
    url = urljoin(base, url)
  last = None
  for i in range(retries):
    try:
      r = requests.get(url, headers=HEADERS, timeout=60, allow_redirects=True, stream=stream)
      r.raise_for_status()
      return r
    except Exception as e:
      last = e; time.sleep(1.5*(i+1))
  log(f"[WARN] fetch failed: {url} — {last}")
  return None

def soup_of(html):
  # Use built-in parser; no lxml dependency
  return BS(html, "html.parser")

def find_detail_links(collection_html, base_url, patterns):
  """Return candidate detail-page URLs matching any of the regex patterns."""
  s = soup_of(collection_html)
  links = []
  for a in s.find_all("a", href=True):
    text = (a.get_text() or "").strip().lower()
    href = a["href"].lower()
    url = urljoin(base_url, a["href"])
    for pat in patterns:
      if re.search(pat, text, re.I) or re.search(pat, href, re.I):
        links.append(url)
        break
  # dedupe preserving order
  seen, out = set(), []
  for u in links:
    if u not in seen:
      seen.add(u); out.append(u)
  return out[:6]  # safety cap

def find_pdf_on_page(html, base_url):
  """Return first plausible PDF URL from a detail page."""
  s = soup_of(html)
  # Prefer true .pdf links or links that look like downloads
  for a in s.find_all("a", href=True):
    href = a["href"]
    lh = href.lower()
    if lh.endswith(".pdf") or ".pdf?" in lh or "download" in lh or "attachment" in lh:
      return urljoin(base_url, href)
  return None

def pdf_to_text(pdf_bytes):
  try:
    from pdfminer.high_level import extract_text
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
      f.write(pdf_bytes); path=f.name
    try:
      return extract_text(path) or ""
    finally:
      try: os.remove(path)
      except: pass
  except Exception as e:
    log(f"[WARN] pdf_to_text failed: {e}")
    return ""

def extract_numbers(text):
  return sorted(re.findall(r"(roof|wall|floor|window|roof ?light|rooflight|door)\D{0,12}([0-9]\.[0-9]{1,2})", text, re.I))

def load_registry():
  p = "registry/current.json"
  try:
    return json.load(open(p, encoding="utf-8"))
  except Exception:
    return []

def save_registry(data):
  pathlib.Path("registry").mkdir(parents=True, exist_ok=True)
  json.dump(data, open("registry/current.json","w",encoding="utf-8"), indent=2)

def ensure_dirs(*paths):
  for p in paths:
    pathlib.Path(p).mkdir(parents=True, exist_ok=True)

today = datetime.date.today().isoformat()
registry = load_registry()
changed = []

for site in SITES:
  j, name, coll = site["jurisdiction"], site["name"], site["collection"]
  slug = slugify(f"{j}-{name}")
  log(f"\n=== {j.upper()} :: {name} ===")
  r = fetch(coll)
  if not r:
    log(f"[WARN] cannot load collection: {coll}")
    continue
  cand = find_detail_links(r.text, coll, site["detail_patterns"])
  log(f"[INFO] detail candidates: {len(cand)}")
  pdf_url = None
  for i, link in enumerate(cand, 1):
    rr = fetch(link)
    if not rr: 
      continue
    pdf = find_pdf_on_page(rr.text, link)
    log(f"  - cand {i}/{len(cand)} → pdf: {bool(pdf)} ({link})")
    if pdf:
      pdf_url = pdf
      break

  if not pdf_url:
    log("[INFO] no pdf found yet; skipping")
    continue

  # fetch PDF, compute checksum & text
  rpdf = fetch(pdf_url)
  if not rpdf:
    continue
  pdf_url = rpdf.url  # canonical after redirects
  pdf_bytes = rpdf.content
  checksum = "sha256:" + sha256_bytes(pdf_bytes)
  text = pdf_to_text(pdf_bytes)
  numbers = extract_numbers(text)

  arch_dir = f"archive/{j}/{slug}"
  txt_dir  = f"texts/{slug}"
  dif_dir  = f"diffs/{slug}"
  ensure_dirs(arch_dir, txt_dir, dif_dir, "docs/history")

  # upsert registry entry
  entry = next((x for x in registry if x["jurisdiction"]==j and x["name"]==name), None)
  if not entry:
    entry = {
      "jurisdiction": j, "name": name, "slug": slug,
      "source_url": pdf_url, "checksum": checksum, "numbers": numbers,
      "first_seen": today, "last_seen": today, "rule_version": "v1.0.0",
      "history": []
    }
    registry.append(entry)
    open(f"{arch_dir}/{today}.pdf","wb").write(pdf_bytes)
    open(f"{txt_dir}/latest.txt","w",encoding="utf-8").write(text)
    entry["history"].append({
      "date": today, "source_url": pdf_url, "checksum": checksum,
      "numbers": numbers, "archived_pdf": f"{arch_dir}/{today}.pdf", "diff": None
    })
    changed.append((slug,"NEW"))
    log("[INFO] NEW entry created")
  else:
    major = (entry["source_url"] != pdf_url)
    minor = (entry["checksum"] != checksum)
    numeric = (entry.get("numbers") != numbers)
    if major or minor or numeric:
      prev_text = open(f"{txt_dir}/latest.txt", encoding="utf-8").read() if os.path.exists(f"{txt_dir}/latest.txt") else ""
      diff = list(difflib.unified_diff(prev_text.splitlines(1), text.splitlines(1), fromfile="previous", tofile="current"))
      diff_path = f"{dif_dir}/{today}.patch"
      open(diff_path,"w",encoding="utf-8").writelines(diff)
      open(f"{arch_dir}/{today}.pdf","wb").write(pdf_bytes)
      open(f"{txt_dir}/latest.txt","w",encoding="utf-8").write(text)

      rv = entry.get("rule_version","v1.0.0")
      def bump(v, pos):
        parts = [int(x) for x in v[1:].split(".")]
        parts += [0]*(3-len(parts)); parts[pos]+=1
        if pos<2: parts[pos+1:]=[0]*(2-pos)
        return f"v{parts[0]}.{parts[1]}.{parts[2]}"
      if major: rv, kind = bump(rv,0), "MAJOR"
      elif numeric: rv, kind = bump(rv,1), "NUMERIC"
      else: rv, kind = bump(rv,2), "MINOR"

      entry.update({"source_url":pdf_url, "checksum":checksum, "numbers":numbers,
                    "last_seen":today, "rule_version":rv})
      entry["history"].append({
        "date": today, "source_url": pdf_url, "checksum": checksum, "numbers": numbers,
        "archived_pdf": f"{arch_dir}/{today}.pdf", "diff": diff_path
      })
      changed.append((slug,kind))
      log(f"[INFO] CHANGE recorded:
