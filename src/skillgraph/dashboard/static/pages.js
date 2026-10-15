/* skillgraph dashboard: pages (Overview, Recommendations, Skills, Skill, Claim, Runs, Compare, Glossary). */
"use strict";

(() => {
  const SG = window.SG;
  const { h, href, pct, usd, tok, clip, sum, lines, plural } = SG;
  const P = (SG.pages = SG.pages || {});

  const setName = (id) => { const s = SG.SETS.find((x) => x.id === id); return s ? SG.setLabel(s) : id; };
  const skillOf = (S, name) => S.data.skills.find((s) => s.skill === name);
  const findClaim = (sk, id) => sk.claims.find((c) => c.id === id);
  const runHref = (set, path, params) => href(["s", set, "run", path], params);
  const rateText = (r) => (r && r.n ? `${Math.round((r.followed ?? 0) * r.n)}/${r.n}` : "–");
  const ratePct = (rs) => (rs.length ? rs.filter((r) => r.all_success).length / rs.length : null);
  const pageHead = (title, meta, actions) => h("header.page-head", h("div", h("h1", title), meta ? h("p.meta", meta) : null), actions ? h("div.page-actions", actions) : null);

  // ------------------------------------------------------------ recommendation card
  function recCard(S, r, compact) {
    const lineLink = r.lines ? h("a.badge.outline.mono", { href: href(["s", S.id, "skill", r.skill], { tab: "lines", line: r.lines[0] }), title: "Show in SKILL.md" }, lines(r.lines)) : null;
    const prio = { high: ["bad", "High"], medium: ["warn", "Medium"], low: ["neutral", "Low"] }[r.priority];
    return h("article.rec", { class: `prio-${r.priority}` },
      h("div.rec-head", SG.badge(prio[0], prio[1]), h("strong.rec-action", r.action),
        h("a.rec-skill", { href: href(["s", S.id, "skill", r.skill]) }, r.skill), lineLink,
        r.claim ? h("span.mono.faint", r.claim) : null, r.ref ? h("span.mono.faint", `ref ${r.ref}`) : null),
      r.quote && !compact ? h("blockquote.quote.mono", clip(r.quote, 400)) : h("p.rec-title", clip(r.title, 200)),
      compact ? null : [
        h("p.rec-why", r.why),
        r.notes?.length ? h("ul.rec-notes", r.notes.map((n) => h("li", clip(n, 240)))) : null,
        h("p.rec-do", h("span.label", "Do: "), r.do.startsWith("skillgraph ") ? SG.code(r.do) : r.do),
      ],
      h("div.rec-links", r.links.map(([label, target]) => h("a", { href: target }, label + " →"))));
  }

  // ------------------------------------------------------------ overview
  P.overview = async (ctx) => {
    const S = await SG.setData(ctx.set);
    if (!ctx.isCurrent()) return;
    const D = S.data, runs = S.runs, set = ctx.set;
    const recs = SG.recommend(S);
    const highs = recs.filter((r) => r.priority === "high");
    SG.chrome({ set, page: "overview", S, title: setName(set), crumbs: [[setName(set)]] });

    const withSkill = runs.filter((r) => r.variant === "skill" && r.all_success != null);
    const without = runs.filter((r) => r.variant === "baseline" && r.all_success != null);
    const q = {};
    D.skills.forEach((s) => Object.entries(s.totals.quadrants || {}).forEach(([k, v]) => { q[k] = (q[k] || 0) + v; }));
    const nClaims = sum(D.skills.map((s) => s.claims.length));
    const weak = sum(D.skills.map((s) => s.claims.filter((c) => c.weak).length));
    const stale = sum(D.skills.map((s) => s.stale_checks || 0)) + D.skills.filter((s) => s.stale).length;
    const agent = sum(runs.map((r) => r.cost_usd)), judge = sum(runs.map((r) => r.judge_cost_usd));
    const hasBaseline = without.length > 0;
    const actionCounts = {};
    highs.forEach((r) => { actionCounts[r.action] = (actionCounts[r.action] || 0) + 1; });

    const kpi = (label, value, sub, target, helpTerm) => h(target ? "a.kpi.card" : "div.kpi.card", { href: target },
      h("div.kpi-label", label, helpTerm ? SG.help(helpTerm) : null), h("div.kpi-value", value), sub ? h("div.kpi-sub", sub) : null);

    SG.put(SG.view,
      pageHead(setName(set), [D.model, D.trigger && `trigger ${D.trigger}`, `${plural(runs.length, "run")}`, `${plural(D.skills.length, "skill")}`, D.generated && `built ${D.generated}`].filter(Boolean).join(" · "),
        h("a.btn", { href: href(["s", set, "runs"], { sort: "cost", dir: "desc" }), title: "Runs, most expensive first" }, `Spend ${usd(agent + judge)}`)),
      S.error ? SG.notice("bad", S.error) : null,
      D.skills.some((s) => s.stale) ? SG.notice("warn", "A SKILL.md changed since its claims were extracted; line numbers may be off. Run ", SG.code("skillgraph extract"), ".") : null,
      !D.skills.length ? SG.notice("", "No graded runs yet. Run ", SG.code(`skillgraph check ${D.runset || "<dir>"}`), " to grade them; the ", h("a", { href: href(["s", set, "runs"]) }, "runs"), " are listed already.") : null,
      D.skills.length ? h("section.kpis",
        kpi("Task success", withSkill.length ? `${pct(ratePct(withSkill))} with the skill` : "–",
          hasBaseline ? h("span", `${pct(ratePct(without))} without · `, h("span.link", "see failures →")) : "no runs without the skill to compare",
          href(["s", set, "runs"], { variant: "skill", outcome: "failed" })),
        kpi(hasBaseline ? "Lines that work" : "Rules followed", `${(q.working || 0) + (q.followed || 0)} of ${nClaims}`,
          `${q.inert || 0} inert · ${q.ignored || 0} ignored · ${q.interfering || 0} interfering · ${q.untested || 0} untested`, href(["s", set, "skills"]), "verdict"),
        kpi("Needs attention", plural(highs.length, "item"),
          highs.length ? Object.entries(actionCounts).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(" · ") : "nothing urgent", href(["s", set, "recommendations"])),
        kpi("Confidence", weak ? `${plural(weak, "weak verdict")}` : "no weak verdicts",
          stale ? `${stale} stale` : "nothing stale", href(["s", set, "recommendations"], { action: "Add runs" }), "weak")) : null,
      D.skills.length ? h("section",
        h("div.section-head", h("h2", "Skills"), h("span.hint", hasBaseline ? "Verdicts compare runs with and without the skill." : "No baseline runs: verdicts say followed or ignored.")),
        h("div.skill-list.card", D.skills.map((sk) => skillRow(S, sk, recs)))) : null,
      recs.length ? h("section",
        h("div.section-head", h("h2", "What to change first"), h("a", { href: href(["s", set, "recommendations"]) }, `See all ${recs.length} →`)),
        h("div.rec-list", recs.slice(0, 3).map((r) => recCard(S, r, true)))) : null,
    );
  };

  function skillRow(S, sk, recs) {
    const set = S.id;
    const health = SG.health(sk);
    const parts = SG.verdictParts(sk.totals.quadrants);
    const mine = recs.filter((r) => r.skill === sk.skill);
    const errors = sk.lint.filter((f) => f.severity === "error").length;
    const R = sk.reference;
    return h("article.skill-row",
      h("div.skill-row-main",
        h("div.skill-row-title", h("h3", h("a", { href: href(["s", set, "skill", sk.skill]) }, sk.skill)), SG.badge("outline", sk.classification)),
        h("p.health", { class: `tone-${health.tone}` }, health.text),
        h("div.verdict-line", SG.bar(parts),
          h("div.legend", parts.filter((p) => p.n).map((p) => h("a", { href: href(["s", set, "skill", sk.skill], { tab: "claims", verdict: p.label.toLowerCase() }) },
            h("i", { class: p.cls }), `${p.label} ${p.n}`))))),
      h("div.skill-row-side",
        h("div.stat", h("span.faint", "success"), ` ${pct(sk.totals.success.skill)} with`, sk.totals.success.baseline != null ? ` · ${pct(sk.totals.success.baseline)} without` : ""),
        h("div.stat.faint", `${plural(sk.totals.runs, "run")} · ${usd(sk.totals.cost_usd)}`),
        R ? h("div.stat", h("a", { href: href(["s", set, "skill", sk.skill], { tab: "reference" }) }, `Reference: ${pct(R.score?.coverage)} covered`),
          (R.diagnosis_counts?.gap ? [" · ", SG.badge("bad", `${R.diagnosis_counts.gap} gap`)] : null)) : null,
        h("div.row-tags", sk.stale ? SG.badge("warn", "stale") : null, errors ? h("a.badge.bad", { href: href(["s", set, "skill", sk.skill], { tab: "lint" }) }, `${errors} lint error`) : null),
        mine.length ? h("a.small", { href: href(["s", set, "recommendations"], { skill: sk.skill }) }, `${plural(mine.length, "recommendation")} →`) : null));
  }

  // ------------------------------------------------------------ recommendations
  P.recommendations = async (ctx) => {
    const S = await SG.setData(ctx.set);
    if (!ctx.isCurrent()) return;
    const set = ctx.set;
    const all = SG.recommend(S);
    SG.chrome({ set, page: "recommendations", S, title: "Recommendations", crumbs: [[setName(set), href(["s", set])], ["Recommendations"]] });
    const state = { skill: ctx.params.get("skill") || "", action: ctx.params.get("action") || "" };
    const list = h("div.rec-list");
    const count = h("p.meta");
    const paint = () => {
      const items = all.filter((r) => (!state.skill || r.skill === state.skill) && (!state.action || r.action === state.action));
      const by = (p) => items.filter((r) => r.priority === p).length;
      count.textContent = `${plural(items.length, "item")} · ${by("high")} high · ${by("medium")} medium · ${by("low")} low`;
      SG.put(list, items.length ? items.map((r) => recCard(S, r, false)) : SG.empty("Nothing to change here: every claim is working or untested by design."));
    };
    const select = (label, key, options) => h("label.field", h("span", label),
      h("select.control", { onchange: (e) => { state[key] = e.target.value; SG.setParams({ [key]: e.target.value }); paint(); } },
        h("option", { value: "" }, "all"), options.map((o) => h("option", { value: o, selected: state[key] === o }, o))));
    SG.put(SG.view,
      pageHead("Recommendations", null),
      h("p.lede", "What to change first, ranked by impact. Built from the verdicts, the reference process, lint and ablations; nothing here calls a model."),
      h("div.toolbar", select("Skill", "skill", S.data.skills.map((s) => s.skill)), select("Action", "action", [...new Set(all.map((r) => r.action))]), count),
      list);
    paint();
  };

  // ------------------------------------------------------------ skills list
  P.skills = async (ctx) => {
    const S = await SG.setData(ctx.set);
    if (!ctx.isCurrent()) return;
    const set = ctx.set;
    SG.chrome({ set, page: "skills", S, title: "Skills", crumbs: [[setName(set), href(["s", set])], ["Skills"]] });
    SG.put(SG.view, pageHead("Skills", `${plural(S.data.skills.length, "skill")} graded in this run set`),
      SG.table({
        minWidth: 860,
        rowHref: (sk) => href(["s", set, "skill", sk.skill]),
        empty: "No graded skills yet.",
        cols: [
          { key: "skill", label: "Skill", render: (sk) => h("div", h("a.strong", { href: href(["s", set, "skill", sk.skill]) }, sk.skill), h("div.faint.small", SG.health(sk).text)) },
          { key: "verdicts", label: "Verdicts", render: (sk) => h("div.cell-bar", SG.bar(SG.verdictParts(sk.totals.quadrants)), h("span.faint.small", `${sk.claims.length} claims`)) },
          { key: "success", label: "Success with · without", render: (sk) => `${pct(sk.totals.success.skill)} · ${pct(sk.totals.success.baseline)}` },
          { key: "ref", label: "Reference", render: (sk) => (sk.reference ? `${pct(sk.reference.score?.coverage)} covered` : "–") },
          { key: "runs", label: "Runs", align: "right", render: (sk) => sk.totals.runs },
          { key: "cost", label: "Cost", align: "right", render: (sk) => usd(sk.totals.cost_usd) },
          { key: "warn", label: "Warnings", render: (sk) => h("div.row-tags", sk.stale ? SG.badge("warn", "stale") : null,
            sk.lint.some((f) => f.severity === "error") ? SG.badge("bad", "lint error") : null) },
        ],
        rows: S.data.skills,
      }));
  };

  // ------------------------------------------------------------ skill
  P.skill = async (ctx) => {
    const S = await SG.setData(ctx.set);
    if (!ctx.isCurrent()) return;
    const set = ctx.set, name = ctx.skill, params = ctx.params;
    const sk = skillOf(S, name);
    SG.chrome({ set, page: "skill", skillName: name, S, title: name, crumbs: [[setName(set), href(["s", set])], ["Skills", href(["s", set, "skills"])], [name]] });
    if (!sk) { SG.put(SG.view, SG.notice("warn", `No graded runs for ${name} in this run set.`)); return; }
    const tab = params.get("tab") || "lines";
    const R = sk.reference;
    const counts = sk.totals.quadrants || {};
    const tabHref = (key) => href(["s", set, "skill", name], { tab: key });
    const header = h("div",
      pageHead(name, `${sk.classification} · ${plural(sk.claims.length, "claim")} · ${plural(sk.totals.runs, "run")} · ${usd(sk.totals.cost_usd)}`,
        [h("a.btn", { href: href(["s", set, "runs"], { skill: name }) }, "Runs →"),
          h("a.btn", { href: SG.isStatic ? `report-${set}.html` : `/report/${SG.enc(set)}`, target: "_blank", rel: "noopener" }, "Claim graph ↗")]),
      sk.summary ? h("p.lede", sk.summary) : null,
      sk.stale ? SG.notice("warn", "SKILL.md changed since its claims were extracted; line numbers may be off. Run ", SG.code("skillgraph extract"), ".") : null,
      sk.stale_checks ? SG.notice("warn", `${plural(sk.stale_checks, "run")} were graded against older claims. Run `, SG.code("skillgraph check"), ".") : null,
      h("div.summary-strip",
        h("p.health", { class: `tone-${SG.health(sk).tone}` }, SG.health(sk).text),
        h("div.chips", SG.VERDICT_ORDER.filter((k) => counts[k]).map((k) => {
          const b = SG.verdictBadge(k, href(["s", set, "skill", name], { tab: "claims", verdict: k }));
          b.append(` ${counts[k]}`);
          return b;
        }))),
      SG.tabs([
        { key: "lines", label: "Lines" },
        { key: "claims", label: "Claims", count: sk.claims.length },
        sk.conditions.length ? { key: "conditions", label: "Conditions", count: sk.conditions.length } : null,
        sk.ablations.length ? { key: "ablations", label: "Ablations", count: sk.ablations.length } : null,
        R ? { key: "reference", label: "Reference" } : null,
        { key: "lint", label: "Lint", count: sk.lint.length },
      ], tab, tabHref));
    const body = h("div.tab-body");
    SG.put(SG.view, header, body);
    const render = { lines: linesTab, claims: claimsTab, conditions: conditionsTab, ablations: ablationsTab, reference: referenceTab, lint: lintTab }[tab] || linesTab;
    render(S, sk, body, params);
    if (params.get("claim")) openClaim(S, sk, params.get("claim"));
  };

  function openClaim(S, sk, id) {
    const c = findClaim(sk, id);
    if (!c) return;
    SG.setParams({ claim: id });
    SG.drawer(h("span", h("span.mono", c.id), " · ", sk.skill), claimDetail(S, sk, c, true), () => SG.setParams({ claim: null }));
  }
  SG.openClaim = openClaim;

  function claimCard(S, sk, c) {
    return h("button.claim-card", { type: "button", onclick: () => openClaim(S, sk, c.id), "data-claim": c.id },
      h("div.claim-card-head", SG.verdictBadge(c.quadrant), h("span.mono", c.id), SG.badge("outline", c.kind),
        h("span.mono.faint", lines(c.span)), c.weak ? SG.badge("warn outline", "weak", "Fewer than 3 runs per variant") : null,
        c.failure_mode ? SG.badge("bad outline", c.failure_mode.replace(/_/g, " ")) : null),
      h("p.claim-card-text", c.text),
      h("div.claim-card-rates", h("span", "with ", SG.score(c.rates.skill, "with")), c.rates.baseline ? h("span", "without ", SG.score(c.rates.baseline, "without")) : null));
  }

  function linesTab(S, sk, body, params) {
    let verdict = params.get("verdict") || null;
    let line = Number(params.get("line")) || null;
    const lineVerdict = new Map();
    for (const c of sk.claims) for (let ln = c.span[0]; ln <= c.span[1]; ln++) {
      const cur = lineVerdict.get(ln);
      if (!cur || SG.VERDICT_ORDER.indexOf(c.quadrant) < SG.VERDICT_ORDER.indexOf(cur)) lineVerdict.set(ln, c.quadrant);
    }
    const lintLines = new Set();
    sk.lint.filter((f) => f.severity === "error" && f.span).forEach((f) => { for (let ln = f.span[0]; ln <= f.span[1]; ln++) lintLines.add(ln); });
    const panes = h("div.panes", { "data-show": line ? "claims" : "text" });
    const lineEls = sk.lines.map((text, k) => {
      const ln = k + 1, v = lineVerdict.get(ln);
      return h(v ? "button.line" : "div.line", {
        type: v ? "button" : null, class: [v ? `has-claim q-${v}` : null, lintLines.has(ln) ? "lint-error" : null].filter(Boolean).join(" ") || null,
        "data-ln": ln, title: v ? `${SG.VERDICT[v].label}: show its claims` : lintLines.has(ln) ? "Lint error on this line" : null,
        onclick: v ? () => { line = line === ln ? null : ln; SG.setParams({ line }); panes.dataset.show = line ? "claims" : "text"; paint(); } : null,
      }, h("span.ln", ln), h("span.tx", text || " "));
    });
    const listHead = h("div.pane-head");
    const list = h("div.claim-list");
    const counts = {};
    sk.claims.forEach((c) => { counts[c.quadrant] = (counts[c.quadrant] || 0) + 1; });
    const filters = h("div.filters", [null, ...SG.VERDICT_ORDER.filter((k) => counts[k])].map((k) => h("button.filter", {
      type: "button", "data-v": k || "", title: k ? SG.VERDICT[k].tip : null,
      onclick: () => { verdict = k; line = null; SG.setParams({ verdict: k, line: null }); paint(); },
    }, k ? `${SG.VERDICT[k].label} ${counts[k]}` : `All ${sk.claims.length}`)));
    function paint() {
      filters.querySelectorAll(".filter").forEach((b) => b.setAttribute("aria-pressed", String((b.dataset.v || null) === verdict)));
      lineEls.forEach((el, k) => el.classList.toggle("hl", line === k + 1));
      const shown = sk.claims.filter((c) => (!verdict || c.quadrant === verdict) && (!line || (c.span[0] <= line && line <= c.span[1])));
      SG.put(listHead, line ? h("span", `Claims on L${line} (${shown.length}) `, h("button.link", { type: "button", onclick: () => { line = null; SG.setParams({ line: null }); panes.dataset.show = "text"; paint(); } }, "show all"))
        : h("span", `${verdict ? SG.VERDICT[verdict].label : "All"} claims (${shown.length})`, h("span.faint", " · click a line to filter")));
      SG.put(list, shown.length ? shown.map((c) => claimCard(S, sk, c)) : SG.empty("No claims match."));
    }
    const seg = SG.segmented([{ key: "text", label: "Text" }, { key: "claims", label: "Claims" }], panes.dataset.show, (k) => {
      panes.dataset.show = k; seg.querySelectorAll("button").forEach((b, i) => b.setAttribute("aria-selected", String((i === 0 ? "text" : "claims") === k)));
    });
    SG.put(body, filters, h("div.only-mobile", seg),
      SG.put(panes,
        h("section.card.pane.pane-text", { "aria-label": "SKILL.md" }, h("div.pane-head", "SKILL.md ", h("span.faint", "· the left edge shows each line's verdict")), h("div.lines", lineEls)),
        h("section.card.pane.pane-claims.sticky", { "aria-label": "Claims" }, listHead, list)));
    paint();
    if (line) lineEls[line - 1]?.scrollIntoView({ block: "center" });
  }

  function claimsTab(S, sk, body, params) {
    const set = S.id, name = sk.skill;
    const st = { verdict: params.get("verdict") || "", kind: params.get("kind") || "", check: params.get("check") || "", q: params.get("q") || "",
      sort: params.get("sort") || "verdict", dir: params.get("dir") || "asc" };
    const ablates = sk.variants.filter((v) => v.startsWith("ablate-"));
    const out = h("div");
    const paint = () => {
      const q = st.q.toLowerCase();
      const rows = sk.claims.filter((c) => (!st.verdict || c.quadrant === st.verdict) && (!st.kind || c.kind === st.kind)
        && (!st.check || c.check.type === st.check) && (!q || `${c.id} ${c.text}`.toLowerCase().includes(q)));
      SG.put(out, SG.table({
        minWidth: 900, sort: st.sort, dir: st.dir,
        onSort: (k, d) => { st.sort = k; st.dir = d; SG.setParams({ sort: k, dir: d }); paint(); },
        empty: "No claims match these filters.",
        cols: [
          { key: "verdict", label: "Verdict", sort: (c) => SG.VERDICT_ORDER.indexOf(c.quadrant), render: (c) => SG.verdictBadge(c.quadrant) },
          { key: "id", label: "ID", sort: (c) => Number(c.id.replace(/\D/g, "")) || c.id, render: (c) => h("button.link.mono", { type: "button", onclick: () => openClaim(S, sk, c.id) }, c.id) },
          { key: "kind", label: "Kind", sort: (c) => c.kind, render: (c) => c.kind },
          { key: "lines", label: "Lines", sort: (c) => c.span[0], render: (c) => h("a.mono", { href: href(["s", set, "skill", name], { tab: "lines", line: c.span[0] }) }, lines(c.span)) },
          { key: "text", label: "Claim", render: (c) => h("span.cell-text", { title: c.text }, c.text) },
          { key: "with", label: "With", sort: (c) => c.skill_rate, render: (c) => SG.score(c.rates.skill, "with", href(["s", set, "runs"], { skill: name, claim: c.id, variant: "skill" })) },
          { key: "without", label: "Without", sort: (c) => c.baseline_rate, render: (c) => SG.score(c.rates.baseline, "without", c.rates.baseline ? href(["s", set, "runs"], { skill: name, claim: c.id, variant: "baseline" }) : null) },
          ablates.length ? { key: "abl", label: "Lines removed", render: (c) => h("div.abl-list", ablates.map((v) => c.rates[v] ? h("span.abl", { title: `Followed with ${v.replace("ablate-", "lines ")} deleted` },
            h("span.mono.faint", v.replace(/^ablate-L(\d+)-(\d+)$/, (_, a, b) => (a === b ? `L${a}` : `L${a}–${b}`))), SG.score(c.rates[v], "other")) : null)) } : null,
          { key: "check", label: "Check", sort: (c) => c.check.type, render: (c) => h("span.faint", c.check.type) },
          { key: "conf", label: "Notes", render: (c) => h("div.row-tags", c.weak ? SG.badge("warn outline", "weak") : null, c.failure_mode ? SG.badge("bad outline", c.failure_mode.replace(/_/g, " ")) : null) },
        ].filter(Boolean),
        rows,
        onRow: (c) => openClaim(S, sk, c.id),
      }));
    };
    const sel = (label, key, options) => h("label.field", h("span", label), h("select.control", { onchange: (e) => { st[key] = e.target.value; SG.setParams({ [key]: e.target.value }); paint(); } },
      h("option", { value: "" }, "all"), options.map(([v, l]) => h("option", { value: v, selected: st[key] === v }, l))));
    SG.put(body,
      h("div.toolbar",
        sel("Verdict", "verdict", SG.VERDICT_ORDER.filter((k) => sk.claims.some((c) => c.quadrant === k)).map((k) => [k, SG.VERDICT[k].label])),
        sel("Kind", "kind", [...new Set(sk.claims.map((c) => c.kind))].map((k) => [k, k])),
        sel("Check", "check", [...new Set(sk.claims.map((c) => c.check.type))].map((k) => [k, k])),
        h("label.field.grow", h("span", "Search"), h("input.control", { type: "search", value: st.q, placeholder: "id or text", oninput: (e) => { st.q = e.target.value; SG.setParams({ q: st.q }); paint(); } }))),
      out);
    paint();
  }

  function conditionsTab(S, sk, body) {
    SG.put(body, h("p.lede", "A condition decides which branch of the skill applies. Claims on the other branch are n/a in that run."),
      SG.table({
        minWidth: 640,
        cols: [
          { key: "id", label: "ID", render: (k) => h("span.mono", k.id) },
          { key: "text", label: "Condition", render: (k) => h("div", k.text, h("div.faint.small", lines(k.span))) },
          ...sk.variants.map((v) => ({ key: v, label: `Held (${v})`, align: "right", render: (k) => (k.held[v] ? `${k.held[v].true}/${k.held[v].n}` : "–") })),
          { key: "gated", label: "Claims it gates", render: (k) => h("div.row-tags", sk.claims.filter((c) => c.when?.cond === k.id || (c.when_all || []).some((g) => g.cond === k.id))
            .map((c) => h("button.link.mono", { type: "button", onclick: () => openClaim(S, sk, c.id) }, c.id))) },
        ],
        rows: sk.conditions,
      }));
  }

  function ablationsTab(S, sk, body) {
    SG.put(body, h("p.lede", "These lines were deleted and the tasks rerun. If behavior changed, the lines were doing work."),
      h("div.stack", sk.ablations.map((a) => h("article.card.card-pad",
        h("div.row", h("strong.mono", lines(a.remove[0])), SG.badge(SG.ABLATION[a.verdict]?.cls, a.verdict, SG.ABLATION[a.verdict]?.tip),
          h("span.faint", a.note), h("a.push", { href: href(["s", S.id, "runs"], { skill: sk.skill, variant: a.variant }) }, `${plural(a.runs, "run")} →`)),
        h("pre.quote.mono", a.text),
        h("p.small", `Task success: ${pct(a.success_skill)} with these lines → ${pct(a.success_ablated)} without them.`),
        a.effects.length ? SG.table({ minWidth: 420, cols: [
          { key: "id", label: "Claim", render: (e) => h("button.link.mono", { type: "button", onclick: () => openClaim(S, sk, e.id) }, e.id) },
          { key: "from", label: "With the lines", align: "right", render: (e) => pct(e.from) },
          { key: "to", label: "Without", align: "right", render: (e) => pct(e.to) },
          { key: "d", label: "Change", align: "right", render: (e) => h("span", { class: e.delta < 0 ? "text-bad" : e.delta > 0 ? "text-good" : null }, `${e.delta > 0 ? "+" : ""}${Math.round(e.delta * 100)} pts`) },
          { key: "aff", label: "", render: (e) => (e.affected ? SG.badge("outline", "on these lines") : "") },
        ], rows: a.effects }) : h("p.faint.small", "No claim changed by 50 points or more.")))));
  }

  function referenceTab(S, sk, body, params) {
    const R = sk.reference;
    let diag = params.get("diag") || "";
    const step = params.get("step");
    const out = h("div");
    const paint = () => {
      const rows = R.nodes.filter((n) => !diag || n.diagnosis === diag);
      SG.put(out, SG.table({
        minWidth: 820,
        cols: [
          { key: "d", label: "Diagnosis", render: (n) => SG.diagBadge(n.diagnosis) },
          { key: "step", label: "Step", render: (n) => h("details.step", { open: step === n.id || null, id: `step-${n.id}` },
            h("summary", h("span.mono.faint", n.id), " ", n.text),
            h("p.small", R.diagnoses?.[n.diagnosis] || SG.DIAG[n.diagnosis]?.tip || ""),
            n.note ? h("p.small.faint", n.note) : null,
            n.held ? h("p.small", "Branches taken: ", Object.entries(n.held).map(([b, k]) => `${b} ${k}`).join(" · ")) : null) },
          { key: "kind", label: "Kind", render: (n) => n.kind },
          { key: "rel", label: "In the skill", render: (n) => h("div", n.relation, n.claims?.length ? h("div.row-tags", n.claims.map((id) => h("button.link.mono", { type: "button", onclick: () => openClaim(S, sk, id) }, id))) : null) },
          { key: "with", label: "With", align: "right", render: (n) => (n.kind === "decision" ? "–" : h("a", { href: href(["s", S.id, "runs"], { skill: sk.skill, claim: `ref:${n.id}`, variant: "skill" }) }, pct(n.rate_with))) },
          { key: "without", label: "Without", align: "right", render: (n) => pct(n.rate_without) },
        ],
        rows,
      }));
    };
    const dc = R.diagnosis_counts || {};
    SG.put(body,
      h("p.lede", `“${R.name}”: ${pct(R.score?.coverage)} of the process is in the skill. Steps done ${pct(R.correctness?.skill)} with the skill${R.correctness?.baseline != null ? `, ${pct(R.correctness.baseline)} without` : ""}. ${R.paths.filter((p) => p.tested).length} of ${R.paths.length} paths tested.`),
      R.coverage_stale ? SG.notice("warn", "The coverage lockfile is stale; run ", SG.code("skillgraph coverage"), ".") : null,
      h("div.filters", [["", `All ${R.nodes.length}`], ...Object.entries(dc).map(([k, v]) => [k, `${k} ${v}`])].map(([k, label]) => h("button.filter", {
        type: "button", "aria-pressed": String(diag === k), title: k ? SG.DIAG[k]?.tip : null,
        onclick: (e) => { diag = k; SG.setParams({ diag: k }); e.target.parentNode.querySelectorAll(".filter").forEach((b) => b.setAttribute("aria-pressed", String(b === e.target))); paint(); },
      }, label))),
      out,
      h("h3.sub", "Paths through the process"),
      h("div.card.card-pad.paths", R.paths.map((p) => h("div.path", p.tested ? SG.badge("good", "tested") : SG.badge("dashed", "untested"),
        h("span.mono.small", p.steps.map((s) => s.label ? `${s.node} [${s.label}]` : s.node).join(" → "))))),
      R.findings?.length ? [h("h3.sub", "Findings"), h("div.card", h("ul.plain-list", R.findings.map((f) => h("li", SG.badge(f.severity === "error" ? "bad" : f.severity === "warn" ? "warn" : "neutral", f.severity), " ", f.message))))] : null);
    paint();
    if (step) setTimeout(() => document.getElementById(`step-${step}`)?.scrollIntoView({ block: "center" }), 0);
  }

  function lintTab(S, sk, body) {
    SG.put(body, h("p.lede", "Static checks on the skill text and its claims. Errors stop ", SG.code("pipeline --strict"), "."),
      SG.table({
        minWidth: 600, empty: "No lint findings.",
        cols: [
          { key: "sev", label: "Severity", render: (f) => SG.badge(f.severity === "error" ? "bad" : f.severity === "warn" ? "warn" : "neutral", f.severity) },
          { key: "rule", label: "Rule", render: (f) => h("span.mono.small", f.rule) },
          { key: "lines", label: "Lines", render: (f) => (f.span ? h("a.mono", { href: href(["s", S.id, "skill", sk.skill], { tab: "lines", line: f.span[0] }) }, lines(f.span)) : "–") },
          { key: "msg", label: "Message", render: (f) => f.message },
        ],
        rows: sk.lint,
      }));
  }

  // ------------------------------------------------------------ claim detail
  function claimDetail(S, sk, c, inDrawer) {
    const set = S.id, name = sk.skill;
    const v = SG.VERDICT[c.quadrant] || {};
    const ablates = Object.keys(c.rates).filter((k) => k.startsWith("ablate-"));
    const span = [Math.max(1, c.span[0] - 1), Math.min(sk.lines.length, c.span[1] + 1)];
    const excerpt = sk.lines.slice(span[0] - 1, span[1]).map((t, k) => {
      const ln = span[0] + k;
      return h("div.line", { class: ln >= c.span[0] && ln <= c.span[1] ? `has-claim q-${c.quadrant}` : "context" }, h("span.ln", ln), h("span.tx", t || " "));
    });
    const chk = c.check || {};
    const rec = SG.recommend(S).find((r) => r.skill === name && r.claim === c.id);
    const results = c.results || sk.runs.filter((r) => r.statuses[c.id]).map((r) => ({ path: r.path, task: r.task, variant: r.variant, rep: r.rep, status: r.statuses[c.id] }));
    const order = (r) => [r.variant === "skill" ? 0 : r.variant === "baseline" ? 1 : 2, SG.STATUS_ORDER.indexOf(r.status), r.task, r.rep];
    results.sort((a, b) => { const x = order(a), y = order(b); for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1; return 0; });
    return h("div.claim-detail",
      h("div.row-tags", SG.verdictBadge(c.quadrant), SG.badge("outline", c.kind),
        h("a.badge.outline.mono", { href: href(["s", set, "skill", name], { tab: "lines", line: c.span[0] }) }, lines(c.span)),
        c.weak ? SG.badge("warn outline", "weak", "Fewer than 3 runs per variant") : null,
        inDrawer ? h("a.push.small", { href: href(["s", set, "claim", name, c.id]) }, "Open as page ↗") : null),
      h("p.claim-text", c.text),
      h("p.small", h("strong", v.tip || ""), " ", v.todo || ""),
      h("div.rates",
        h("div", h("span.faint", "With the skill"), SG.score(c.rates.skill, "with", href(["s", set, "runs"], { skill: name, claim: c.id, variant: "skill" }))),
        c.rates.baseline ? h("div", h("span.faint", "Without"), SG.score(c.rates.baseline, "without", href(["s", set, "runs"], { skill: name, claim: c.id, variant: "baseline" }))) : null,
        ablates.map((k) => h("div", h("span.faint", k), SG.score(c.rates[k], "other", href(["s", set, "runs"], { skill: name, claim: c.id, variant: k }))))),
      c.failure_mode ? h("p.small", "Most common failure with the skill: ", SG.statusBadge(c.failure_mode)) : null,
      h("h4", "In SKILL.md"), h("div.lines.card.excerpt", excerpt),
      h("h4", "How it is checked"),
      h("dl.kv",
        h("dt", "check"), h("dd", chk.type === "judge" ? ["judge: ", h("q", chk.question)] : chk.type === "file" ? `final file ${chk.path_glob} ${(chk.content_any || []).join(" | ")}` : `tool ${(chk.tools || []).join(", ") || "any"} matching ${(chk.input_any || []).join(" | ") || "anything"}`),
        c.after?.length ? [h("dt", "after"), h("dd", c.after.join(", "))] : null,
        c.when ? [h("dt", "only when"), h("dd", `${c.when.cond} is ${c.when.value}`)] : null),
      h("h4", `Runs (${results.length})`),
      SG.table({
        minWidth: 520, empty: "No runs graded this claim.",
        rowHref: (r) => runHref(set, r.path, r.evidence != null ? { claim: c.id, event: r.evidence } : { claim: c.id, tab: "scores" }),
        cols: [
          { key: "s", label: "Status", render: (r) => SG.statusBadge(r.status) },
          { key: "run", label: "Run", render: (r) => h("a", { href: runHref(set, r.path, r.evidence != null ? { claim: c.id, event: r.evidence } : { claim: c.id, tab: "scores" }) }, `${r.task} · ${r.variant} r${r.rep}`) },
          { key: "why", label: "Reason", render: (r) => h("span.small", r.reason ? clip(r.reason, 220) : r.method && r.method !== "judge" ? `${r.method} check` : "") },
          { key: "ev", label: "Event", align: "right", render: (r) => (r.evidence != null ? `${r.evidence}${r.view ? ` · ${r.view}` : ""}` : "–") },
        ],
        rows: results,
      }),
      rec ? [h("h4", "Suggested change"), h("p.small", rec.do)] : null);
  }

  P.claimPage = async (ctx) => {
    const S = await SG.setData(ctx.set);
    if (!ctx.isCurrent()) return;
    const set = ctx.set, sk = skillOf(S, ctx.skill);
    const c = sk && findClaim(sk, ctx.claim);
    SG.chrome({ set, page: "skill", skillName: ctx.skill, S, title: `${ctx.claim} · ${ctx.skill}`,
      crumbs: [[setName(set), href(["s", set])], [ctx.skill, href(["s", set, "skill", ctx.skill])], [ctx.claim]] });
    if (!c) { SG.put(SG.view, SG.notice("warn", `No claim ${ctx.claim} in ${ctx.skill}.`)); return; }
    SG.put(SG.view, pageHead(h("span", h("span.mono", c.id), " · ", ctx.skill), null), h("div.card.card-pad", claimDetail(S, sk, c, false)));
  };

  // ------------------------------------------------------------ runs
  P.runs = async (ctx) => {
    const S = await SG.setData(ctx.set);
    if (!ctx.isCurrent()) return;
    const set = ctx.set, params = ctx.params;
    SG.chrome({ set, page: "runs", S, title: "Runs", crumbs: [[setName(set), href(["s", set])], ["Runs"]] });
    const keys = ["skill", "task", "variant", "outcome", "status", "judge", "claim", "q", "problems", "sort", "dir", "group"];
    const st = Object.fromEntries(keys.map((k) => [k, params.get(k) || ""]));
    const runs = S.runs;
    const bySkillRun = new Map();
    S.data.skills.forEach((sk) => sk.runs.forEach((r) => bySkillRun.set(r.path, r)));
    const claimStatus = (row) => {
      if (!st.claim) return null;
      const agg = bySkillRun.get(row.path);
      if (!agg || row.skill !== (st.skill || row.skill)) return null;
      return st.claim.startsWith("ref:") ? agg.ref_statuses?.[st.claim.slice(4)] : agg.statuses?.[st.claim];
    };
    const problem = (r) => r.all_success === false || r.error || (r.failed || []).length > 0;
    const claimSkill = st.claim && (st.skill ? skillOf(S, st.skill) : S.data.skills.find((sk) => sk.claims.some((c) => c.id === st.claim)));
    const claimInfo = claimSkill ? (st.claim.startsWith("ref:") ? claimSkill.reference?.nodes.find((n) => n.id === st.claim.slice(4)) : findClaim(claimSkill, st.claim)) : null;
    const out = h("div");
    const chips = h("div.active-filters");
    const count = h("span.count-text");
    const multiSkill = new Set(runs.map((r) => r.skill)).size > 1;

    const cols = () => [
      { key: "task", label: "Task", sort: (r) => r.task, render: (r) => h("div",
        h("a.strong", { href: runHref(set, r.path, st.claim ? { claim: st.claim } : null) }, r.task),
        r.task_intent ? h("div.faint.small.one-line", { title: r.task_intent }, r.task_intent) : r.prompt ? h("div.faint.small.one-line", { title: r.prompt }, r.prompt) : null,
        r.episode && r.episode.of > 1 ? h("div.faint.small", `episode ${r.episode.index} of ${r.episode.of}`) : null) },
      multiSkill && !st.skill ? { key: "skill", label: "Skill", sort: (r) => r.skill, render: (r) => r.skill } : null,
      { key: "variant", label: "Variant", sort: (r) => (r.variant === "skill" ? "0" : r.variant === "baseline" ? "1" : "2" + r.variant) + r.rep, render: (r) => h("div",
        h("span", r.variant), h("span.faint", ` r${r.rep}`), r.skill_loaded === false && r.variant !== "baseline" ? h("div", SG.badge("warn", "skill not loaded")) : null) },
      st.claim ? { key: "claimcol", label: h("span", "Claim ", h("span.mono", st.claim)), sort: (r) => SG.STATUS_ORDER.indexOf(claimStatus(r)), render: (r) => {
        const s = claimStatus(r); return s ? SG.statusBadge(s) : h("span.faint", "–");
      } } : null,
      { key: "claims", label: "Claim results", sort: (r) => -(r.failed || []).length, render: (r) => (r.checked ? h("div.cell-bar", SG.bar(SG.statusParts(r.statuses)),
        h("span.small", (r.failed || []).length ? h("span.text-bad", `${(r.failed || []).length} failed: ${(r.failed || []).slice(0, 4).join(", ")}${(r.failed || []).length > 4 ? "…" : ""}`) : h("span.faint", `${r.statuses.satisfied || 0} satisfied`)))
        : h("span.faint", "not graded")) },
      { key: "outcome", label: "Outcome", sort: (r) => (r.all_success == null ? 2 : r.all_success ? 1 : 0), render: (r) => h("div",
        r.all_success == null ? h("span.faint", "–") : r.all_success ? SG.badge("good", "✓ passed") : SG.badge("bad", "✗ failed"),
        r.error ? h("div", SG.badge("bad", "error", r.error)) : null) },
      { key: "size", label: "Tools · tokens", align: "right", sort: (r) => r.tokens, render: (r) => h("div", String(r.tool_calls), h("div.faint.small", `~${tok(r.tokens)}`)) },
      { key: "judge", label: "Judge", sort: (r) => r.judge_mode || "", render: (r) => h("div", r.judge_mode ? SG.badge(r.judge_mode === "windowed" ? "info" : "outline", r.judge_mode) : h("span.faint", "–"),
        r.tokens_sent ? h("div.faint.small", `sent ~${tok(r.tokens_sent)}`) : null) },
      { key: "cost", label: "Cost", align: "right", sort: (r) => (r.cost_usd || 0) + (r.judge_cost_usd || 0), render: (r) => h("div", usd(r.cost_usd), h("div.faint.small", `+${usd(r.judge_cost_usd)} judge`)) },
    ].filter(Boolean);

    function filtered() {
      const q = st.q.toLowerCase();
      return runs.filter((r) => (!st.skill || r.skill === st.skill) && (!st.task || r.task === st.task) && (!st.variant || r.variant === st.variant)
        && (!st.outcome || (st.outcome === "passed" ? r.all_success === true : st.outcome === "failed" ? r.all_success === false : r.all_success == null))
        && (!st.status || (r.statuses || {})[st.status]) && (!st.judge || r.judge_mode === st.judge)
        && (!st.claim || claimStatus(r) != null)
        && (!q || `${r.task} ${r.path} ${r.task_intent || ""} ${r.prompt || ""}`.toLowerCase().includes(q))
        && (!st.problems || problem(r)));
    }
    function setSt(patch) { Object.assign(st, patch); SG.setParams(patch); paint(); }
    function paint() {
      const rows = filtered();
      count.textContent = `${rows.length} of ${runs.length}`;
      const labels = { skill: "skill", task: "task", variant: "variant", outcome: "outcome", status: "has", judge: "judge", claim: "claim", q: "search", problems: "only problems" };
      SG.put(chips, Object.keys(labels).filter((k) => st[k]).map((k) => h("button.chip", { type: "button", title: "Remove filter", onclick: () => { setSt({ [k]: "" }); syncControls(); } },
        `${labels[k]}${k === "problems" ? "" : `: ${st[k]}`}`, " ×")),
        Object.keys(labels).some((k) => st[k]) ? h("button.link", { type: "button", onclick: () => { setSt(Object.fromEntries(Object.keys(labels).map((k) => [k, ""]))); syncControls(); } }, "Clear filters") : null);
      const tableFor = (rs) => SG.table({ cols: cols(), rows: rs, sort: st.sort, dir: st.dir, minWidth: 900,
        onSort: (k, d) => setSt({ sort: k, dir: d }), rowHref: (r) => runHref(set, r.path, st.claim ? { claim: st.claim } : null),
        empty: h("span", "No runs match these filters. ", h("button.link", { type: "button", onclick: () => { setSt(Object.fromEntries(keys.filter((k) => !["sort", "dir", "group"].includes(k)).map((k) => [k, ""]))); syncControls(); } }, "Clear filters")) });
      if (st.group === "task" && rows.length) {
        const groups = new Map();
        rows.forEach((r) => { const k = `${r.skill} / ${r.task}`; groups.set(k, [...(groups.get(k) || []), r]); });
        SG.put(out, [...groups.entries()].map(([k, rs]) => {
          const pass = rs.filter((r) => r.all_success != null);
          const worst = SG.STATUS_ORDER.find((s) => rs.some((r) => (r.statuses || {})[s])) || null;
          return h("details.group", { open: true }, h("summary", h("strong", k), h("span.faint", ` · ${plural(rs.length, "run")} · passed ${pass.length ? pct(ratePct(pass)) : "–"}`), worst ? [" ", SG.statusBadge(worst)] : null), tableFor(rs));
        }));
      } else SG.put(out, tableFor(rows));
    }
    const values = (k) => [...new Set(runs.filter((r) => !st.skill || k === "skill" || r.skill === st.skill).map((r) => r[k]))].sort();
    const controls = {};
    const sel = (label, key, options) => (controls[key] = h("select.control", { "aria-label": label, onchange: (e) => setSt({ [key]: e.target.value }) },
      h("option", { value: "" }, `${label}: all`), options.map(([v, l]) => h("option", { value: v, selected: st[key] === v }, `${label}: ${l}`))));
    const syncControls = () => Object.entries(controls).forEach(([k, el]) => {
      if (el.type === "checkbox") el.checked = !!st[k]; else el.value = st[k];
    });
    const claimOptions = (st.skill ? [skillOf(S, st.skill)] : S.data.skills).filter(Boolean).flatMap((sk) => sk.claims.map((c) => [c.id, `${c.id}${st.skill ? "" : " (" + sk.skill + ")"}`]));
    SG.put(SG.view,
      pageHead("Runs", h("span", count, " runs · click a run to open its trace")),
      claimInfo ? SG.notice("info", h("strong", h("span.mono", st.claim), " "), claimInfo.text, ". The claim column shows its result in each run.") : null,
      h("div.toolbar",
        multiSkill ? sel("Skill", "skill", values("skill").map((v) => [v, v])) : null,
        sel("Task", "task", values("task").map((v) => [v, v])),
        sel("Variant", "variant", values("variant").map((v) => [v, v])),
        sel("Outcome", "outcome", [["passed", "passed"], ["failed", "failed"], ["unknown", "unknown"]]),
        sel("Has", "status", ["violated", "missed", "out_of_order", "unclear"].map((s) => [s, SG.STATUS[s].label])),
        runs.some((r) => r.judge_mode === "windowed") ? sel("Judge", "judge", [["full", "full"], ["windowed", "windowed"]]) : null,
        sel("Claim", "claim", claimOptions),
        h("input.control.grow", { type: "search", placeholder: "Search task, path, prompt", "aria-label": "Search runs", value: st.q,
          oninput: (e) => { st.q = e.target.value; SG.setParams({ q: st.q }); paint(); } }),
        h("label.toggle", (controls.problems = h("input", { type: "checkbox", checked: !!st.problems, onchange: (e) => setSt({ problems: e.target.checked ? "1" : "" }) })), " Only problems"),
        h("label.field", h("span", "Group"), (controls.group = h("select.control", { onchange: (e) => setSt({ group: e.target.value }) },
          h("option", { value: "" }, "none"), h("option", { value: "task", selected: st.group === "task" }, "by task"))))),
      chips, out);
    paint();
  };

  // ------------------------------------------------------------ compare
  P.compare = async (ctx) => {
    const sets = SG.SETS;
    const a = ctx.a || sets[0]?.id, b = ctx.b || sets.find((s) => s.id !== a)?.id;
    const SA = sets.length ? await SG.setData(a).catch(() => null) : null;
    if (!ctx.isCurrent()) return;
    SG.chrome({ set: a, page: "compare", S: SA, title: "Compare", hidePicker: true, crumbs: [["Compare"]] });
    if (sets.length < 2) {
      SG.put(SG.view, pageHead("Compare", null), SG.empty(h("p", "Compare two run sets, for example before and after editing a skill:"), h("p", SG.code("skillgraph dashboard runs/before runs/after"))));
      return;
    }
    const picker = (val, after) => h("select.control", { "aria-label": after ? "After" : "Before", onchange: (e) => {
      location.hash = after ? href(["compare", a, e.target.value]) : href(["compare", e.target.value, b]);
    } }, sets.map((s) => h("option", { value: s.id, selected: s.id === val }, SG.setLabel(s))));
    const head = [pageHead("Compare", null), h("div.toolbar", h("span.faint", "Before"), picker(a, false), h("span.faint", "→ after"), picker(b, true),
      h("a.btn", { href: href(["compare", b, a]) }, "Swap"))];
    if (!b || a === b) { SG.put(SG.view, ...head, SG.notice("", "Pick two different run sets.")); return; }
    SG.put(SG.view, ...head, SG.skeleton(5));
    const D = await SG.api(`/api/diff/${SG.enc(a)}/${SG.enc(b)}`);
    if (!ctx.isCurrent()) return;
    const tot = { better: 0, worse: 0 };
    D.skills.forEach((s) => { tot.better += s.counts.better; tot.worse += s.counts.worse; });
    const verdict = tot.better > tot.worse ? ["good", "Improved"] : tot.worse > tot.better ? ["bad", "Regressed"] : tot.better ? ["warn", "Mixed"] : ["neutral", "No change in verdicts"];
    const only = ctx.params.get("skill") || "";
    const ORDER = ["worse", "better", "changed", "new", "removed", "same"];
    SG.put(SG.view, ...head,
      h("div.card.card-pad.compare-verdict", SG.badge(verdict[0], verdict[1]), " ",
        D.skills.map((s) => `${s.skill}: ${s.counts.better} better, ${s.counts.worse} worse`).join(" · ") || "No skills in common."),
      h("p.small.faint", "Claims are matched by id and text; editing a skill renumbers claims, so ids can differ."),
      D.only_before.length || D.only_after.length ? SG.notice("", `Only before: ${D.only_before.join(", ") || "none"} · only after: ${D.only_after.join(", ") || "none"}`) : null,
      D.skills.filter((s) => !only || s.skill === only).map((s) => {
        const sameRows = s.claims.filter((c) => c.change === "same");
        const rows = s.claims.filter((c) => c.change !== "same").sort((x, y) => ORDER.indexOf(x.change) - ORDER.indexOf(y.change));
        const table = (rs) => SG.table({ minWidth: 720, rows: rs,
          rowHref: (c) => (c.after ? href(["s", b, "claim", s.skill, c.after]) : href(["s", a, "claim", s.skill, c.before])),
          cols: [
            { key: "ch", label: "Change", render: (c) => SG.badge(SG.CHANGE[c.change], c.change) },
            { key: "claim", label: "Claim", render: (c) => h("div", h("span.mono.small", [...new Set([c.before, c.after].filter(Boolean))].join(" → ")), " ", c.text) },
            { key: "v", label: "Verdict", render: (c) => h("div.row-tags", c.verdict[0] ? SG.verdictBadge(c.verdict[0]) : h("span.faint", "–"), h("span.faint", "→"), c.verdict[1] ? SG.verdictBadge(c.verdict[1]) : h("span.faint", "–")) },
            { key: "r", label: "With the skill", align: "right", render: (c) => `${pct(c.skill_rate[0])} → ${pct(c.skill_rate[1])}` },
          ] });
        const sameBox = h("div");
        return h("section.compare-skill",
          h("div.section-head", h("h2", s.skill), h("span.hint", `success with skill ${pct(s.success.skill[0])} → ${pct(s.success.skill[1])} · cost ${usd(s.cost_usd[0])} → ${usd(s.cost_usd[1])} · runs ${s.runs[0]} → ${s.runs[1]} · SKILL.md ${s.lines[0]} → ${s.lines[1]} lines`)),
          h("div.row-tags", Object.entries(s.counts).filter(([, n]) => n).map(([k, n]) => SG.badge(SG.CHANGE[k], `${n} ${k}`))),
          rows.length ? table(rows) : h("p.faint.small", "No claim changed verdict or rate."),
          sameRows.length ? h("button.link", { type: "button", onclick: (e) => { SG.put(sameBox, table(sameRows)); e.target.remove(); } }, `Show ${sameRows.length} unchanged`) : null,
          sameBox);
      }));
  };

  // ------------------------------------------------------------ glossary
  P.glossary = async () => {
    const set = SG.SETS[0]?.id;
    SG.chrome({ set, page: "glossary", S: set ? await SG.setData(set).catch(() => null) : null, title: "Glossary", crumbs: [["Glossary"]], hidePicker: true });
    const table = (rows) => h("div.card", h("dl.glossary", rows.map(([term, text, extra]) => [h("dt", term), h("dd", text, extra ? h("div.faint.small", extra) : null)])));
    SG.put(SG.view,
      pageHead("Glossary", "What the words in this dashboard mean"),
      h("h2.sub", "Verdicts (per claim, across runs)"),
      table(SG.VERDICT_ORDER.map((k) => [SG.verdictBadge(k), SG.VERDICT[k].tip, `What to do: ${SG.VERDICT[k].todo}`])),
      h("h2.sub", "Results (per claim, in one run)"),
      table(SG.STATUS_ORDER.map((k) => [SG.statusBadge(k), SG.STATUS[k].tip])),
      h("h2.sub", "Reference diagnoses (per step of the process)"),
      table(Object.keys(SG.DIAG).map((k) => [SG.diagBadge(k), SG.DIAG[k].tip])),
      h("h2.sub", "Ablation results"),
      table(Object.keys(SG.ABLATION).map((k) => [SG.badge(SG.ABLATION[k].cls, k), SG.ABLATION[k].tip])),
      h("h2.sub", "Other terms"),
      table(SG.TERMS.map(([t, d]) => [h("strong", t), d])));
  };
})();
