/* skillgraph dashboard: app shell (sidebar, top bar, breadcrumbs) and the router. */
"use strict";

(() => {
  const SG = window.SG;
  const { h, icon, href, enc } = SG;
  const view = document.getElementById("view");
  const sidebar = document.getElementById("sidebar");
  const topbar = document.getElementById("topbar");
  SG.view = view;
  SG.SETS = [];

  // ------------------------------------------------------------ theme
  const THEMES = ["system", "light", "dark"];
  let theme = "system";
  try { theme = localStorage.getItem("sg-theme") || "system"; } catch (_) { /* storage blocked */ }
  const applyTheme = () => {
    if (theme === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
  };
  applyTheme();

  // ------------------------------------------------------------ sidebar
  const navOpen = (open) => document.body.classList.toggle("nav-open", open);
  document.getElementById("nav-scrim").addEventListener("click", () => navOpen(false));

  function renderSidebar(ctx) {
    const { set, page, skillName, S } = ctx;
    const item = (key, label, ico, target, extra) => h("a.nav-item", {
      href: target, "aria-current": page === key ? "page" : null, title: label,
      onclick: () => navOpen(false),
    }, icon(ico), h("span.nav-label", label), extra || null);
    const highs = S ? SG.recommend(S).filter((r) => r.priority === "high").length : null;
    const skills = S ? S.data.skills.map((s) => s.skill) : [];
    const other = SG.SETS.find((s) => s.id !== set);
    const themeBtn = h("button.nav-item", { type: "button", title: `Theme: ${theme}`, "aria-label": `Color theme: ${theme}. Click to change.`,
      onclick: () => {
        theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
        try { localStorage.setItem("sg-theme", theme); } catch (_) { /* storage blocked */ }
        applyTheme();
        renderSidebar(ctx);
      } }, icon("theme"), h("span.nav-label", `Theme: ${theme}`));
    SG.put(sidebar,
      h("a.brand", { href: set ? href(["s", set]) : "#/", "aria-label": "skillgraph home" }, icon("logo", 20), h("span.nav-label", "skillgraph")),
      h("nav.nav", { "aria-label": "Main" },
        set ? [
          item("overview", "Overview", "overview", href(["s", set])),
          item("recommendations", "Recommendations", "recs", href(["s", set, "recommendations"]),
            highs ? h("span.nav-count.bad", { title: `${highs} high-priority` }, highs) : null),
          item("skills", "Skills", "skills", href(["s", set, "skills"])),
          skills.length ? h("div.nav-sub", skills.map((name) => h("a.nav-subitem", {
            href: href(["s", set, "skill", name]), "aria-current": skillName === name ? "page" : null,
            onclick: () => navOpen(false), title: name }, h("span.nav-label", name)))) : null,
          item("runs", "Runs", "runs", href(["s", set, "runs"]), S ? h("span.nav-count", S.runs.length) : null),
        ] : null,
        item("compare", "Compare", "compare", other ? href(["compare", set || SG.SETS[0].id, other.id]) : href(["compare"]))),
      h("div.nav-foot",
        item("glossary", "Glossary", "glossary", "#/glossary"),
        set && S && S.data.skills.length ? h("a.nav-item", { href: SG.isStatic ? `report-${set}.html` : `/report/${enc(set)}`, target: "_blank", rel: "noopener",
          title: "Claim graph report (opens a new tab)" }, icon("report"), h("span.nav-label", "Claim graph ↗")) : null,
        themeBtn));
  }

  // ------------------------------------------------------------ top bar
  function renderTopbar(ctx) {
    const { set, crumbs, hidePicker } = ctx;
    const picker = h("select.control.set-picker", { "aria-label": "Run set", disabled: SG.SETS.length < 2,
      onchange: (e) => {
        const { parts } = SG.parseHash();
        const keep = parts[0] === "s" && ["recommendations", "skills", "runs"].includes(parts[2]) ? [parts[2]] : [];
        location.hash = href(["s", e.target.value, ...keep]);
      } }, SG.SETS.map((s) => h("option", { value: s.id, selected: s.id === set }, SG.setLabel(s))));
    SG.put(topbar,
      h("button.icon-button.menu", { type: "button", "aria-label": "Open navigation", onclick: () => navOpen(true) }, icon("menu")),
      set && !hidePicker ? h("label.set-switch", h("span.visually-hidden", "Run set"), picker) : null,
      h("nav.crumbs", { "aria-label": "Breadcrumb" },
        (crumbs || []).map(([label, target], i, all) => [
          i ? h("span.sep", { "aria-hidden": "true" }, "›") : null,
          target && i < all.length - 1 ? h("a", { href: target }, label) : h("span", { "aria-current": i === all.length - 1 ? "page" : null }, label),
        ])),
      h("a.icon-button.help-link", { href: "#/glossary", title: "Glossary", "aria-label": "Glossary" }, icon("glossary")));
  }
  SG.setLabel = (s) => (SG.SETS.filter((x) => x.name === s.name).length > 1 ? `${s.name} (${s.id})` : s.name);

  /** Pages call this once their data is in: it redraws the sidebar and breadcrumbs. */
  SG.chrome = (ctx) => { renderSidebar(ctx); renderTopbar(ctx); document.title = `${ctx.title ? ctx.title + " · " : ""}skillgraph`; };

  // ------------------------------------------------------------ router
  let routeSeq = 0;
  const leaving = [];
  /** Pages register cleanup (key handlers and the like) that runs before the next page. */
  SG.onLeave = (fn) => leaving.push(fn);
  async function route() {
    const seq = ++routeSeq;
    while (leaving.length) { try { leaving.pop()(); } catch (_) { /* ignore */ } }
    document.querySelector(".drawer-root")?.remove();
    const { parts, params } = SG.parseHash();
    try {
      if (!SG.SETS.length) SG.SETS = await SG.api("/api/sets");
      if (!SG.SETS.length) { SG.put(view, SG.empty("No run sets.")); return; }
      const first = SG.SETS[0].id;
      if (parts[0] === "diff") { location.replace(href(["compare", parts[1] || first, parts[2] || ""])); return; }
      const ctx = { params, seq, isCurrent: () => seq === routeSeq };
      if (parts[0] === "glossary") await SG.pages.glossary(ctx);
      else if (parts[0] === "compare") await SG.pages.compare({ ...ctx, a: parts[1], b: parts[2] });
      else if (parts[0] !== "s" || !SG.SETS.some((s) => s.id === parts[1])) { location.replace(href(["s", first])); return; }
      else {
        const set = parts[1];
        const page = parts[2] || "overview";
        SG.put(view, SG.skeleton(6));
        Object.assign(ctx, { set });
        if (page === "overview") await SG.pages.overview(ctx);
        else if (page === "recommendations") await SG.pages.recommendations(ctx);
        else if (page === "skills") await SG.pages.skills(ctx);
        else if (page === "skill" && parts[3]) await SG.pages.skill({ ...ctx, skill: parts[3] });
        else if (page === "claim" && parts[4]) await SG.pages.claimPage({ ...ctx, skill: parts[3], claim: parts[4] });
        else if (page === "runs") await SG.pages.runs(ctx);
        else if (page === "run" && parts.length >= 7) await SG.pages.run({ ...ctx, path: parts.slice(3).join("/") });
        else { location.replace(href(["s", set])); return; }
      }
      if (seq === routeSeq) {
        view.focus({ preventScroll: true });
        if (!params.get("event") && !params.get("claim") && !params.get("step") && !params.get("line")) window.scrollTo(0, 0);
      }
    } catch (err) {
      if (seq !== routeSeq) return;
      SG.put(view, SG.notice("bad", h("strong", "Couldn't load this page. "), String(err.message || err), " ",
        h("button.btn", { type: "button", onclick: route }, "Retry")));
    }
  }
  SG.route = route;
  window.addEventListener("hashchange", route);
  renderSidebar({});
  route();
})();
