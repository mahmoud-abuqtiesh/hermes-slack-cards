# hermes-slack-cards

A [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that lets the agent answer in Slack with native cards: KPI tiles, bar/line/area/pie charts, tables and link buttons.

The agent writes a [json-render](https://json-render.dev) chat spec.
The plugin validates it with `@json-render/chat` and converts it to Block Kit with Chat SDK's Slack adapter (`@chat-adapter/slack`), the same stack as the [json-render/chat demo](https://x.com/ctatedev/status/2118853661040295997).
Only the conversion runs in Node; Hermes keeps its own Slack connection and posts the blocks.

## Install

```sh
hermes plugins install mahmoud-abuqtiesh/hermes-slack-cards --ref <commit-sha> --enable
```

Then restart the gateway (`systemctl --user restart hermes-gateway`).

The plugin needs its locked Node dependencies (`npm ci`, about 35 MB).
In a terminal, `hermes plugins install` asks and installs them.
Non-interactive installs (Ansible, CI) skip that question, so the plugin runs the same `npm ci` itself on its first gateway start, using Hermes' bundled Node.
That honors `security.allow_lazy_installs` (on by default); if you turned it off, run `npm ci --ignore-scripts` in the plugin directory yourself.
Until the dependencies are in place, the tool tells the agent to answer in markdown.

Pin `--ref` to a commit so a deploy never pulls an unreviewed change.

## Slack app

No manifest change is needed if Hermes already runs on Slack.
The plugin uses what the Hermes Slack setup already has:

- `chat:write` bot scope, to post the card.
- Interactivity enabled (Socket Mode is fine). Link buttons open their URL in the browser, but Slack also sends a click event, and the plugin acknowledges it so the button never shows an error. With interactivity off, Slack sends nothing and the buttons still work.

The workspace must support the newer Block Kit blocks the cards use (`layout`, `data_visualization`, `table`, `data_table`).
If Slack rejects a block, the tool reports the error and the agent answers in markdown.

## Hermes config

Optional, in `config.yaml`:

```yaml
platforms:
  slack:
    extra:
      rich_blocks: true  # plain markdown replies also get native tables and headers
```

## SOUL.md

The plugin tells the agent the tool exists, but it does not decide when a card is the default.
Without this, people have to ask for "a card" every time.
Add a section like this to the agent's `SOUL.md` and adjust the triggers to the agent's job:

```markdown
## Slack cards

In Slack, answer with a card (the `slack_card` tool) instead of markdown whenever the answer is structured and someone will scan it rather than read it:
- alert triage and incident status
- deploy reviews and error reports (new or regressed errors, counts per release)
- health or status checks of a service, cell or datasource
- metric breakdowns and rankings (per cell, per service, top N)
- anything the user calls a card or dashboard

Keep plain text for conversation, yes/no answers, clarifying questions and explanations.
One card per answer. Do not post a summary next to it.
When a human has to act, put a bold "Next action: <owner> - <exact action>" line in the card.
```

## What the agent can put in a card

| Component | Renders as |
|---|---|
| `Card` (root) | Header plus a subtitle line |
| `Fields` / `Field` | Up to 3 KPI tiles in one row |
| `Chart` (`bar`, `line`, `area`, `pie`) | Native Slack chart, at most 2 per message |
| `Table` | Content-sized table; over 20 rows becomes a paged data table |
| `Text` (`muted` for footers) | Markdown paragraph or small grey line |
| `Actions` / `LinkButton` | Buttons that open a URL |
| `Section`, `Divider`, `Image` | Grouping, separator, image |

KPI tiles, charts and tables are wrapped in one Block Kit `layout` so they share the same width.
Interactive buttons are left out on purpose: use Hermes' clarify tool for choices.

The tool description the agent reads is the json-render catalog (`catalog-prompt.txt`, generated) plus card rules in `__init__.py`: no invented data, status emoji in the title, short chart labels, caveats inside the card, no follow-up message.

## Develop

```sh
npm ci --ignore-scripts
cd ~/.hermes/hermes-agent && venv/bin/python /path/to/hermes-slack-cards/selftest.py
```

After bumping a dependency, regenerate the catalog prompt and commit it:

```sh
node render.mjs prompt > catalog-prompt.txt
```
