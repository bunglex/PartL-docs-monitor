#!/usr/bin/env python3
import argparse, json
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", required=True)
    ap.add_argument("--templates", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    data = json.loads(Path(args.registry).read_text(encoding="utf-8"))
    env = Environment(
        loader=FileSystemLoader(args.templates),
        autoescape=select_autoescape(["html", "xml"])
    )
    tmpl = env.get_template("doc.html.j2")
    html = tmpl.render(items=data.get("items", []), machine_json="current.json")
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir/"index.html").write_text(html, encoding="utf-8")
    # also copy the json into docs for the link
    (outdir/"current.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
