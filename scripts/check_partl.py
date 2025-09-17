#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape


def main():
    ap = argparse.ArgumentParser(description="Render static site from registry JSON")
    ap.add_argument("--registry", required=True, help="Path to registry/current.json")
    ap.add_argument("--templates", required=True, help="Templates directory (contains doc.html.j2)")
    ap.add_argument("--out", required=True, help="Output directory for the rendered site (e.g., docs/)")
    args = ap.parse_args()

    registry_path = Path(args.registry)
    templates_dir = Path(args.templates)
    outdir = Path(args.out)

    # Load registry and accept either {"items":[...]} or just [...]
    try:
        data_raw = json.loads(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[ERROR] Registry not found: {registry_path}")
        data_raw = {}
    except json.JSONDecodeError as e:
        print(f"[ERROR] Invalid JSON in {registry_path}: {e}")
        data_raw = {}

    if isinstance(data_raw, dict) and isinstance(data_raw.get("items"), list):
        items = data_raw["items"]
        data_to_write = data_raw
    elif isinstance(data_raw, list):
        items = data_raw
        data_to_write = {"items": items}
    else:
        items = []
