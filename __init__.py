import hashlib
import json
import logging
import re
import shutil
import subprocess
import threading
from functools import lru_cache
from pathlib import Path

from agent.secret_scope import get_secret
from gateway.session_context import get_session_env
from tools.registry import tool_error, tool_result

logger = logging.getLogger(__name__)

HERE = Path(__file__).parent

CARD_RULES = """\
CARD RULES (these override the catalog where they differ):
- Every number, name and link comes from a tool result in this conversation. Never invent or sample data.
- Card title: a status emoji when the subject has a status (🟢 🟡 🔴), then the subject. Subtitle: scope and time window.
- Fields: 0-3 short headline KPIs that the chart or table does not already show. They render as tiles in one row. The time window and scope go in the subtitle, coverage and data quality in a muted footer, never in Fields. Skip Fields when the chart or table says it all.
- Chart for a trend over time (line or area, at most 20 points) or a comparison across categories (bar); pie only for shares of a whole. At most 2 charts. Chart titles max 50 chars; category, series and segment labels max 20 chars, so abbreviate them in the chart and keep full names in the table.
- Table for row data, about 10 rows at most. Right-align numeric columns. Leave out columns that are mostly empty.
- Numbers are human-scaled: 594M, 1.5 s, 12.7k, not 593,755,017.
- If data retrieval failed or was partial, say so in the title (🟡 Inconclusive) and never show a healthy status on missing data.
- LinkButtons point at the exact item (dashboard panel, error, ticket, commit), not a homepage. At most 4.
- End with a muted Text naming the data sources and window.
- Caveats and corrections go inside the card as a Text element, never in a follow-up message.
- After the card posts, your final reply is at most one short line and never repeats or adds to the card."""

SCHEMA = {
    "name": "slack_card",
    "description": (
        "Post a native Slack card (KPI tiles, charts, table, link buttons) into the current Slack thread. "
        "`spec` is a json-render chat spec: {\"root\": \"<key>\", \"elements\": {\"<key>\": {\"type\", \"props\", "
        "\"children\": [keys]}}}. If the spec is invalid the tool returns the issues; fix them and call again.\n\n"
        + (HERE / "catalog-prompt.txt").read_text() + "\n\n" + CARD_RULES
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "spec": {
                "type": "object",
                "description": "json-render chat spec with a Card root.",
                "properties": {"root": {"type": "string"}, "elements": {"type": "object"}},
                "required": ["root", "elements"],
            },
        },
        "required": ["spec"],
    },
}

PROMPT_SECTION = (
    "## Slack cards\n"
    "On Slack you can post a native card with the `slack_card` tool (via `tool_call`; read its description "
    "with `tool_describe` first): KPI tiles, charts, a table and link buttons. Use it for structured, scannable "
    "answers and whenever the user asks for a card. After the card posts, your final reply is at most one "
    "short line."
)

LINK_ACTION_RE = re.compile(r"^link-")
SLACK_LINK_RE = re.compile(r"<([^|>]+)(?:\|([^>]+))?>")

# `hermes plugins update` pulls new code but leaves node_modules alone, so record which lockfile the
# installed deps came from and reinstall when it changes.
STAMP = HERE / "node_modules" / ".slack-cards-lock-sha256"


def _lock_hash():
    return hashlib.sha256((HERE / "package-lock.json").read_bytes()).hexdigest()


def _deps_current():
    return STAMP.is_file() and STAMP.read_text().strip() == _lock_hash()


_deps = {"state": "ready" if _deps_current() else "missing", "reason": None}


@lru_cache(maxsize=1)
def node_bin():
    """Hermes' own bundled Node (the one its npx MCP servers use), else node on PATH."""
    try:
        import pm
        runner = pm.ensure("node")
        found = shutil.which("node", path=runner.env.get("PATH", ""))
        if found:
            return found
    except Exception as exc:
        logger.debug("slack-cards: pm node lookup failed: %s", exc)
    return shutil.which("node") or "node"


def _install_deps():
    # Same executor `hermes plugins install` uses after its y/N prompt. Non-interactive installs
    # (Ansible) skip that prompt, so the plugin installs its own locked deps, honoring
    # security.allow_lazy_installs (explicit=False).
    try:
        from pm.workspace import install_node_sidecar
        reason = install_node_sidecar(HERE, explicit=False)
        if not reason:
            STAMP.write_text(_lock_hash())
    except Exception as exc:
        reason = str(exc)
    _deps.update(state="failed" if reason else "ready", reason=reason)
    log = logger.warning if reason else logger.info
    log("slack-cards: node deps %s%s", _deps["state"], f": {reason}" if reason else "")


def ensure_deps():
    if _deps["state"] == "missing":
        _deps["state"] = "installing"
        threading.Thread(target=_install_deps, name="slack-cards-npm-ci", daemon=True).start()


def plain_title(title):
    return SLACK_LINK_RE.sub(lambda m: m.group(2) or m.group(1), title)


def render(spec):
    if _deps["state"] != "ready":
        return {"issues": [f"card renderer unavailable (node deps {_deps['state']}"
                           f"{': ' + _deps['reason'] if _deps['reason'] else ''}); reply in markdown this time"]}
    root = (spec.get("elements") or {}).get(spec.get("root")) or {}
    props = root.get("props") or {}
    if isinstance(props.get("title"), str):
        props["title"] = plain_title(props["title"])
    out = subprocess.run([node_bin(), "render.mjs"], cwd=HERE, input=json.dumps(spec), capture_output=True,
                         text=True, timeout=30)
    if out.returncode != 0:
        return {"issues": [f"renderer failed: {out.stderr.strip()[-500:]}"]}
    return json.loads(out.stdout)


def slack_card(args, **kw):
    if get_session_env("HERMES_SESSION_PLATFORM") != "slack":
        return tool_error("slack_card only works in a Slack conversation; reply in markdown instead.")
    channel = get_session_env("HERMES_SESSION_CHAT_ID")
    thread_ts = get_session_env("HERMES_SESSION_THREAD_ID") or None
    token = get_secret("SLACK_BOT_TOKEN")
    if not channel or not token:
        return tool_error("No Slack channel or bot token in this session.")
    rendered = render(args.get("spec") or {})
    if rendered.get("issues"):
        return tool_error("Invalid card spec, fix and retry:\n- " + "\n- ".join(rendered["issues"]))
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    try:
        resp = WebClient(token=token).chat_postMessage(
            channel=channel, thread_ts=thread_ts, text=rendered["text"][:3000] or "card",
            blocks=rendered["blocks"], unfurl_links=False, unfurl_media=False,
        )
    except SlackApiError as e:
        return tool_error(f"Slack rejected the card: {e.response.get('error')} {e.response.get('errors') or ''}")
    return tool_result({"posted": True, "ts": resp["ts"], "channel": channel})


async def ack_link_click(ack, body, action):
    # ponytail: link buttons open the URL client-side; Slack still sends block_actions and wants an ack
    await ack()


def slack_prompt_section(info):
    return PROMPT_SECTION if info.get("platform") == "slack" else ""


def register(ctx):
    ensure_deps()
    ctx.register_system_prompt_section("slack-cards", slack_prompt_section)
    ctx.register_tool(
        name="slack_card", toolset="hermes-slack", schema=SCHEMA, handler=slack_card,
        description=SCHEMA["description"], emoji="🗂️",
    )
    ctx.register_slack_action_handler(LINK_ACTION_RE, ack_link_click)
