#!/usr/bin/env python3
"""
Render a tiny static site from registry JSON.

Usage:
  python scripts/render_site.py --registry registry/current.json --templates templates --out docs
"""

import argparse
import json
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape, TemplateNotFound


def load_registry(p: Path) -> dict:
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[ERROR] Registry not found: {p}")
        return {"items": []}
    except json.JSONDecodeError as e:
        print(f"[ERROR] Invalid JSON in {p}: {e}")
        return {"items": []}

    # Accept {"items":[...]} or just [...]
    if isinstance(raw, dict) and isinstance(raw.get("items"), list):
        items = raw["items"]
        payload = raw
    elif isinstance(raw, list):
        items = raw
        payload = {"items": items}
    else:
        items = []
        payload = {"items": items}

    print(f"[INFO] Loaded {len(items)} items from {p}")
    return payload


def main():
    ap = argparse.ArgumentParser(description="Render static HTML from registry JSON")
    ap.add_argument("--registry", required=True)
    ap.add_argument("--templates", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    registry_path = Path(args.registry)
    templates_dir = Path(args.templates)
    outdir = Path(args.out)

    data = load_registry(registry_path)
    items = data.get("items", [])

    # Set up templating
    try:
        env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        tmpl = env.get_template("doc.html.j2")
    except TemplateNotFound:
        # Helpful error with directory listing
        existing = ", ".join(sorted(p.name for p in templates_dir.glob("*")))
        print(f"[ERROR] Template 'doc.html.j2' not found in {templates_dir}")
        print(f"[HINT] Files in templates/: {existing or '(empty)'}")
        raise

    html = tmpl.render(items=items, machine_json="current.json")

    # Write outputs
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "index.html").write_text(html, encoding="utf-8")
    (outdir / "current.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    print(f"[OK] Wrote {outdir/'index.html'} and {outdir/'current.json'} ({len(items)} items)")


if __name__ == "__main__":
    main()
