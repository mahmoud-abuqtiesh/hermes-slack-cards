// `node render.mjs prompt` prints the catalog section of the tool description.
// `node render.mjs` reads a json-render chat spec ({root, elements}) on stdin and prints
// {blocks, text} (Slack Block Kit via Chat SDK's Slack adapter) or {issues} for the model to fix.
import { readFileSync } from "node:fs";
import { defineCatalog } from "@json-render/core";
import { CardSpecError, schema, standardComponentDefinitions, toCard } from "@json-render/chat";
import { cardToBlockKit, cardToFallbackText } from "@chat-adapter/slack/blocks";

const SLACK_LIMITS = {
  tables: 1, charts: 2, fields: 10, tableRows: 100, tableColumns: 20,
  chartLabel: 20, chartTitle: 50, chartSeries: 12, chartPoints: 20,
};

// ponytail: no interactive Button; clicks would need a handler and Hermes' clarify tool already covers choices
const { Button, ...components } = standardComponentDefinitions;

function catalogSection() {
  const full = defineCatalog(schema, { components }).prompt();
  const list = full.slice(full.indexOf("AVAILABLE COMPONENTS ("),full.indexOf("EVENTS (the `on` field)")).trim();
  // ponytail: rules 8+ are @json-render/chat's own defaultRules; 1-7 are JSONL streaming rules we don't use
  const rules = full.slice(full.indexOf("RULES:")).split("\n").filter((l) => /^\d+\. /.test(l))
    .filter((l) => Number(l.split(".")[0]) >= 8 && !/\$state|\$bindState|repeat/.test(l))
    .map((l) => "- " + l.replace(/^\d+\.\s*/, ""));
  return `${list}\n\nCATALOG RULES:\n${rules.join("\n")}`;
}

function findTable(el) {
  if (el.type === "table") return el;
  for (const c of el.children ?? []) {
    const t = findTable(c);
    if (t) return t;
  }
}

// Two deviations from Chat SDK's Slack output, both for how Slack renders them:
// - data_table splits columns evenly, truncates and pages at 5 rows; `table` sizes to content and wraps.
//   Keep data_table only for tables long enough to need paging.
// - section.fields stacks every label/value pair on its own lines; one inline row reads better.
// ponytail: 20-row cutoff is a taste call; raise it if long tables look fine unpaged
const VISUAL = new Set(["data_visualization", "table", "data_table"]);
const mrkdwnSection = (text) => ({ type: "section", text: { type: "mrkdwn", text } });

function fieldPairs(section) {
  return section.fields.map((f) => {
    const [label, ...rest] = f.text.split("\n");
    return [label.replace(/^\*(.*)\*$/, "$1"), rest.join(" ")];
  });
}

// Slack gives every block type its own intrinsic width (chart box vs content-sized table).
// Rows of one `layout` block share its width, so KPI tiles, charts and tables line up.
function toLayoutRow(b) {
  if (b.type === "section" && b.fields) {
    return { items: fieldPairs(b).map(([l, v]) => ({ block: mrkdwnSection(`${l}\n*${v}*`) })) };
  }
  return { items: [{ block: b }] };
}

function toFallback(b) {
  if (b.type === "section" && b.fields) return [mrkdwnSection(fieldPairs(b).map(([l, v]) => `${l}  *${v}*`).join("      "))];
  if (b.type === "table") return [b];
  if (b.type === "data_visualization") return [mrkdwnSection(`:bar_chart: ${b.title}`)];
  return [mrkdwnSection("(table)")];
}

function groupIntoLayouts(blocks) {
  const out = [];
  let run = [];
  const flush = () => {
    // ponytail: a layout holds 5 rows; longer runs split into several layouts
    for (let i = 0; i < run.length; i += 5) {
      const rows = run.slice(i, i + 5);
      out.push({ type: "layout", label: "card body", rows: rows.map(toLayoutRow), fallback: rows.flatMap(toFallback) });
    }
    run = [];
  };
  for (const b of blocks) {
    const tileable = (b.type === "section" && b.fields && b.fields.length <= 3) || VISUAL.has(b.type);
    if (tileable) run.push(b);
    else {
      flush();
      out.push(b.type === "section" && b.fields ? toFallback(b)[0] : b);
    }
  }
  flush();
  return out;
}

function polish(blocks, card) {
  const table = findTable(card);
  return groupIntoLayouts(blocks.map((b) => {
    if (b.type === "data_table" && b.rows.length <= 21) {
      return {
        type: "table",
        rows: b.rows,
        column_settings: b.rows[0].map((_, i) => {
          const align = table?.align?.[i] ?? "left";
          return align === "left" ? { is_wrapped: true } : { align };
        }),
      };
    }
    return b;
  }));
}

if (process.argv[2] === "prompt") {
  process.stdout.write(catalogSection());
} else {
  try {
    const card = toCard(JSON.parse(readFileSync(0, "utf8")), { components, limits: SLACK_LIMITS });
    process.stdout.write(JSON.stringify({ blocks: polish(cardToBlockKit(card), card), text: cardToFallbackText(card) }));
  } catch (e) {
    process.stdout.write(JSON.stringify({ issues: e instanceof CardSpecError ? e.issues : [String(e?.message || e)] }));
  }
}
