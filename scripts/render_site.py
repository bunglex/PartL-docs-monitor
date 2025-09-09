import json, pathlib
from jinja2 import Environment, FileSystemLoader, select_autoescape

DATA=json.load(open("registry/current.json"))
env=Environment(loader=FileSystemLoader("templates"),autoescape=select_autoescape(["html"]))
pathlib.Path("docs").mkdir(parents=True,exist_ok=True)
pathlib.Path("docs/history").mkdir(parents=True,exist_ok=True)

open("docs/index.html","w",encoding="utf-8").write(env.get_template("index.html.j2").render(items=DATA))
open("docs/current.json","w",encoding="utf-8").write(json.dumps(DATA,indent=2))

doc_tpl=env.get_template("doc.html.j2")
for it in DATA:
  open(f"docs/history/{it['slug']}.html","w",encoding="utf-8").write(doc_tpl.render(item=it))
