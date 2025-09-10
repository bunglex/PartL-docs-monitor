# Discovery-mode monitor for energy regs docs (Part L / Section 6 / TGD L / F1-F2).
# Give only jurisdiction seed "collection" pages. The script discovers detail pages,
# finds PDFs, auto-classifies (track), extracts an issued date, archives + diffs,
# and writes registry/current.json used by the site.

import os, re, json, datetime, difflib, pathlib, time, hashlib, tempfile, contextlib, io
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup as BS

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/123.0 Safari/537.36 PartL-monitor/1.0")
HEADERS = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-GB,en;q=0.9"}

# ---- Only seed pages; no need to list volumes etc. ----
SITES = [
  {"jurisdiction":"england",          "collection":"https://www.gov.uk/government/collections/approved-documents"},
  {"jurisdiction":"wales",            "collection":"https://www.gov.wales/building-regulations-approved-documents"},
  {"jurisdiction":"scotland",         "collection":"https://www.gov.scot/collections/building-standards/"},
  {"jurisdiction":"northern_ireland", "collection":"https://www.finance-ni.gov.uk/topics/building-regulations/technical-booklets"},
  {"jurisdiction":"ireland",          "collection":"https://www.gov.ie/en/publication/07b29-technical-guidance-document-l-conservation-of-fuel-and-energy-dwellings/"},
]

def log(m): print(m, flush=True)
def slug(s):
  s = re.sub(r"[^a-z0-9]+","-", s.lower()).strip("-")
  return re.sub(r"-{2,}","-", s)
def sha256(b): import hashlib; return hashlib.sha256(b).hexdigest()

def fetch(url, base=None, retries=3, stream=False):
  if base and not url.startswith(("http://","https://")):
    url = urljoin(base, url)
  last=None
  for i in range(retries):
    try:
      r = requests.get(url, headers=HEADERS, timeout=60, allow_redirects=True, stream=stream)
      r.raise_for_status()
      return r
    except Exception as e:
      last=e; time.sleep(1.5*(i+1))
  log(f"[WARN] fetch failed: {url} — {last}")
  return None

def soup(html):  # no lxml required
  return BS(html, "html.parser")

# -------------- Classification & date extraction ----------------

INCLUDE = {
  "england": ["approved document l","part l","conservation of fuel"],
  "wales": ["approved document l","part l","conservation of fuel"],
  "scotland": ["technical handbook","section 6","energy"],
  "northern_ireland": ["technical booklet f","conservation of fuel"],
  "ireland": ["technical guidance document l","tgd l","part l","dwellings"],
}
EXCLUDE = {
  "england": [],
  "wales": [],
  "scotland": [],
  "northern_ireland": [],
  "ireland": ["non-domestic"],  # for now (we seeded the dwellings page)
}

def score_for(j, text, href):
  t=(text or "").lower(); h=(href or "").lower(); s=0
  for w in INCLUDE.get(j, []):
    if w in t or w in h: s+=2
  for w in EXCLUDE.get(j, []):
    if w in t or w in h: s-=3
  if h.endswith(".pdf") or ".pdf?" in h or "download" in h: s+=3
  return s

def classify_track(j, text, href):
  """Return (track_id, track_title) using lightweight rules."""
  tt=(text or "").lower(); hh=(href or "").lower()
  def pick(a,b): return (a,b)
  if j in ("england","wales"):
    if "volume 1" in tt or "volume-1" in hh or "dwellings" in tt:
      return pick("vol1-dwellings","Volume 1 — Dwellings")
    if "volume 2" in tt or "volume-2" in hh or "non-domestic" in tt or "buildings other than dwellings" in tt:
      return pick("vol2-non-domestic","Volume 2 — Buildings other than dwellings")
    if "amendment" in tt:
      return pick("amendments","Amendments")
    return pick("part-l","Part L")
  if j=="scotland":
    if "non-domestic" in tt:
      return pick("non-domestic","Non-domestic Handbook — Section 6")
    return pick("domestic","Domestic Handbook — Section 6")
  if j=="northern_ireland":
    if "booklet f2" in tt or "f2" in hh:
      return pick("f2-non-domestic","Technical Booklet F2 — Non-domestic")
    return pick("f1-dwellings","Technical Booklet F1 — Dwellings")
  if j=="ireland":
    return pick("dwellings","TGD L — Dwellings")
  return pick("general","Energy efficiency")

def detail_candidates(coll_html, base, j):
  cand=[]
  for a in soup(coll_html).find_all("a", href=True):
    sc = score_for(j, a.get_text(), a["href"])
    if sc>0:
      cand.append((sc, urljoin(base, a["href"]), a.get_text()))
  seen=set(); out=[]
  for sc,u,t in sorted(cand, key=lambda x: -x[0]):
    if u not in seen:
      seen.add(u); out.append((u,t))
  return out[:12]

def find_pdf_on_page(detail_html, base, j):
  best=None; best_sc=-999; best_text=""
  for a in soup(detail_html).find_all("a", href=True):
    href=a["href"]; txt=a.get_text()
    sc=score_for(j, txt, href)
    if href.lower().endswith(".pdf") or "download" in href.lower() or sc>=2:
      full=urljoin(base, href)
      if sc>best_sc:
        best_sc, best, best_text = sc, full, txt
  return best, best_text

MONTHS = {m:i for i,m in enumerate(
  ["","january","february","march","april","may","june","july","august","september","october","november","december"])}

def to_iso(dstr):
  try:
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", dstr)
    if m:
      day, mon, yr = int(m.group(1)), m.group(2).lower(), int(m.group(3))
      mm = MONTHS.get(mon)
      if mm: return f"{yr:04d}-{mm:02d}-{day:02d}"
  except: pass
  # YYYY or YYYY-MM-DD fallbacks
  m=re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", dstr)
  if m: return m.group(0)
  m=re.search(r"\b(\d{4})\b", dstr)
  if m: return m.group(1)
  return dstr.strip()

def extract_issued(detail_html, headers):
  s=soup(detail_html)
  # <time datetime>
  t=s.find("time", attrs={"datetime":True})
  if t: return to_iso(t["datetime"])
  # <meta property='article:published_time' ...>
  m=s.find("meta", attrs={"property":"article:published_time","content":True})
  if m: return to_iso(m["content"])
  # text like "Published 12 July 2023" or "Last updated 3 March 2025"
  txt=s.get_text(" ")
  m=re.search(r"(Published|First published|Last updated|Updated)\s+([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})", txt, re.I)
  if m: return to_iso(m.group(2))
  # HTTP Last-Modified header
  lm=headers.get("Last-Modified") if headers else None
  if lm: return to_iso(lm)
  return ""

def pdf_text(pdf_bytes):
  try:
    from pdfminer.high_level import extract_text
    import logging
    for name in ("pdfminer","pdfminer.layout","pdfminer.pdfinterp","pdfminer.converter"):
      logging.getLogger(name).setLevel(logging.ERROR)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
      f.write(pdf_bytes); path=f.name
    try:
      with contextlib.redirect_stderr(io.StringIO()):
        return extract_text(path) or ""
    finally:
      try: os.remove(path)
      except: pass
  except Exception as e:
    log(f"[WARN] pdf_text failed: {e}")
    return ""

def derive_name(detail_html, pdf_txt, jurisdiction, track_title):
  s=soup(detail_html); h1=s.find("h1")
  base = h1.get_text(strip=True) if (h1 and h1.get_text(strip=True)) else ""
  if not base:
    for line in pdf_txt.splitlines()[:40]:
      L=line.strip()
      if len(L)>8 and any(k in L.lower() for k in ["approved document","technical handbook","technical booklet","guidance","section 6","part l","dwellings"]):
        base=L; break
  if not base: base=f"{jurisdiction.title()} energy efficiency document"
  # add the track to disambiguate
  if track_title and track_title.lower() not in base.lower():
    return f"{base} — {track_title}"
  return base

def extract_numbers(text):
  return sorted(re.findall(r"(roof|wall|floor|window|roof ?light|rooflight|door)\D{0,12}([0-9]\.[0-9]{1,2})", text, re.I))

def load_registry():
  try: return json.load(open("registry/current.json","r",encoding="utf-8"))
  except Exception: return []

def save_registry(reg):
  pathlib.Path("registry").mkdir(parents=True, exist_ok=True)
  json.dump(reg, open("registry/current.json","w",encoding="utf-8"), indent=2)

def ensure(*paths):
  for p in paths: pathlib.Path(p).mkdir(parents=True, exist_ok=True)

# ------------------ Run ------------------

today = datetime.date.today().isoformat()
registry = load_registry()
changed = []

for site in SITES:
  j = site["jurisdiction"]; coll = site["collection"]
  log(f"\n=== {j.upper()} ===")
  rs = fetch(coll)
  if not rs:
    log("[WARN] cannot load collection"); continue

  cands = detail_candidates(rs.text, coll, j)
  log(f"[INFO] detail candidates: {len(cands)}")

  pdf_url = None; detail_html = ""; picked_track_id=""; track_title=""; link_text_for_name=""
  for u, link_text in cands:
    # If the candidate is already a PDF, take it directly
    if u.lower().endswith(".pdf"):
      pdf_url = u
      picked_track_id, track_title = classify_track(j, link_text, u)
      link_text_for_name = link_text
      break
    rr = fetch(u)
    if not rr: continue
    detail_html = rr.text
    picked_track_id, track_title = classify_track(j, link_text, u)
    cand_pdf, cand_pdf_text = find_pdf_on_page(detail_html, u, j)
    log(f"  tried {u} → pdf={bool(cand_pdf)}")
    if cand_pdf:
      pdf_url = cand_pdf
      link_text_for_name = cand_pdf_text or link_text
      issued = extract_issued(detail_html, rr.headers)
      break
  if not pdf_url:
    log("[INFO] no pdf found; skip"); continue

  rp = fetch(pdf_url, stream=True)
  if not rp: continue
  pdf_url = rp.url
  pdf_bytes = rp.content
  checksum = "sha256:"+sha256(pdf_bytes)
  txt = pdf_text(pdf_bytes)

  # Issue date (if we didn't set it above)
  issued = locals().get("issued","")
  if not issued:
    issued = rp.headers.get("Last-Modified","") or ""

  # Name & track
  track_id, track_title = picked_track_id or classify_track(j, link_text_for_name, pdf_url)
  name = derive_name(detail_html, txt, j, track_title)

  numbers = extract_numbers(txt)

  # Use jurisdiction + track_id for slug to avoid collisions
  sl = slug(f"{j}-{track_id}")
  arch = f"archive/{j}/{sl}"; txtdir = f"texts/{sl}"; difdir = f"diffs/{sl}"
  ensure(arch, txtdir, difdir, "docs/history")

  # Upsert registry
  entry = next((x for x in registry if x.get("slug")==sl), None)
  if not entry:
    entry = {"jurisdiction":j, "track":track_id, "track_title":track_title,
             "name":name, "slug":sl, "source_url":pdf_url,
             "checksum":checksum, "numbers":numbers,
             "issued":issued, "first_seen":today, "last_seen":today,
             "rule_version":"v1.0.0", "history":[]}
    registry.append(entry)
    open(f"{arch}/{today}.pdf","wb").write(pdf_bytes)
    open(f"{txtdir}/latest.txt","w",encoding="utf-8").write(txt)
    entry["history"].append({"date":today,"source_url":pdf_url,"checksum":checksum,
                             "numbers":numbers,"issued":issued,
                             "archived_pdf":f"{arch}/{today}.pdf","diff":None})
    changed.append((sl,"NEW"))
    log("[INFO] NEW")
  else:
    major = (entry["source_url"] != pdf_url)
    minor = (entry["checksum"] != checksum)
    numeric = (entry.get("numbers") != numbers)
    if major or minor or numeric:
      prev = open(f"{txtdir}/latest.txt",encoding="utf-8").read() if os.path.exists(f"{txtdir}/latest.txt") else ""
      diff=list(difflib.unified_diff(prev.splitlines(1), txt.splitlines(1), fromfile="previous", tofile="current"))
      open(f"{difdir}/{today}.patch","w",encoding="utf-8").writelines(diff)
      open(f"{arch}/{today}.pdf","wb").write(pdf_bytes)
      open(f"{txtdir}/latest.txt","w",encoding="utf-8").write(txt)
      # bump version (major > numeric > patch)
      v=entry.get("rule_version","v1.0.0")[1:].split("."); v=[int(x) for x in v]+[0]*(3-len(v))
      if major:   v=[v[0]+1,0,0]; kind="MAJOR"
      elif numeric: v=[v[0],v[1]+1,0]; kind="NUMERIC"
      else:      v=[v[0],v[1],v[2]+1]; kind="MINOR"
      entry.update({"name":name,"track":track_id,"track_title":track_title,
                    "source_url":pdf_url,"checksum":checksum,"numbers":numbers,
                    "issued":issued, "last_seen":today,
                    "rule_version":f"v{v[0]}.{v[1]}.{v[2]}"})
      entry["history"].append({"date":today,"source_url":pdf_url,"checksum":checksum,
                               "numbers":numbers,"issued":issued,
                               "archived_pdf":f"{arch}/{today}.pdf","diff":f"{difdir}/{today}.patch"})
      changed.append((sl,kind))
      log(f"[INFO] CHANGE: {kind}")
    else:
      entry["last_seen"]=today
      # also keep nicer title/track if we learned them
      entry["track"]=track_id; entry["track_title"]=track_title
      entry["name"]=name
      if issued and not entry.get("issued"):
        entry["issued"]=issued
      log("[INFO] No change")

save_registry(registry)
if changed:
  with open("CHANGELOG.md","a",encoding="utf-8") as f:
    for sl,kind in changed:
      f.write(f"{today}: {sl} — {kind}\n")
else:
  log("[INFO] No new/changed docs.")
