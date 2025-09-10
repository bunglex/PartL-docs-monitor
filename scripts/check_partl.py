# Auto-discover Part L / Section 6 / TGD L PDFs (no exact titles required).
# Crawls each collection page -> follows likely detail pages -> picks best PDF,
# archives it, extracts text for diffs, and updates registry/current.json.

import os, re, json, datetime, difflib, pathlib, time, hashlib, tempfile
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup as BS

# ---- Collection pages & tracks (we'll monitor multiple docs per jurisdiction) ----
SITES = [
  # ENGLAND (same collection URL, two tracks)
  { "jurisdiction":"england", "track":"vol1-dwellings",
    "collection":"https://www.gov.uk/government/collections/approved-documents",
    "include":["approved document l","volume 1","dwellings","domestic"],
    "exclude":["volume 2","non-domestic","buildings other than dwellings"] },
  { "jurisdiction":"england", "track":"vol2-non-domestic",
    "collection":"https://www.gov.uk/government/collections/approved-documents",
    "include":["approved document l","volume 2","non-domestic","buildings other than dwellings"],
    "exclude":["volume 1","dwellings","domestic"] },
  # Optional amendments track (England)
  { "jurisdiction":"england", "track":"amendments",
    "collection":"https://www.gov.uk/government/collections/approved-documents",
    "include":["approved document l","amendment","amendments"],
    "exclude":[] },

  # WALES
  { "jurisdiction":"wales", "track":"vol1-dwellings",
    "collection":"https://www.gov.wales/building-regulations-approved-documents",
    "include":["approved document l","volume 1","dwellings","domestic"],
    "exclude":["volume 2","non-domestic"] },
  { "jurisdiction":"wales", "track":"vol2-non-domestic",
    "collection":"https://www.gov.wales/building-regulations-approved-documents",
    "include":["approved document l","volume 2","non-domestic","buildings other than dwellings"],
    "exclude":["volume 1","dwellings","domestic"] },

  # SCOTLAND (domestic/non-domestic handbooks)
  { "jurisdiction":"scotland", "track":"domestic",
    "collection":"https://www.gov.scot/collections/building-standards/",
    "include":["technical handbook","domestic","section 6","energy"],
    "exclude":["non-domestic"] },
  { "jurisdiction":"scotland", "track":"non-domestic",
    "collection":"https://www.gov.scot/collections/building-standards/",
    "include":["technical handbook","non-domestic","section 6","energy"],
    "exclude":["domestic"] },

  # NORTHERN IRELAND (F1/F2)
  { "jurisdiction":"northern_ireland", "track":"f1-dwellings",
    "collection":"https://www.finance-ni.gov.uk/topics/building-regulations/technical-booklets",
    "include":["technical booklet f1","dwellings"],
    "exclude":["f2","buildings other than dwellings","non-domestic"] },
  { "jurisdiction":"northern_ireland", "track":"f2-non-domestic",
    "collection":"https://www.finance-ni.gov.uk/topics/building-regulations/technical-booklets",
    "include":["technical booklet f2","buildings other than dwellings","non-domestic"],
    "exclude":["f1","dwellings","domestic"] },

  # IRELAND (TGD L – dwellings) — add non-domestic later if you like
  { "jurisdiction":"ireland", "track":"dwellings",
    "collection":"https://www.gov.ie/en/publication/07b29-technical-guidance-document-l-conservation-of-fuel-and-energy-dwellings/",
    "include":["technical guidance document l","tgd l","dwellings","part l"],
    "exclude":["non-domestic"] },
  # { "jurisdiction":"ireland", "track":"non-domestic", ... }  # can be added later
]


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/123.0 Safari/537.36 PartL-monitor/1.0")
HEADERS = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-GB,en;q=0.9"}

def log(msg): print(msg, flush=True)
def slug(s):
  s = re.sub(r"[^a-z0-9]+","-", s.lower()).strip("-")
  return re.sub(r"-{2,}","-", s)
def sha256(b): return hashlib.sha256(b).hexdigest()

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
      last = e
      time.sleep(1.5*(i+1))
  log(f"[WARN] fetch failed: {url} — {last}")
  return None

def soup(html):
  # no lxml dependency; fallback is fine for these pages
  return BS(html, "html.parser")

def score(text, href, include, exclude):
  t = (text or "").lower(); h = (href or "").lower(); s = 0
  for w in include:
    if w in t or w in h: s += 2
  for w in exclude:
    if w in t or w in h: s -= 3
  if h.endswith(".pdf") or ".pdf?" in h or "download" in h: s += 3
  return s

def detail_candidates(coll_html, base, include, exclude):
  cand = []
  for a in soup(coll_html).find_all("a", href=True):
    sc = score(a.get_text(), a["href"], include, exclude)
    if sc > 0:
      cand.append((sc, urljoin(base, a["href"])))
  # unique & best-first
  seen, out = set(), []
  for sc, u in sorted(cand, key=lambda x: -x[0]):
    if u not in seen:
      seen.add(u); out.append(u)
  return out[:8]

def find_pdf_on_page(detail_html, base, include, exclude):
  best, best_sc = None, -999
  for a in soup(detail_html).find_all("a", href=True):
    href = a["href"]
    sc = score(a.get_text(), href, include, exclude)
    if href.lower().endswith(".pdf") or "download" in href.lower() or sc >= 2:
      full = urljoin(base, href)
      if sc > best_sc:
        best_sc, best = sc, full
  return best

def pdf_text(pdf_bytes):
  try:
    from pdfminer.high_level import extract_text
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
      f.write(pdf_bytes); path = f.name
    try:
      return extract_text(path) or ""
    finally:
      try: os.remove(path)
      except: pass
  except Exception as e:
    log(f"[WARN] pdf_text failed: {e}")
    return ""

def derive_name(detail_html, pdf_txt, jurisdiction):
  s = soup(detail_html)
  h1 = s.find("h1")
  if h1 and h1.get_text(strip=True):
    return h1.get_text(strip=True)
  for line in (pdf_txt.splitlines()[:40]):
    L = line.strip()
    if len(L) > 8 and any(k in L.lower() for k in
        ["approved document","technical handbook","technical booklet","guidance","section 6","part l","dwellings"]):
      return L
  return f"{jurisdiction.title()} energy efficiency document"

def extract_numbers(text):
  return sorted(re.findall(r"(roof|wall|floor|window|roof ?light|rooflight|door)\D{0,12}([0-9]\.[0-9]{1,2})", text, re.I))

def load_registry():
  try:
    return json.load(open("registry/current.json","r",encoding="utf-8"))
  except Exception:
    return []

def save_registry(reg):
  pathlib.Path("registry").mkdir(parents=True, exist_ok=True)
  json.dump(reg, open("registry/current.json","w",encoding="utf-8"), indent=2)

def ensure(*paths):
  for p in paths: pathlib.Path(p).mkdir(parents=True, exist_ok=True)

today = datetime.date.today().isoformat()
registry = load_registry()
changed = []

for site in SITES:
  j = site["jurisdiction"]; coll = site["collection"]
  include = site.get("include", []); exclude = site.get("exclude", [])
  log(f"\n=== {j.upper()} ===")
  rs = fetch(coll)
  if not rs:
    log("[WARN] cannot load collection"); continue
  cands = detail_candidates(rs.text, coll, include, exclude)
  log(f"[INFO] detail candidates: {len(cands)}")
  pdf_url, detail_html = None, None
  for u in cands:
    rr = fetch(u)
    if not rr: continue
    detail_html = rr.text
    pdf = find_pdf_on_page(detail_html, u, include, exclude)
    log(f"  tried {u} → pdf={bool(pdf)}")
    if pdf:
      pdf_url = pdf; break
  if not pdf_url:
    log("[INFO] no pdf found; skip"); continue

  rp = fetch(pdf_url)
  if not rp: continue
  pdf_url = rp.url  # canonical after redirects
  pdf_bytes = rp.content
  checksum = "sha256:" + sha256(pdf_bytes)
  txt = pdf_text(pdf_bytes)
  name = derive_name(detail_html or "", txt, j)
  numbers = extract_numbers(txt)

  sl = slug(f"{j}-{site.get('track') or name}")
  arch = f"archive/{j}/{sl}"; txtdir = f"texts/{sl}"; difdir = f"diffs/{sl}"
  ensure(arch, txtdir, difdir, "docs/history")

  entry = next((x for x in registry if x["jurisdiction"]==j and x["slug"]==sl), None)
  if not entry:
    entry = {"jurisdiction":j, "name":name, "slug":sl, "source_url":pdf_url,
             "checksum":checksum, "numbers":numbers,
             "first_seen":today, "last_seen":today, "rule_version":"v1.0.0",
             "history":[]}
    registry.append(entry)
    open(f"{arch}/{today}.pdf","wb").write(pdf_bytes)
    open(f"{txtdir}/latest.txt","w",encoding="utf-8").write(txt)
    entry["history"].append({"date":today,"source_url":pdf_url,"checksum":checksum,
                             "numbers":numbers,"archived_pdf":f"{arch}/{today}.pdf","diff":None})
    changed.append((sl,"NEW"))
    log("[INFO] NEW")
  else:
    major = (entry["source_url"] != pdf_url)
    minor = (entry["checksum"] != checksum)
    numeric = (entry.get("numbers") != numbers)
    if major or minor or numeric:
      prev = open(f"{txtdir}/latest.txt",encoding="utf-8").read() if os.path.exists(f"{txtdir}/latest.txt") else ""
      diff = list(difflib.unified_diff(prev.splitlines(1), txt.splitlines(1),
                                       fromfile="previous", tofile="current"))
      open(f"{difdir}/{today}.patch","w",encoding="utf-8").writelines(diff)
      open(f"{arch}/{today}.pdf","wb").write(pdf_bytes)
      open(f"{txtdir}/latest.txt","w",encoding="utf-8").write(txt)
      # bump version (major > minor > patch)
      v = entry.get("rule_version","v1.0.0")[1:].split("."); v = [int(x) for x in v] + [0]*(3-len(v))
      if major:   v = [v[0]+1, 0, 0]; kind="MAJOR"
      elif numeric: v = [v[0], v[1]+1, 0]; kind="NUMERIC"
      else:      v = [v[0], v[1], v[2]+1]; kind="MINOR"
      entry.update({"name":name,"source_url":pdf_url,"checksum":checksum,"numbers":numbers,
                    "last_seen":today,"rule_version":f"v{v[0]}.{v[1]}.{v[2]}"})
      entry["history"].append({"date":today,"source_url":pdf_url,"checksum":checksum,
                               "numbers":numbers,"archived_pdf":f"{arch}/{today}.pdf",
                               "diff":f"{difdir}/{today}.patch"})
      changed.append((sl,kind))
      log(f"[INFO] CHANGE: {kind}")
    else:
      entry["last_seen"] = today
      log("[INFO] No change")

save_registry(registry)
if changed:
  with open("CHANGELOG.md","a",encoding="utf-8") as f:
    for sl,kind in changed:
      f.write(f"{today}: {sl} — {kind}\n")
else:
  log("[INFO] No new/changed docs.")
