# --- CHECKER: fetch, detect, archive, diff, version bump (robust) ---
import os, re, json, datetime, difflib, pathlib, time, hashlib, tempfile
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup as BS

ENG_COLL = "https://www.gov.uk/government/collections/approved-documents"
WAL_COLL = "https://www.gov.wales/building-regulations-approved-documents"
SCT_COLL = "https://www.gov.scot/collections/building-standards/"
NI_COLL  = "https://www.finance-ni.gov.uk/topics/building-regulations/technical-booklets"
ROI_COLL = "https://www.gov.ie/en/publication/07b29-technical-guidance-document-l-conservation-of-fuel-and-energy-dwellings/"

DOCS = [
  {"jurisdiction":"england","name":"Part L Vol.1 (Dwellings)","pattern":r"Approved Document L.*Volume 1","collection": ENG_COLL},
  {"jurisdiction":"wales","name":"Part L Vol.1 (Dwellings)","pattern":r"Approved Document L.*Volume 1","collection": WAL_COLL},
  {"jurisdiction":"scotland","name":"Technical Handbook (Domestic) – Section 6 Energy","pattern":r"(Technical Handbook).*(Domestic).*6.*Energy","collection": SCT_COLL},
  {"jurisdiction":"northern_ireland","name":"Technical Booklet F1 – Dwellings","pattern":r"(Technical Booklet F1).*(dwellings)","collection": NI_COLL},
  {"jurisdiction":"ireland","name":"TGD L – Dwellings (Part L)","pattern":r"(Technical Guidance Document L).*(Dwellings)","collection": ROI_COLL},
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/123.0 Safari/537.36 PartL-monitor/1.0")
HEADERS = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-GB,en;q=0.9"}

def slugify(s:str)->str:
  s=re.sub(r"[^a-zA-Z0-9]+","-",s.strip().lower()).strip("-")
  return re.sub(r"-{2,}","-",s)

def sha256_bytes(b:bytes)->str:
  return hashlib.sha256(b).hexdigest()

def fetch(url, base=None, retries=3, stream=False):
  if base and not url.startswith("http"):
    url = urljoin(base, url)
  last_err=None
  for i in range(retries):
    try:
      r = requests.get(url, headers=HEADERS, timeout=60, allow_redirects=True, stream=stream)
      r.raise_for_status()
      return r
    except Exception as e:
      last_err=e
      time.sleep(2*(i+1))
  print(f"[WARN] fetch failed after retries: {url} — {last_err}")
  return None

def find_pdf_links(html, pattern):
  out=[]
  s=BS(html, "lxml")
  for a in s.select('a[href$=".pdf"]'):
    t=(a.get_text() or "").strip()
    h=a.get("href","")
    if re.search(pattern, t, re.I) or re.search(pattern, h, re.I):
      out.append(h)
  return out

def pdf_to_text(pdf_bytes:bytes)->str:
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
    print(f"[WARN] pdf_to_text failed: {e}")
    return ""

def extract_numbers(text):
  return sorted(re.findall(r"(roof|wall|floor|window|roof ?light|rooflight|door)\D{0,12}([0-9]\.[0-9]{1,2})", text, re.I))

def load_registry():
  p="registry/current.json"
  return json.load(open(p)) if os.path.exists(p) else []

def save_registry(data):
  os.makedirs("registry", exist_ok=True)
  json.dump(data, open("registry/current.json","w"), indent=2)

def ensure_dirs(*paths):
  for p in paths:
    pathlib.Path(p).mkdir(parents=True, exist_ok=True)

today = datetime.date.today().isoformat()
registry = load_registry()
changed_rows = []

# Pre-fetch collection pages (skip ones that fail)
collections = {
  "england": ENG_COLL, "wales": WAL_COLL, "scotland": SCT_COLL,
  "northern_ireland": NI_COLL, "ireland": ROI_COLL
}
collections_html={}
for k,u in collections.items():
  r = fetch(u)
  if r is None:
    print(f"[WARN] could not load collection: {k} {u}")
  else:
    collections_html[k]=r.text

for d in DOCS:
  html = collections_html.get(d["jurisdiction"])
  if not html:
    continue
  links = find_pdf_links(html, d["pattern"])
  if not links:
    print(f"[INFO] no PDF links matched for {d['jurisdiction']} / {d['name']}")
    continue

  base = d["collection"]
  r = fetch(links[0], base=base)
  if r is None:
    continue
  pdf_url = r.url  # canonical after redirects
  r2 = fetch(pdf_url)
  if r2 is None:
    continue
  pdf_bytes = r2.content
  checksum = "sha256:"+sha256_bytes(pdf_bytes)
  text = pdf_to_text(pdf_bytes)
  numbers = extract_numbers(text)

  slug = slugify(f"{d['jurisdiction']}-{d['name']}")
  arch_dir, text_dir, diff_dir = f"archive/{d['jurisdiction']}/{slug}", f"texts/{slug}", f"diffs/{slug}"
  ensure_dirs(arch_dir, text_dir, diff_dir, "docs", "docs/history")

  entry = next((x for x in registry if x["jurisdiction"]==d["jurisdiction"] and x["name"]==d["name"]), None)
  if not entry:
    entry = {
      "jurisdiction": d["jurisdiction"], "name": d["name"], "slug": slug,
      "source_url": pdf_url, "checksum": checksum, "numbers": numbers,
      "first_seen": today, "last_seen": today, "rule_version": "v1.0.0",
      "history": []
    }
    registry.append(entry)
    open(f"{arch_dir}/{today}.pdf","wb").write(pdf_bytes)
    open(f"{text_dir}/latest.txt","w",encoding="utf-8").write(text)
    entry["history"].append({
      "date": today, "source_url": pdf_url, "checksum": checksum,
      "numbers": numbers, "archived_pdf": f"{arch_dir}/{today}.pdf", "diff": None
    })
    changed_rows.append((slug, "NEW_DOC"))
    continue

  major = (entry["source_url"] != pdf_url)
  minor = (entry["checksum"] != checksum)
  numeric_change = (entry.get("numbers") != numbers)

  if major or minor:
    prev_path = f"{text_dir}/latest.txt"
    prev = open(prev_path,encoding="utf-8").read() if os.path.exists(prev_path) else ""
    diff = list(difflib.unified_diff(prev.splitlines(1), text.splitlines(1),
                                     fromfile="previous", tofile="current"))
    diff_path = f"{diff_dir}/{today}.patch"
    open(diff_path,"w",encoding="utf-8").writelines(diff)
    pdf_path = f"{arch_dir}/{today}.pdf"
    open(pdf_path,"wb").write(pdf_bytes)
    open(prev_path,"w",encoding="utf-8").write(text)

    rv = entry.get("rule_version","v1.0.0")
    major_bump = lambda v: f"v{int(v[1:].split('.')[0])+1}.0.0"
    minor_bump = lambda v: f"v{v[1:].split('.')[0]}.{int(v.split('.')[1])+1}.0"
    patch_bump = lambda v: f"v{v[1:].split('.')[0]}.{v.split('.')[1]}.{int(v.split('.')[-1])+1}"
    if major: rv, kind = major_bump(rv), "MAJOR"
    elif numeric_change: rv, kind = minor_bump(rv), "NUMERIC"
    else: rv, kind = patch_bump(rv), "MINOR"

    entry.update({
      "source_url": pdf_url, "checksum": checksum, "numbers": numbers,
      "last_seen": today, "rule_version": rv
    })
    entry["history"].append({
      "date": today, "source_url": pdf_url, "checksum": checksum, "numbers": numbers,
      "archived_pdf": pdf_path, "diff": diff_path
    })
    changed_rows.append((slug, kind))

save_registry(registry)

if changed_rows:
  with open("CHANGELOG.md","a",encoding="utf-8") as f:
    for slug,kind in changed_rows:
      f.write(f"{today}: {slug} — {kind} change detected\n")
else:
  print("[INFO] No changes detected (initial population may still have run).")
