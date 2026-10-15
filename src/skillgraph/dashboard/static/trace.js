/* skillgraph dashboard: the run page, a trace viewer. Tree of phases, turns and events on
   the left; a detail pane on the right with the scores (claim results) as a first-class tab,
   linked both ways to the events that decided them. */
"use strict";

(() => {
  const SG = window.SG;
  const { h, href, usd, tok, clip, plural } = SG;

  const target = (ev) => {
    const i = ev.input;
    if (!i || typeof i !== "object") return String(i ?? "");
    return String(i.command || i.file_path || i.pattern || i.skill || i.description || i.url || i.query || JSON.stringify(i));
  };
  const firstLine = (s) => String(s || "").split("\n").find((x) => x.trim()) || "";

  SG.pages.run = async (ctx) => {
    const set = ctx.set, path = ctx.path;
    const [S, R] = await Promise.all([SG.setData(set), SG.runData(set, path)]);
    if (!ctx.isCurrent()) return;
    const t = R.trace, C = R.check, G = R.digest, ev = t.events;
    const win = C?.window || null;
    const res = t.result || {};
    const setName = SG.setLabel(SG.SETS.find((s) => s.id === set) || { id: set, name: set });
    SG.chrome({ set, page: "runs", skillName: null, S, title: `${t.task} r${t.rep}`,
      crumbs: [[setName, href(["s", set])], [t.skill, href(["s", set, "skill", t.skill])], ["Runs", href(["s", set, "runs"], { skill: t.skill })], [`${t.task} / ${t.variant.name} / r${t.rep}`]] });

    // ---------------------------------------------------------- results and their evidence
    const results = [];
    for (const [id, r] of Object.entries(C?.claims || {})) results.push({ scope: "claim", id, r, text: R.claims[id]?.text, span: R.claims[id]?.span });
    for (const [id, r] of Object.entries(C?.conditions || {})) results.push({ scope: "condition", id, r, text: R.conditions[id]?.text });
    for (const [id, r] of Object.entries(C?.reference?.claims || {})) results.push({ scope: "reference", id, r, text: R.reference[id]?.text });
    const byEvent = new Map();
    for (const x of results) if (x.r.evidence != null) byEvent.set(x.r.evidence, [...(byEvent.get(x.r.evidence) || []), x]);
    const turnOf = R.turn_of_event || [];
    const evidenceTurns = new Map();
    for (const [i, xs] of byEvent) { const n = turnOf[i]; if (n != null) evidenceTurns.set(n, [...(evidenceTurns.get(n) || []), ...xs]); }
    const routedTo = new Map();
    for (const [id, turns] of Object.entries(win?.routes || {})) for (const n of turns) routedTo.set(n, [...(routedTo.get(n) || []), id]);
    const offSkill = new Set(win?.off_skill_turns || []);
    const phases = ((G.intents || {}).data?.phases || []).slice().sort((a, b) => a.first - b.first);
    const statusOf = (x) => (x.scope === "condition" ? (x.r.value === true ? "satisfied" : x.r.value === false ? "n/a" : "unclear") : x.r.status);
    const counts = {};
    results.filter((x) => x.scope === "claim").forEach((x) => { counts[x.r.status] = (counts[x.r.status] || 0) + 1; });

    // ---------------------------------------------------------- state
    const p = ctx.params;
    const st = {
      event: p.get("event") != null ? Number(p.get("event")) : null,
      turn: null,
      tab: p.get("tab") || "detail",
      status: p.get("status") || "",
      q: p.get("q") || "",
      tool: "",
      onlyEvidence: false,
      collapseOff: !!win,
      pane: "timeline",
      expanded: new Set(),
      openGroups: new Set(),
      highlight: p.get("claim") || null,
    };
    if (st.highlight && st.event == null) {
      const x = results.find((r) => r.id === st.highlight || `ref:${r.id}` === st.highlight);
      if (x && x.r.evidence != null) st.event = x.r.evidence;
      else if (x) st.tab = "scores";
    }
    if (st.event != null && turnOf[st.event] != null) st.expanded.add(turnOf[st.event]);
    if (st.event != null || p.get("tab")) st.pane = "detail";

    // ---------------------------------------------------------- header
    const scoreLinks = SG.STATUS_ORDER.filter((s) => counts[s]).map((s) => {
      const b = SG.statusBadge(s, href(["s", set, "run", path], { tab: "scores", status: s }));
      b.append(` ${counts[s]}`);
      return b;
    });
    const successChecks = Object.entries(t.success || {});
    const passed = successChecks.filter(([, ok]) => ok).length;
    const header = h("div.trace-head",
      h("header.page-head", h("div", h("h1", t.task),
        h("p.meta", [`${t.variant.name} r${t.rep}`, t.model, `${plural(G.stats.turns, "turn")}`, `${plural(G.stats.tool_calls, "tool call")}`, `~${tok(G.stats.tokens_full)} tokens`,
          res.cost_usd != null ? `${usd(res.cost_usd)} agent` : null, C?.judge_cost_usd != null ? `${usd(C.judge_cost_usd)} judge` : null].filter(Boolean).join(" · ")))),
      h("div.score-strip",
        h("span.faint", "Scores"), scoreLinks.length ? scoreLinks : h("span.faint", C ? "no claims" : "not graded yet"),
        successChecks.length ? (passed === successChecks.length ? SG.badge("good", `✓ task passed (${passed}/${successChecks.length})`) : SG.badge("bad", `✗ task failed (${passed}/${successChecks.length})`)) : null,
        C ? SG.badge(C.judge_mode === "windowed" ? "info" : "outline", `judged ${C.judge_mode || "full"}`, C.judge_mode === "windowed" ? "Long trace: the judge read the turns that hold the evidence plus a skeleton of the whole run." : "The judge read the whole trace.") : null,
        win ? h("span.faint.small", `${plural(win.calls, "judge call")} on ~${tok(win.tokens_sent)} of ~${tok(win.tokens_full)} tokens${win.escalated?.length ? ` · ${win.escalated.length} confirmed on the skeleton` : ""}`) : null,
        t.episode && t.episode.of > 1 ? SG.badge("accent", `episode ${t.episode.index} of ${t.episode.of}`) : null,
        t.skill_loaded === false && t.variant.name !== "baseline" ? SG.badge("warn", "skill not loaded") : null,
        t.error ? SG.badge("bad", "error", t.error) : null),
      G.intents ? h("p.intent", h("strong", "Task: "), G.intents.data.task, h("span.faint", " · "), h("strong", "Outcome: "), G.intents.data.outcome,
        h("span.faint.small", ` (named by ${G.intents.model}, which never sees the skill)`)) : null);

    // ---------------------------------------------------------- tree
    const treeBox = h("div.tree", { role: "tree", "aria-label": "Timeline", tabindex: "0" });
    const filterInput = h("input.control.grow", { type: "search", placeholder: "Filter events  ( / )", "aria-label": "Filter events", value: st.q,
      oninput: (e) => { st.q = e.target.value; SG.setParams({ q: st.q || null }); renderTree(); } });
    const tools = [...new Set(ev.filter((e) => e.kind === "tool").map((e) => e.tool))].sort();
    const toolSel = h("select.control", { "aria-label": "Tool", onchange: (e) => { st.tool = e.target.value; renderTree(); } },
      h("option", { value: "" }, "All tools"), tools.map((x) => h("option", { value: x }, x)));
    const evToggle = h("label.toggle", h("input", { type: "checkbox", onchange: (e) => { st.onlyEvidence = e.target.checked; renderTree(); } }), " Only evidence");
    const offToggle = win ? h("label.toggle", h("input", { type: "checkbox", checked: st.collapseOff, onchange: (e) => { st.collapseOff = e.target.checked; renderTree(); } }), " Collapse off-skill") : null;

    const matches = (e) => {
      if (st.tool && e.tool !== st.tool) return false;
      if (st.onlyEvidence && !byEvent.has(e.i)) return false;
      if (st.q) {
        const q = st.q.toLowerCase();
        const hay = e.kind === "tool" ? `${e.tool} ${target(e)} ${JSON.stringify(e.input)}` : e.text || "";
        if (!hay.toLowerCase().includes(q)) return false;
      }
      return true;
    };

    function dots(n) {
      const xs = evidenceTurns.get(n) || [];
      if (!xs.length) return null;
      const shown = xs.slice(0, 3).map((x) => h("span.dot-ev", { class: `st-${SG.STATUS[statusOf(x)]?.cls || "neutral"}`, title: `${x.id} ${statusOf(x).replace(/_/g, " ")}` }));
      return h("span.ev-dots", shown, xs.length > 3 ? h("span.faint.small", `+${xs.length - 3}`) : null);
    }
    function whoOf(turn) {
      return turn.kind === "user" ? "you" : turn.kind === "meta" ? "injected" : turn.agent ? `subagent ${clip(turn.agent, 10)}` : "agent";
    }

    function eventRow(e) {
      const sel = st.event === e.i;
      const badges = (byEvent.get(e.i) || []).map((x) => x.scope === "condition" ? SG.conditionBadge(x.r.value) : SG.statusBadge(x.r.status));
      const label = e.kind === "tool" ? [h("span.ev-tool", e.tool), h("span.ev-target.mono", { title: target(e) }, target(e).replace(/\s+/g, " "))]
        : [h("span.ev-kind", e.kind === "text" ? "says" : e.meta ? "injected" : "user"), h("span.ev-target", firstLine(e.text))];
      return h("div.tree-row.ev-row", { role: "treeitem", "data-key": `e:${e.i}`, "aria-selected": String(sel), class: [sel ? "selected" : null, e.is_error ? "is-error" : null].filter(Boolean).join(" ") || null,
        onclick: () => select({ event: e.i }) },
      h("span.idx", e.i), label, e.is_error ? SG.badge("bad", "error") : null, badges.length ? h("span.ev-badges", badges) : null);
    }

    function turnRow(turn, filtered) {
      const open = filtered || st.expanded.has(turn.n);
      const sel = st.turn === turn.n && st.event == null;
      const routed = routedTo.get(turn.n) || [];
      const row = h("div.tree-row.turn-row", { role: "treeitem", "aria-expanded": String(open), "data-key": `t:${turn.n}`, "aria-selected": String(sel),
        class: [sel ? "selected" : null, offSkill.has(turn.n) ? "off" : null, turn.kind !== "agent" ? "nonagent" : null].filter(Boolean).join(" ") || null,
        onclick: () => { toggle(turn.n); select({ turn: turn.n }); } },
      h("span.chev", SG.icon(open ? "down" : "right", 14)),
      h("span.turn-n", `Turn ${turn.n}`), h("span.turn-who", whoOf(turn)),
      turn.actions.length ? h("span.faint.small", plural(turn.actions.length, "action")) : null,
      turn.errors ? SG.badge("bad", `${turn.errors} err`) : null,
      offSkill.has(turn.n) ? SG.badge("dashed", "off-skill", "No judge question pointed at this turn") : null,
      routed.length ? SG.badge("info", `evidence for ${routed.slice(0, 3).join(", ")}${routed.length > 3 ? "…" : ""}`, "The router pointed these questions at this turn") : null,
      dots(turn.n),
      turn.said ? h("span.turn-said", clip(turn.said, 140)) : turn.summary ? h("span.turn-said.mono", clip(turn.summary, 140)) : null);
      const kids = open ? ev.slice(turn.start, turn.end + 1).filter((e) => !filtered || matches(e)).map(eventRow) : [];
      return [row, kids.length ? h("div.tree-kids", { role: "group" }, kids) : null];
    }

    function renderTree() {
      const filtering = !!(st.q || st.tool || st.onlyEvidence);
      const matchTurns = filtering ? new Set(ev.filter(matches).map((e) => turnOf[e.i])) : null;
      const turns = G.turns.filter((tn) => !filtering || matchTurns.has(tn.n));
      const groups = phases.length ? phases.map((ph) => ({ ph, turns: turns.filter((tn) => tn.n >= ph.first && tn.n <= ph.last) })) : [{ ph: null, turns }];
      if (phases.length) {
        const inPhase = new Set(groups.flatMap((g) => g.turns.map((tn) => tn.n)));
        const rest = turns.filter((tn) => !inPhase.has(tn.n));
        if (rest.length) groups.push({ ph: { intent: "Other turns", first: rest[0].n, last: rest[rest.length - 1].n }, turns: rest });
      }
      const out = [];
      for (const g of groups) {
        if (!g.turns.length) continue;
        if (g.ph) out.push(h("div.phase-row", { title: "Phase named by the digest model" }, g.ph.intent, h("span.faint.small", ` turns ${g.ph.first}–${g.ph.last}`)));
        let run = [];
        const flush = () => {
          if (!run.length) return;
          const key = `${run[0].n}-${run[run.length - 1].n}`;
          if (run.length > 1 && !st.openGroups.has(key) && !(st.event != null && run.some((tn) => tn.n === turnOf[st.event]))) {
            const span = `${run[0].n}–${run[run.length - 1].n}`;
            out.push(h("div.tree-row.group-row", { role: "treeitem", "aria-expanded": "false", "data-key": `g:${key}`,
              onclick: () => { st.openGroups.add(key); renderTree(); } },
            h("span.chev", SG.icon("right", 14)), h("span", `Turns ${span}`), SG.badge("dashed", `off-skill (${run.length})`),
            h("span.turn-said", clip(run.map((tn) => tn.said).filter(Boolean).slice(0, 2).join(" · "), 120))));
          } else run.forEach((tn) => out.push(...turnRow(tn, false)));
          run = [];
        };
        for (const tn of g.turns) {
          if (!filtering && st.collapseOff && offSkill.has(tn.n)) { run.push(tn); continue; }
          flush();
          out.push(...turnRow(tn, filtering));
        }
        flush();
      }
      SG.put(treeBox, out.length ? out : SG.empty("No events match. ", h("button.link", { type: "button", onclick: () => {
        st.q = ""; st.tool = ""; st.onlyEvidence = false; filterInput.value = ""; toolSel.value = ""; evToggle.querySelector("input").checked = false; SG.setParams({ q: null }); renderTree();
      } }, "Clear filter")));
    }

    function toggle(n) { if (st.expanded.has(n)) st.expanded.delete(n); else st.expanded.add(n); renderTree(); }

    function select({ event, turn, scroll }) {
      if (event != null) { st.event = event; st.turn = turnOf[event] ?? null; if (st.turn != null) st.expanded.add(st.turn); }
      else { st.event = null; st.turn = turn ?? null; }
      if (st.tab !== "detail") st.tab = "detail";
      if (event != null) st.pane = "detail";  // on phones, picking an event shows it; picking a turn just opens it
      SG.setParams({ event: st.event, tab: null, status: null, claim: null });
      renderTree(); renderDetail(); syncPane();
      if (scroll) treeBox.querySelector(".tree-row.selected")?.scrollIntoView({ block: "center" });
    }

    // ---------------------------------------------------------- detail pane
    const tabsBox = h("nav.tabs.pane-tabs", { "aria-label": "Details" });
    const detail = h("div.detail-body");
    const resultRow = (x, opts = {}) => h("div.result", { class: st.highlight === x.id ? "hl" : null },
      x.scope === "condition" ? SG.conditionBadge(x.r.value) : SG.statusBadge(x.r.status),
      h("div.result-main",
        h("div", h("span.mono.small", (x.scope === "reference" ? "ref " : "") + x.id), " ", x.text || ""),
        x.r.reason ? h("div.result-why", x.r.reason) : null,
        h("div.result-meta",
          x.r.method ? h("span", { title: "How it was graded" }, x.r.method === "judge" ? "judge" : `${x.r.method} check`) : null,
          x.r.view ? h("span", { title: "Which view of a long trace decided it" }, x.r.view === "window" ? "decided by an excerpt" : "decided by the whole-trace skeleton") : null,
          x.span ? h("a.mono", { href: href(["s", set, "skill", t.skill], { tab: "lines", line: x.span[0] }) }, SG.lines(x.span)) : null,
          x.r.evidence != null && !opts.noEvent ? h("button.link", { type: "button", onclick: () => select({ event: x.r.evidence, scroll: true }) }, `event ${x.r.evidence}`) : null,
          x.scope === "claim" ? h("a", { href: href(["s", set, "claim", t.skill, x.id]) }, "claim →") : null)));

    function renderDetail() {
      const tabs = [
        { key: "detail", label: "Detail" },
        { key: "scores", label: "Scores", count: results.length || null },
        { key: "prompt", label: "Prompt" },
        { key: "answer", label: "Answer" },
        { key: "files", label: "Files", count: Object.keys(t.files || {}).length || null },
        { key: "info", label: "Info" },
      ];
      SG.put(tabsBox, tabs.map((x) => h("button.tab", { type: "button", "aria-current": st.tab === x.key ? "page" : null,
        onclick: () => { st.tab = x.key; SG.setParams({ tab: x.key === "detail" ? null : x.key }); renderDetail(); } }, x.label, x.count ? h("span.count", x.count) : null)));
      const body = { detail: detailTab, scores: scoresTab, prompt: () => h("div.text-block", t.task_prompt || "(no prompt)"),
        answer: () => h("div.text-block", res.text || "(no final answer)"), files: filesTab, info: infoTab }[st.tab] || detailTab;
      SG.put(detail, body());
    }

    function detailTab() {
      if (st.event != null && ev[st.event]) {
        const e = ev[st.event];
        const decided = byEvent.get(e.i) || [];
        return h("div",
          h("div.detail-title", h("strong", `Event ${e.i}`), " · ", e.kind === "tool" ? h("span.ev-tool", e.tool) : e.kind === "text" ? "agent message" : e.meta ? "injected text" : "user message",
            e.raw_tool && e.raw_tool !== e.tool ? h("span.faint.small", ` (${e.raw_tool})`) : null, e.is_error ? [" ", SG.badge("bad", "error")] : null,
            turnOf[e.i] != null ? h("button.link.push", { type: "button", onclick: () => select({ turn: turnOf[e.i] }) }, `turn ${turnOf[e.i]}`) : null),
          decided.length ? h("section.decided", h("h4", "Decided here"), decided.map((x) => resultRow(x, { noEvent: true }))) : null,
          e.kind === "tool" ? [
            h("h4", "Input"), h("pre.code-block", JSON.stringify(e.input, null, 2)),
            h("h4", e.is_error ? "Output (error)" : "Output"), h("pre.code-block", { class: e.is_error ? "is-error" : null }, e.output || "(no output)"),
          ] : h("div.text-block", e.text || ""));
      }
      if (st.turn != null) {
        const turn = G.turns.find((x) => x.n === st.turn);
        if (turn) {
          const routed = routedTo.get(turn.n) || [];
          return h("div",
            h("div.detail-title", h("strong", `Turn ${turn.n}`), " · ", whoOf(turn), h("span.faint.small", ` · events ${turn.start}–${turn.end}`)),
            offSkill.has(turn.n) ? SG.notice("", "Off-skill: no judge question pointed at this turn, so it is likely unrelated to the skill.") : null,
            turn.said ? h("div.text-block", turn.said) : null,
            turn.summary ? [h("h4", "Actions"), h("p.mono.small", turn.summary)] : null,
            turn.files?.length ? [h("h4", "Files written"), h("p.mono.small", turn.files.join(", "))] : null,
            routed.length ? [h("h4", "Questions pointed here"), h("p.small", routed.join(", "))] : null,
            (evidenceTurns.get(turn.n) || []).length ? [h("h4", "Decided in this turn"), (evidenceTurns.get(turn.n) || []).map((x) => resultRow(x))] : null);
        }
      }
      const failing = results.filter((x) => ["violated", "missed", "out_of_order", "unclear"].includes(x.r.status));
      return h("div",
        h("p.faint", "Select an event or turn in the timeline. Use j / k to move, Enter to open a turn, / to filter."),
        failing.length ? [h("h4", "Needs a look"), failing.map((x) => resultRow(x))] : C ? h("p.small", "Every claim was satisfied or didn't apply.") : null);
    }

    function scoresTab() {
      if (!C) return SG.empty("Not graded yet: run ", SG.code("skillgraph check <dir>"), ".");
      const order = (a, b) => SG.STATUS_ORDER.indexOf(statusOf(a)) - SG.STATUS_ORDER.indexOf(statusOf(b)) || a.id.localeCompare(b.id, undefined, { numeric: true });
      const shown = (scope) => results.filter((x) => x.scope === scope && (!st.status || statusOf(x) === st.status)).sort(order);
      const chips = h("div.filters", ["", ...SG.STATUS_ORDER.filter((s) => counts[s])].map((s) => h("button.filter", { type: "button", "aria-pressed": String(st.status === s),
        onclick: () => { st.status = s; SG.setParams({ status: s || null }); renderDetail(); } }, s ? `${SG.STATUS[s].label} ${counts[s]}` : "All")));
      const group = (title, xs) => (xs.length ? [h("h4", title), xs.map((x) => resultRow(x))] : null);
      return h("div", chips,
        win?.unrouted?.length ? h("p.small.faint", `No turn was found for ${win.unrouted.join(", ")}; they were judged on the whole trace.`) : null,
        group("Skill claims", shown("claim")), group("Branch conditions", st.status ? [] : shown("condition")), group("Reference steps", shown("reference")));
    }

    function filesTab() {
      const files = Object.entries(t.files || {});
      return h("div",
        t.files_reconstructed ? h("p.small.faint", "Rebuilt from the Write and Edit calls in the trace; files the trace never shows in full are empty.") : null,
        files.length ? files.map(([name, content]) => h("details.file", h("summary.mono", name), h("pre.code-block", content == null ? "(binary or not shown in full)" : content))) : h("p.faint", "No final files."),
        successChecks.length ? [h("h4", "Success checks"), successChecks.map(([n, ok]) => h("div.result", ok ? SG.badge("good", "✓ passed") : SG.badge("bad", "✗ failed"), h("span", n)))] : null,
        t.git && (t.git.log || []).length ? [h("h4", "Git"), h("pre.code-block", [`tags: ${(t.git.tags || []).join(", ") || "none"}`, ...(t.git.status || []).map((s) => `status: ${s}`), ...(t.git.log || [])].join("\n"))] : null);
    }

    function infoTab() {
      const rows = [
        ["Run", path], ["Model", t.model], ["Trigger", t.trigger], ["Enforced rules", t.enforce ? "yes" : "no"],
        ["Skill loaded", String(t.skill_loaded)], ["Wall time", t.wall_s != null ? `${t.wall_s}s` : "–"],
        ["Blocked tool calls", String((t.blocked || []).length)], ["Harness", t.init?.harness || "–"],
        ["Judge mode", C?.judge_mode || (C ? "full" : "not graded")], ["Judge items", C ? String(C.judge_items) : "–"],
        win ? ["Router", `${win.router_model || "lexical only"} (${usd(win.router_cost_usd)})`] : null,
        G.intents ? ["Phases named by", `${G.intents.model}, which never sees the skill; the judge never sees this text`] : null,
        t.episode ? ["Episode", `${t.episode.index} of ${t.episode.of} (session events ${t.episode.start_event}–${t.episode.end_event})`] : null,
        ["Digest", `${G.stats.events} events · ${G.stats.turns} turns · ${G.stats.tool_calls} tool calls · ~${tok(G.stats.tokens_full)} tokens (skeleton ~${tok(G.stats.tokens_skeleton)})`],
      ].filter(Boolean);
      return h("dl.kv", rows.map(([k, v]) => [h("dt", k), h("dd", v)]));
    }

    // ---------------------------------------------------------- layout, mobile panes, keys
    const layout = h("div.trace-layout", { "data-pane": st.pane });
    const seg = SG.segmented([{ key: "timeline", label: "Timeline" }, { key: "detail", label: "Detail" }], st.pane, (k) => { st.pane = k; syncPane(); });
    function syncPane() {
      layout.dataset.pane = st.pane;
      seg.querySelectorAll("button").forEach((b, i) => b.setAttribute("aria-selected", String((i ? "detail" : "timeline") === st.pane)));
    }
    SG.put(SG.view, header, h("div.only-mobile", seg),
      SG.put(layout,
        h("section.card.tree-pane", { "aria-label": "Timeline" },
          h("div.tree-toolbar", filterInput, toolSel, evToggle, offToggle),
          treeBox,
          G.stats.events > 2000 ? h("p.small.faint.pad", "Large trace: events render as you open turns.") : null),
        h("section.card.detail-pane", { "aria-label": "Details" }, tabsBox, detail)));
    renderTree(); renderDetail();
    if (st.event != null) setTimeout(() => treeBox.querySelector(".tree-row.selected")?.scrollIntoView({ block: "center" }), 0);

    const onKey = (e) => {
      const el = e.target instanceof Element ? e.target : null;
      if (el?.closest("input, select, textarea") || e.metaKey || e.ctrlKey || e.altKey || document.querySelector(".drawer-root")) {
        if (e.key === "Escape" && e.target === filterInput) filterInput.blur();
        return;
      }
      if (e.key === "/") { e.preventDefault(); filterInput.focus(); return; }
      const rows = [...treeBox.querySelectorAll(".tree-row")];
      if (!rows.length) return;
      const cur = rows.findIndex((r) => r.classList.contains("selected"));
      const activate = (row) => {
        const [kind, id] = row.dataset.key.split(":");
        if (kind === "e") select({ event: Number(id), scroll: true });
        else if (kind === "t") select({ turn: Number(id), scroll: true });
        else row.click();
      };
      if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); activate(rows[Math.min(rows.length - 1, cur + 1)]); }
      else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); activate(rows[Math.max(0, cur - 1)]); }
      else if (e.key === "Enter" && cur >= 0) {
        e.preventDefault();
        const [kind, id] = rows[cur].dataset.key.split(":");
        if (kind === "t") { toggle(Number(id)); select({ turn: Number(id) }); } else if (kind === "g") rows[cur].click();
      }
    };
    document.addEventListener("keydown", onKey);
    SG.onLeave(() => document.removeEventListener("keydown", onKey));
  };
})();
