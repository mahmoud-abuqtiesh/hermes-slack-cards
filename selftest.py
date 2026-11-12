# Offline check of the spec -> Slack blocks path (no Slack calls).
# Run from the Hermes checkout so its modules import:
#   cd ~/.hermes/hermes-agent && venv/bin/python ~/.hermes/plugins/slack-cards/selftest.py
import importlib.util
import os
import subprocess
import sys

sys.path.insert(0, os.getcwd())
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("slack_cards", os.path.join(here, "__init__.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
if m._deps["state"] != "ready":
    m._install_deps()
assert m._deps["state"] == "ready", m._deps
assert m._deps_current()

generated = subprocess.run([m.node_bin(), "render.mjs", "prompt"], cwd=here, capture_output=True, text=True,
                           check=True).stdout
assert generated == open(os.path.join(here, "catalog-prompt.txt")).read(), \
    "catalog-prompt.txt is stale: node render.mjs prompt > catalog-prompt.txt"

el = lambda t, props, children=(): {"type": t, "props": props, "children": list(children)}
good = {"root": "c", "elements": {
    "c": el("Card", {"title": "🟡 <http://a.com|a.com> backlog", "subtitle": "last 6h UTC"}, ["f", "ch", "t", "a", "n"]),
    "f": el("Fields", {}, ["f1"]),
    "f1": el("Field", {"label": "Queued", "value": 2812}),
    "ch": el("Chart", {"title": "jobs per cell", "chart": {"type": "bar", "categories": ["av", "mq"],
                                                          "series": [{"name": "jobs", "values": [1, 2]}]}}),
    "t": el("Table", {"headers": ["Job", "Runs"], "rows": [["X", 1]], "align": ["left", "right"]}),
    "a": el("Actions", {}, ["l"]),
    "l": el("LinkButton", {"label": "Grafana", "url": "https://grafana.example.com"}),
    "n": el("Text", {"content": "Prometheus", "style": "muted"}),
}}
out = m.render(good)
types = [b["type"] for b in out["blocks"]]
assert types == ["header", "context", "layout", "actions", "context"], out
rows = [[i["block"] for i in r["items"]] for r in out["blocks"][2]["rows"]]
assert [[b["type"] for b in r] for r in rows] == [["section"], ["data_visualization"], ["table"]], rows
assert rows[0][0]["text"]["text"] == "Queued\n*2812*", rows[0]
assert rows[2][0]["column_settings"] == [{"is_wrapped": True}, {"align": "right"}], rows[2]
assert {b["type"] for b in out["blocks"][2]["fallback"]} <= {"section", "table"}
assert out["blocks"][0]["text"]["text"] == "🟡 a.com backlog"
assert m.LINK_ACTION_RE.match(out["blocks"][3]["elements"][0]["action_id"])

long = dict(good, elements=dict(good["elements"], t=el("Table", {"headers": ["n"], "rows": [[i] for i in range(30)]})))
nested = [i["block"]["type"] for b in m.render(long)["blocks"] for r in b.get("rows", []) for i in r["items"]]
assert "data_table" in nested, nested

bad = {"root": "c", "elements": {"c": el("Card", {}, ["x"]), "x": el("Chart", {"title": "t", "chart": {
    "type": "bar", "categories": ["a"], "series": [{"name": "s", "values": [1, 2]}]}})}}
assert m.render(bad)["issues"], "invalid spec must return issues"
assert "Button:" not in m.SCHEMA["description"].replace("LinkButton:", "")
assert "slack_card" in m.slack_prompt_section({"platform": "slack"})
assert m.slack_prompt_section({"platform": "discord"}) == ""
print("OK")
