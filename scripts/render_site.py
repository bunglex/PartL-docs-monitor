import json, pathlib, sys
from jinja2 import Environment, FileSystemLoader, select_autoescape, TemplateNotFound

# Load data (tolerate missing/invalid JSON)
try:
    data = json.load(open("registry/current.json", "r", encoding="utf-8"))
    if not isinstance(data, list):
        data = []
except FileNotFoundError:
    data = []
except Exception as e:
    print(f"[WARN] bad registry/current.json: {e}", file=sys.stderr)
    data = []

# Ensure folders
pathlib.Path("docs/history").mkdir(parents=True, exist_ok=True)

# Try to load templates; fall back to a minimal page if missing
env = Environment(loader=FileSystemLoader("templates"),
                  autoescape=select_autoescape(["html"]))

def write(path, content):
    pathlib.Path(path).write_text(content, encoding="utf-8")

try:
    index_tpl = env.get_template("index.html.j2")
    doc_tpl = env.get_template("doc.html.j2")
except TemplateNotFound as e:
    write("docs/index.html",
          "<h1>Building Energy Docs</h1><p>No template found or no data yet.</p>")
else:
    write("docs/index.html", index_tpl.render(items=data))
    for it in data:
        write(f"docs/history/{it['slug']}.html", doc_tpl.render(item=it))

# Always write machine-readable JSON
write("docs/current.json", json.dumps(data, indent=2))
print("[INFO] render_site complete; items:", len(data))
