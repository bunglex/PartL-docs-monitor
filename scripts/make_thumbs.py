# scripts/make_thumbs.py
import json, os, re, hashlib, tempfile, subprocess, shutil
import pathlib, urllib.request

OUT_DIR = os.environ.get("OUT_DIR", "site")            # your publish dir
JSON_PATH = os.environ.get("JSON_PATH", f"{OUT_DIR}/current.json")
THUMB_DIR = os.path.join(OUT_DIR, "thumbs")

def slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or hashlib.sha1(os.urandom(8)).hexdigest()[:10]

def pick_pdf(row):
    for k in ("pdf_url","pdf","PDF"):
        if k in row and isinstance(row[k], str) and row[k].lower().endswith(".pdf"):
            return row[k]
    if isinstance(row.get("pdf"), dict) and row["pdf"].get("href"):
        return row["pdf"]["href"]
    links = row.get("links") or {}
    if isinstance(links, dict) and isinstance(links.get("pdf"), str):
        return links["pdf"]
    return ""

def ensure_dir(p): pathlib.Path(p).mkdir(parents=True, exist_ok=True)

def make_thumb(pdf_url: str, out_png: str):
    # download to temp, then use poppler's pdftoppm (very reliable on GH runners)
    with tempfile.TemporaryDirectory() as td:
        pdf_path = os.path.join(td, "doc.pdf")
        with urllib.request.urlopen(pdf_url, timeout=60) as r, open(pdf_path, "wb") as f:
            shutil.copyfileobj(r, f)
        # first page only
        png_base = os.path.splitext(out_png)[0]
        subprocess.run(
            ["pdftoppm", "-png", "-singlefile", "-f", "1", "-l", "1", pdf_path, png_base],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

def main():
    ensure_dir(THUMB_DIR)
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    items = data.get("items") or data  # support either root list or {items:[...]}
    for row in items:
        pdf = pick_pdf(row)
        if not pdf:
            continue
        base = row.get("title") or row.get("name") or pdf
        name = slugify(base)[:70]
        out_png = os.path.join(THUMB_DIR, f"{name}.png")
        rel_png = f"./thumbs/{name}.png"
        if not os.path.exists(out_png):
            try:
                make_thumb(pdf, out_png)
            except Exception:
                continue
        row["thumb_url"] = rel_png

    # write back (preserve original structure)
    if isinstance(data, dict) and "items" in data:
        data["items"] = items
    else:
        data = items

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
