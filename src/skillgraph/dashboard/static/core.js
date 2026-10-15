/* skillgraph dashboard: shared helpers, copy, components and the recommendations engine.
   Everything hangs off window.SG. DOM is built with h(), never from HTML strings, so text
   from traces and skills can't inject markup. See docs/dashboard-prd.md. */
"use strict";

(() => {
  const SG = (window.SG = window.SG || {});
  const STATIC = window.SKILLGRAPH_DATA || null;
  const memo = new Map();

  // ------------------------------------------------------------ data
  SG.isStatic = !!STATIC;
  SG.api = async (endpoint, path) => {
    const key = path === undefined ? endpoint : `${endpoint}?path=${path}`;
    if (STATIC) {
      if (!(key in STATIC)) throw new Error("This export doesn't include " + key);
      return STATIC[key];
    }
    if (memo.has(key)) return memo.get(key);
    const url = path === undefined ? endpoint : `${endpoint}?path=${encodeURIComponent(path)}`;
    const p = fetch(url, { headers: { Accept: "application/json" } }).then(async (res) => {
      const data = await res.json().catch(() => ({ error: res.statusText }));
      if (!res.ok) throw new Error(data.error || res.statusText);
      return data;
    });
    memo.set(key, p);
    p.catch(() => memo.delete(key));
    return p;
  };
  SG.setData = (id) => SG.api(`/api/set/${encodeURIComponent(id)}`);
  SG.runData = (id, path) => SG.api(`/api/run/${encodeURIComponent(id)}`, path);

  // ------------------------------------------------------------ DOM
  function h(tag, attrs, ...kids) {
    if (attrs == null || typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs)) {
      kids.unshift(attrs);
      attrs = {};
    }
    const [name, ...classes] = tag.split(".");
    const el = document.createElement(name || "div");
    if (classes.length) el.className = classes.join(" ");
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = (el.className ? el.className + " " : "") + v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "style") for (const [p, val] of Object.entries(v)) el.style.setProperty(p, val);
      else el.setAttribute(k, v === true ? "" : String(v));
    }
    append(el, kids);
    return el;
  }
  function append(el, kids) {
    for (const kid of [kids].flat(Infinity)) {
      if (kid == null || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  SG.h = h;
  SG.put = (el, ...kids) => { el.replaceChildren(); append(el, kids); return el; };

  const ICONS = {
    overview: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
    recs: "M9 18h6 M10 21h4 M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z",
    skills: "M6 2h9l5 5v15H6z M14 2v6h6 M9 13h8 M9 17h8",
    runs: "M8 6h13 M8 12h13 M8 18h13 M3.5 6h.01 M3.5 12h.01 M3.5 18h.01",
    compare: "M7 7h13 M16 3l4 4-4 4 M17 17H4 M8 13l-4 4 4 4",
    glossary: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3 M12 17h.01",
    report: "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6 M15 3h6v6 M10 14 21 3",
    menu: "M3 6h18 M3 12h18 M3 18h18",
    right: "M9 18l6-6-6-6",
    down: "M6 9l6 6 6-6",
    close: "M18 6 6 18 M6 6l12 12",
    search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z M21 21l-4.3-4.3",
    theme: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M12 3v18",
    back: "M15 18l-6-6 6-6",
    logo: "M5 6 12 18 19 6 M5 6h14",
  };
  SG.icon = (name, size = 16) => {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", size);
    svg.setAttribute("height", size);
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", `icon icon-${name}`);
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", ICONS[name] || "");
    svg.append(path);
    if (name === "logo") {
      for (const [cx, cy] of [[5, 6], [19, 6], [12, 18]]) {
        const c = document.createElementNS(ns, "circle");
        c.setAttribute("cx", cx); c.setAttribute("cy", cy); c.setAttribute("r", "2.5");
        svg.append(c);
      }
    }
    return svg;
  };

  // ------------------------------------------------------------ formatting
  SG.pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);
  SG.usd = (x) => (x == null ? "–" : x === 0 ? "$0" : `$${x < 0.1 ? x.toFixed(3) : x.toFixed(2)}`);
  SG.tok = (n) => (n == null ? "–" : n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n));
  SG.clip = (s, n) => { s = String(s ?? ""); return s.length <= n ? s : s.slice(0, n - 1) + "…"; };
  SG.sum = (xs) => xs.reduce((a, b) => a + (b || 0), 0);
  SG.lines = (span) => (!span ? "" : span[0] === span[1] ? `L${span[0]}` : `L${span[0]}–${span[1]}`);
  SG.plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
  SG.enc = encodeURIComponent;

  // ------------------------------------------------------------ URL
  // Routes are hashes: #/<part>/<part>?query. Filters live in the query so every view
  // can be bookmarked, shared, and opened from a static export.
  SG.parseHash = () => {
    const raw = location.hash.replace(/^#\/?/, "");
    const q = raw.indexOf("?");
    const pathPart = q < 0 ? raw : raw.slice(0, q);
    return {
      parts: pathPart.split("/").filter(Boolean).map(decodeURIComponent),
      params: new URLSearchParams(q < 0 ? "" : raw.slice(q + 1)),
    };
  };
  SG.href = (parts, params) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params || {})) if (v != null && v !== "" && v !== false) qs.set(k, v === true ? "1" : String(v));
    const q = qs.toString();
    return "#/" + parts.map((p) => String(p).split("/").map(SG.enc).join("/")).join("/") + (q ? "?" + q : "");
  };
  SG.setHome = (set) => ["s", set];
  /** Change query parameters without re-rendering the page (filters, selection). */
  SG.setParams = (patch) => {
    const { parts, params } = SG.parseHash();
    for (const [k, v] of Object.entries(patch)) {
      if (v == null || v === "" || v === false) params.delete(k);
      else params.set(k, v === true ? "1" : String(v));
    }
    const q = params.toString();
    const hash = "#/" + parts.map(SG.enc).join("/") + (q ? "?" + q : "");
    history.replaceState(null, "", hash);
    return params;
  };
  SG.paramsObj = (params) => Object.fromEntries(params.entries());

  // ------------------------------------------------------------ copy (docs/dashboard-prd.md 4.10)
  SG.VERDICT = {
    working: { cls: "good", label: "Working", tip: "The agent does this with the skill and not without it. This line is doing its job.", todo: "Keep it." },
    followed: { cls: "good soft", label: "Followed", tip: "The agent does this with the skill. There are no runs without the skill to compare against.", todo: "Add baseline runs to learn whether the line matters." },
    inert: { cls: "neutral", label: "Inert", tip: "The agent does this with or without the skill. The line costs context and changes nothing.", todo: "Cut it, unless it guards against damage." },
    ignored: { cls: "bad", label: "Ignored", tip: "The agent doesn't do this even with the skill.", todo: "Make it concrete; give it a done-criterion (a file, a command, an exact string)." },
    interfering: { cls: "warn", label: "Interfering", tip: "The agent does this without the skill but stops when the skill is loaded. Something in the skill pushes the other way.", todo: "Find the line that conflicts and fix it." },
    untested: { cls: "dashed", label: "Untested", tip: "No run reached this branch of the skill.", todo: "Add a task that takes this branch (skillgraph tasks)." },
  };
  SG.VERDICT_ORDER = ["ignored", "interfering", "working", "followed", "inert", "untested"];
  SG.STATUS = {
    satisfied: { cls: "good", glyph: "✓", label: "satisfied", tip: "The run did this." },
    missed: { cls: "bad", glyph: "✗", label: "missed", tip: "The run didn't do this." },
    violated: { cls: "bad", glyph: "⊘", label: "violated", tip: "The run did what this rule forbids." },
    out_of_order: { cls: "warn", glyph: "⇄", label: "out of order", tip: "The run did this, but before a step it must follow." },
    unclear: { cls: "info", glyph: "?", label: "unclear", tip: "The judge couldn't tell from the trace." },
    "n/a": { cls: "dashed", glyph: "·", label: "n/a", tip: "This branch didn't apply in this run." },
  };
  SG.STATUS_ORDER = ["violated", "missed", "out_of_order", "unclear", "satisfied", "n/a"];
  SG.DIAG = {
    gap: { cls: "bad", tip: "Not in the skill, and the agent doesn't do it. Add it to the skill." },
    implicit: { cls: "info", tip: "Not in the skill, but the agent does it anyway." },
    ignored: { cls: "bad", tip: "The skill asks for it, but the agent doesn't do it. Harden the matching lines." },
    partial: { cls: "warn", tip: "Only partly in the skill, and the agent doesn't do it. Make the skill explicit." },
    conflict: { cls: "bad", tip: "The skill contradicts the reference here. Fix the skill text." },
    ok: { cls: "good", tip: "In the skill, and the agent does it." },
    untested: { cls: "dashed", tip: "No run reached this step. Generate a task for its path." },
    unreached: { cls: "dashed", tip: "Runs on this path didn't end in this state. Check the outcome the skill asks for." },
  };
  SG.ABLATION = {
    "load-bearing": { cls: "good", tip: "Removing these lines made the agent stop doing what they ask." },
    "removal-helped": { cls: "warn", tip: "Task success went up without these lines." },
    "no-effect": { cls: "neutral", tip: "Behavior didn't change without these lines." },
  };
  SG.CHANGE = { better: "good", worse: "bad", changed: "info", same: "neutral", new: "accent", removed: "outline" };
  SG.TERMS = [
    ["claim", "One checkable instruction extracted from SKILL.md, anchored to its lines."],
    ["verdict", "What a claim does across runs: working, inert, ignored, interfering, followed or untested."],
    ["condition", "A branch in the skill. It decides which claims apply; claims on the other branch are n/a."],
    ["baseline", "Runs of the same tasks without the skill, to see what the agent does anyway."],
    ["ablation", "Runs with some lines deleted, to see whether those lines matter."],
    ["weak", "The verdict rests on fewer than 3 runs per variant. Treat it as a hint; rerun with --runs 3."],
    ["judge", "A model answers a yes/no question about the trace. Mechanical checks (tool and file) need no model."],
    ["mechanical check", "Graded by matching tool calls or final files, without a model."],
    ["windowed", "A long trace. The judge read the turns that hold the evidence, plus a shortened skeleton of the whole run."],
    ["window / skeleton", "Which view decided a windowed verdict: an excerpt around the evidence, or every event shortened."],
    ["off-skill turn", "No judge question pointed at this turn; it is likely unrelated to the skill."],
    ["phase", "A name for a stretch of turns, given by a model that never sees the skill."],
    ["episode", "One load of the skill inside a longer session; each becomes its own run."],
    ["lint", "Static problems in the skill text or its claims, found without running anything."],
    ["reference", "The process the skill is meant to encode (an SOP, runbook or flowchart), compiled into steps."],
    ["coverage", "The share of the reference process the skill asks for."],
    ["stale", "SKILL.md or the claims changed after these results were made; rerun extract and check."],
  ];

  // ------------------------------------------------------------ small components
  SG.badge = (cls, label, title) => h("span.badge", { class: cls || null, title }, label);
  SG.verdictBadge = (q, href) => {
    const v = SG.VERDICT[q] || { cls: "", label: q, tip: "" };
    const b = h(href ? "a.badge" : "span.badge", { class: v.cls, title: v.tip, href }, h("span.dot"), v.label);
    return b;
  };
  SG.statusBadge = (s, href) => {
    const v = SG.STATUS[s] || { cls: "", glyph: "", label: s || "–", tip: "" };
    return h(href ? "a.badge" : "span.badge", { class: v.cls, title: v.tip, href }, `${v.glyph} ${v.label}`);
  };
  SG.diagBadge = (d) => SG.badge(SG.DIAG[d]?.cls, d, SG.DIAG[d]?.tip);
  SG.conditionBadge = (value) => SG.badge(value === true ? "info" : value === false ? "outline" : "warn",
    value === true ? "held" : value === false ? "didn't hold" : "unknown");

  /** "4/4" with a mini bar; kind: with | without | other. */
  SG.score = (rate, kind, href, title) => {
    if (!rate || !rate.n) return h("span.score.none", { title: "No applicable runs" }, "–");
    const k = Math.round((rate.followed ?? 0) * rate.n);
    const el = h(href ? "a.score" : "span.score", { class: kind, href, title },
      h("span.score-bar", h("span", { style: { width: `${Math.round(100 * (rate.followed ?? 0))}%` } })),
      h("span.num", `${k}/${rate.n}`));
    return el;
  };

  SG.bar = (parts, label) => {
    const total = SG.sum(parts.map((p) => p.n));
    return h("div.bar", { role: "img", "aria-label": label || parts.filter((p) => p.n).map((p) => `${p.n} ${p.label}`).join(", ") },
      total ? parts.filter((p) => p.n).map((p) => h("span", { class: p.cls, title: `${p.label}: ${p.n}`, style: { width: `${(100 * p.n) / total}%` } })) : null);
  };
  SG.verdictParts = (q) => SG.VERDICT_ORDER.map((k) => ({ n: (q || {})[k] || 0, cls: `seg-${(SG.VERDICT[k].cls || "neutral").split(" ").join("-")}`, label: SG.VERDICT[k].label }));
  SG.statusParts = (c) => SG.STATUS_ORDER.map((k) => ({ n: (c || {})[k] || 0, cls: `seg-${SG.STATUS[k].cls}`, label: SG.STATUS[k].label }));

  SG.help = (term) => {
    const entry = SG.TERMS.find(([t]) => t === term);
    const text = entry ? entry[1] : term;
    const pop = h("span.popover", { role: "tooltip", hidden: true }, h("span", text), " ", h("a", { href: "#/glossary" }, "Glossary →"));
    const btn = h("button.help", { type: "button", "aria-label": `What is ${term}?`, onclick: (e) => {
      e.stopPropagation();
      const open = pop.hidden;
      document.querySelectorAll(".popover").forEach((p) => { p.hidden = true; });
      pop.hidden = !open;
    } }, "?");
    return h("span.help-wrap", btn, pop);
  };
  document.addEventListener("click", () => document.querySelectorAll(".popover").forEach((p) => { p.hidden = true; }));

  SG.skeleton = (rows = 4) => h("div.skeleton", { "aria-busy": "true", "aria-label": "Loading" },
    Array.from({ length: rows }, (_, i) => h("span", { style: { width: `${90 - (i % 3) * 18}%` } })));
  SG.notice = (cls, ...kids) => h("div.notice", { class: cls || null, role: cls === "bad" ? "alert" : null }, kids);
  SG.empty = (...kids) => h("div.empty", kids);
  SG.code = (text) => h("code", text);

  /** In-page tabs; items [{key, label, count}], active key, hrefFor(key). */
  SG.tabs = (items, active, hrefFor) => h("nav.tabs", { "aria-label": "Sections" },
    items.filter(Boolean).map((t) => h("a.tab", { href: hrefFor(t.key), "aria-current": t.key === active ? "page" : null },
      t.label, t.count != null ? h("span.count", t.count) : null)));

  /** A segmented control for small screens; items [{key,label}]. */
  SG.segmented = (items, active, onSelect) => h("div.segmented", { role: "tablist" },
    items.map((t) => h("button", { type: "button", role: "tab", "aria-selected": String(t.key === active),
      onclick: () => onSelect(t.key) }, t.label)));

  /** Sortable data table. cols: [{key, label, sort?, render, align?, cls?}]; returns the wrapper. */
  SG.table = ({ cols, rows, sort, dir, onSort, rowHref, onRow, minWidth, empty, rowClass }) => {
    const sorted = rows.slice();
    const col = cols.find((c) => c.key === sort && c.sort);
    if (col) {
      sorted.sort((a, b) => {
        const x = col.sort(a), y = col.sort(b);
        const cmp = x == null ? 1 : y == null ? -1 : typeof x === "number" ? x - y : String(x).localeCompare(String(y));
        return dir === "desc" ? -cmp : cmp;
      });
    }
    const head = h("tr", cols.map((c) => h("th", {
      class: [c.align === "right" ? "right" : null, c.cls].filter(Boolean).join(" ") || null,
      "aria-sort": c.key === sort ? (dir === "desc" ? "descending" : "ascending") : (c.sort ? "none" : null),
    }, c.sort && onSort ? h("button.sort", { type: "button", onclick: () => onSort(c.key, c.key === sort && dir !== "desc" ? "desc" : "asc") },
      c.label, h("span.sort-mark", { "aria-hidden": "true" }, c.key === sort ? (dir === "desc" ? "↓" : "↑") : "")) : c.label)));
    const body = h("tbody", sorted.map((r) => {
      const href = rowHref ? rowHref(r) : null;
      return h("tr", {
        class: [href || onRow ? "clickable" : null, rowClass ? rowClass(r) : null].filter(Boolean).join(" ") || null,
        onclick: href || onRow ? (e) => { if (e.target.closest("a, button, summary, input, select")) return; if (onRow) onRow(r); else location.hash = href; } : null,
      }, cols.map((c) => h("td", { class: [c.align === "right" ? "right num" : null, c.cls].filter(Boolean).join(" ") || null }, c.render(r))));
    }));
    if (!sorted.length) body.append(h("tr", h("td", { colspan: cols.length }, SG.empty(empty || "Nothing here."))));
    return h("div.table-wrap", h("table", { style: minWidth ? { "min-width": `${minWidth}px` } : null }, h("thead", head), body));
  };

  /** Drawer on the right; returns {el, close}. onClose runs after it closes. */
  SG.drawer = (title, body, onClose) => {
    document.querySelector(".drawer-root")?.remove();
    const prevFocus = document.activeElement;
    const close = () => { root.remove(); document.removeEventListener("keydown", onKey); onClose?.(); prevFocus?.focus?.(); };
    const onKey = (e) => {
      if (e.key === "Escape") close();
      if (e.key === "Tab") {  // keep focus inside the drawer
        const f = [...panel.querySelectorAll("a[href], button, select, input, [tabindex='0']")].filter((x) => !x.hidden);
        if (!f.length) return;
        if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
      }
    };
    const closeBtn = h("button.icon-button", { type: "button", "aria-label": "Close", onclick: close }, SG.icon("close"));
    const panel = h("aside.drawer", { role: "dialog", "aria-modal": "true", "aria-label": typeof title === "string" ? title : "Details" },
      h("header.drawer-head", h("div.drawer-title", title), closeBtn), h("div.drawer-body", body));
    const root = h("div.drawer-root", h("div.scrim", { onclick: close }), panel);
    document.body.append(root);
    document.addEventListener("keydown", onKey);
    closeBtn.focus();
    return { el: panel, close };
  };

  // ------------------------------------------------------------ recommendations (PRD 4.2)
  const PRIORITY = { high: 0, medium: 1, low: 2 };
  SG.PRIORITY = PRIORITY;
  const rateText = (r) => (r && r.n ? `${Math.round((r.followed ?? 0) * r.n)} of ${r.n}` : "none");

  /** Turn verdicts, reference diagnoses, lint, ablations and untested branches into a ranked to-do list. */
  SG.recommend = (S) => {
    const items = [];
    const runset = S.data.runset || "<runs>";
    for (const sk of S.data.skills) {
      const name = sk.skill;
      const hasBaseline = sk.variants.includes("baseline");
      const claimLink = (c) => SG.href(["s", S.id, "claim", name, c.id]);
      const runsLink = (c, variant) => SG.href(["s", S.id, "runs"], { skill: name, claim: c.id, variant });
      const lineText = (span) => sk.lines.slice(span[0] - 1, span[1]).join("\n").trim();
      for (const c of sk.claims) {
        const s = c.rates.skill, b = c.rates.baseline;
        if (c.quadrant === "interfering") {
          items.push({ priority: "high", action: "Investigate", skill: name, lines: c.span, claim: c.id, order: (c.baseline_rate || 0) - (c.skill_rate || 0),
            title: c.text, quote: lineText(c.span),
            why: `Followed ${rateText(b)} runs without the skill, ${rateText(s)} with it: something in the skill pushes the other way.`,
            do: "Look for a line that contradicts or crowds out this one; compare a baseline run with a skill run.",
            links: [["Runs for " + c.id, runsLink(c)], [`Claim ${c.id}`, claimLink(c)]] });
        } else if (c.quadrant === "ignored") {
          const fm = { missed: "the agent never did it", violated: "the agent did what this rule forbids", out_of_order: `done, but before ${(c.after || []).join(", ") || "the step it must follow"}` }[c.failure_mode] || "not followed";
          const how = c.kind === "must_not" ? "Put the rule next to the step it guards, and say what to do instead."
            : c.check.type === "judge" ? "Give it a concrete done-criterion (a file, a command, an exact string) so the judge isn't needed."
              : "State it as an explicit step with the exact command or file.";
          const failing = (c.results || []).filter((r) => r.variant === "skill" && ["missed", "violated", "out_of_order"].includes(r.status)).length;
          items.push({ priority: c.weak ? "medium" : "high", action: "Rewrite", skill: name, lines: c.span, claim: c.id, order: 1 - (c.skill_rate || 0),
            title: c.text, quote: lineText(c.span),
            why: `Ignored: followed in ${rateText(s)} runs with the skill${hasBaseline ? `, ${rateText(b)} without` : ""}; ${fm}.${c.weak ? " (weak: few runs)" : ""}`,
            notes: (c.miss_notes || []).slice(0, 3), do: how,
            links: [[`${failing || "Failing"} failing run${failing === 1 ? "" : "s"}`, runsLink(c, "skill")], [`Claim ${c.id}`, claimLink(c)]] });
        }
      }
      const R = sk.reference;
      if (R) {
        for (const n of R.nodes) {
          const refLink = SG.href(["s", S.id, "skill", name], { tab: "reference", step: n.id });
          const rates = `Agents did it ${SG.pct(n.rate_with)} of the time with the skill${n.rate_without != null ? `, ${SG.pct(n.rate_without)} without` : ""}.`;
          if (["gap", "conflict", "partial"].includes(n.diagnosis)) {
            const verb = { gap: "Add", conflict: "Fix", partial: "Make explicit" }[n.diagnosis];
            const how = { gap: `Add a step to the skill: "${n.text}"`, conflict: "The skill contradicts the process here; change the skill text or the SOP.",
              partial: `Say the whole step; only part of it is in the skill (${(n.claims || []).join(", ") || "no claim"}).` }[n.diagnosis];
            items.push({ priority: n.diagnosis === "partial" ? "medium" : "high", action: verb, skill: name, ref: n.id, order: 0,
              title: `Reference step "${n.text}"`, why: `${SG.DIAG[n.diagnosis].tip} ${rates}${n.note ? " " + n.note : ""}`, do: how,
              links: [["Reference step", refLink], ["Runs", SG.href(["s", S.id, "runs"], { skill: name, claim: `ref:${n.id}` })]] });
          } else if (n.diagnosis === "ignored") {
            items.push({ priority: "high", action: "Rewrite", skill: name, ref: n.id, order: 0,
              title: `Reference step "${n.text}"`, why: `${SG.DIAG.ignored.tip} ${rates}`,
              do: `Harden the lines for ${(n.claims || []).join(", ") || "this step"}: name the exact command, file or output.`,
              links: [["Reference step", refLink]] });
          }
        }
      }
      for (const f of sk.lint.filter((x) => x.severity === "error")) {
        items.push({ priority: "high", action: "Fix", skill: name, lines: f.span, order: 0, title: f.message,
          why: `Lint error (${f.rule}).`, do: "Fix the skill text or the file it points to.",
          links: [["Lint", SG.href(["s", S.id, "skill", name], { tab: "lint" })]] });
      }
      const failedRuns = sk.runs.filter((r) => r.variant === "skill" && r.all_success === false);
      const skillRuns = sk.runs.filter((r) => r.variant === "skill" && r.all_success != null);
      if (failedRuns.length) {
        items.push({ priority: "medium", action: "Check task", skill: name, order: failedRuns.length,
          title: `${failedRuns.length} of ${skillRuns.length} runs with the skill failed their success checks`,
          why: [...new Set(failedRuns.map((r) => r.task))].join(", "), do: "Open a failed run and read what the agent did; the skill or the success check may be wrong.",
          links: [["Failed runs", SG.href(["s", S.id, "runs"], { skill: name, variant: "skill", outcome: "failed" })]] });
      }
      for (const a of sk.ablations.filter((x) => x.verdict === "removal-helped")) {
        items.push({ priority: "medium", action: "Consider cutting", skill: name, lines: a.remove[0], order: 0,
          title: `Lines ${SG.lines(a.remove[0])}`, quote: a.text,
          why: `Task success rose from ${SG.pct(a.success_skill)} to ${SG.pct(a.success_ablated)} without these lines.`,
          do: "Cut or rewrite these lines.", links: [["Ablation runs", SG.href(["s", S.id, "runs"], { skill: name, variant: a.variant })]] });
      }
      for (const c of sk.claims.filter((x) => x.quadrant === "untested")) {
        const cond = c.when ? sk.conditions.find((k) => k.id === c.when.cond) : null;
        items.push({ priority: "medium", action: "Add a task", skill: name, lines: c.span, claim: c.id, order: 0,
          title: c.text, quote: lineText(c.span),
          why: `No run took this branch${cond ? ` ("${cond.text}" was ${c.when.value ? "never true" : "never false"})` : ""}.`,
          do: R ? `skillgraph tasks <suite> --skill ${name} --untested-in ${runset}` : "Write a task whose input takes this branch.",
          links: [[`Claim ${c.id}`, claimLink(c)]] });
      }
      if (R) {
        const untested = R.paths.filter((p) => !p.tested);
        if (untested.length) {
          items.push({ priority: "medium", action: "Add a task", skill: name, order: 0,
            title: `${SG.plural(untested.length, "reference path")} never taken`,
            why: untested.slice(0, 3).map((p) => p.steps.map((s) => s.node).join(" → ")).join("; "),
            do: `skillgraph tasks <suite> --skill ${name} --untested-in ${runset}`,
            links: [["Reference", SG.href(["s", S.id, "skill", name], { tab: "reference" })]] });
        }
      }
      for (const f of sk.lint.filter((x) => x.severity === "warn")) {
        items.push({ priority: "low", action: "Fix", skill: name, lines: f.span, order: 0, title: f.message, why: `Lint warning (${f.rule}).`,
          do: "Tidy the skill text.", links: [["Lint", SG.href(["s", S.id, "skill", name], { tab: "lint" })]] });
      }
      if (hasBaseline) {
        const inert = sk.claims.filter((c) => c.quadrant === "inert" && c.kind !== "must_not").sort((a, b) => a.span[0] - b.span[0]);
        const groups = [];
        for (const c of inert) {
          const g = groups[groups.length - 1];
          if (g && c.span[0] <= g.end + 2) { g.claims.push(c); g.end = Math.max(g.end, c.span[1]); } else groups.push({ start: c.span[0], end: c.span[1], claims: [c] });
        }
        for (const g of groups) {
          const abl = sk.ablations.find((a) => a.remove[0][0] <= g.end && g.start <= a.remove[0][1]);
          items.push({ priority: "low", action: "Consider cutting", skill: name, lines: [g.start, g.end], order: g.claims.length,
            title: `${SG.plural(g.claims.length, "inert claim")} on ${SG.lines([g.start, g.end])}`,
            quote: lineText([g.start, g.end]),
            why: `The agent does ${g.claims.length === 1 ? "this" : "these"} without being told.${abl ? ` Ablation of ${SG.lines(abl.remove[0])}: ${abl.verdict}.` : ""}`,
            do: "Cut to save context; keep anything that guards against damage.",
            links: [["Claims", SG.href(["s", S.id, "skill", name], { tab: "claims", verdict: "inert" })]] });
        }
      }
      const weak = sk.claims.filter((c) => c.weak).length;
      if (weak) {
        items.push({ priority: "low", action: "Add runs", skill: name, order: weak, title: `${SG.plural(weak, "verdict")} rest on fewer than 3 runs per variant`,
          why: "Two runs can't tell a habit from chance.", do: "Rerun with --runs 3 before acting on weak verdicts.",
          links: [["Claims", SG.href(["s", S.id, "skill", name], { tab: "claims" })]] });
      }
    }
    items.sort((a, b) => PRIORITY[a.priority] - PRIORITY[b.priority] || (b.order || 0) - (a.order || 0) || ((a.lines || [0])[0] - (b.lines || [0])[0]));
    return items;
  };

  /** One sentence per skill: does it work? (PRD 4.1) */
  SG.health = (sk) => {
    const q = sk.totals.quadrants || {};
    const n = sk.claims.length;
    const W = (q.working || 0) + (q.followed || 0), I = q.inert || 0, G = q.ignored || 0, F = q.interfering || 0;
    const hasBaseline = sk.variants.includes("baseline");
    const succ = sk.totals.success || {};
    const lift = hasBaseline && succ.skill != null && succ.baseline != null ? ` Task success ${SG.pct(succ.baseline)} without → ${SG.pct(succ.skill)} with.` : "";
    if (!hasBaseline) {
      const broke = sk.runs.filter((r) => Object.values(r.statuses).some((s) => s === "violated")).length;
      return { tone: G + F ? "bad" : "good", text: `${W} of ${n} rules followed in ${sk.totals.runs} runs; ${SG.plural(broke, "run")} broke a rule.` };
    }
    if (G + F === 0 && n && W / n >= 0.5) return { tone: "good", text: `Does real work: ${W} of ${n} lines change behavior.${lift}` };
    if (n && I / n >= 0.6) return { tone: G ? "warn" : "neutral", text: `Mostly redundant: ${I} of ${n} lines describe what the agent does anyway.${G ? ` ${SG.plural(G, "line")} ignored.` : ""}` };
    if (G + F > 0) return { tone: "bad", text: `${SG.plural(G + F, "line")} need attention: ${G} ignored, ${F} interfering. ${W} of ${n} lines work.` };
    return { tone: "neutral", text: `${W} working, ${I} inert, ${q.untested || 0} untested of ${n} lines.` };
  };
})();
