"""skillgraph: find which lines of a SKILL.md an agent follows, ignores, or doesn't need.

  set up      skillgraph doctor                      check credentials and resolved models
              skillgraph models --search sonnet      list usable models (Claude + OpenRouter)
              skillgraph init path/to/skill --dir evals/my-skill
  evaluate    skillgraph pipeline evals/my-skill/suite.yaml --out runs/my-skill
  step-wise   extract -> lint -> run -> check -> ablate -> report
  trees       skillgraph import ~/.claude/projects/<project> --suite S --skill X --out runs/prod
              skillgraph reference docs/sop.md          SOP / decision tree -> reference tree
              skillgraph coverage S                     skill graph vs reference tree
              skillgraph tasks S --skill X              one draft task per reference path
  results     skillgraph summary runs/x --fail-on ignored,gap   text / JSON / JUnit, CI exit code
              skillgraph diff runs/before runs/after         what changed after a skill edit
              skillgraph show runs/x/<skill>/<task>/skill/r1 one run as a readable transcript
  costs       skillgraph estimate S                  what a pipeline would cost; runs ask before spending
  agent skill skillgraph install-skill --user     let Claude Code drive skillgraph for you

Models are `provider:model`, e.g. claude:claude-opus-5-5 or openrouter:openai/gpt-6.1-sol.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

from skillgraph import __version__, config, costs
from skillgraph.aggregate import aggregate_skill, collect, pick_ablation_spans
from skillgraph.checker import check_run_dirs, pending_checks
from skillgraph.extract import extract
from skillgraph.graph import checkability, lint, to_cypher, to_graphml
from skillgraph.providers import LLMError, parse_model
from skillgraph.runner import RunConfig, Variant, load_runset, load_suite, plan_jobs, run_matrix
from skillgraph.schema import is_stale, load_claims, save_json

EFFORTS = ["low", "medium", "high", "xhigh", "max"]


def _csv(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


def _suite_models(args) -> dict:
    path = getattr(args, "suite", None)
    if path and Path(path).exists():
        return load_suite(path).models
    return {}


# ---------------------------------------------------------------- setup commands

def cmd_init(args) -> None:
    from skillgraph.scaffold import init_suite

    skill_dir = Path(args.skill_dir)
    out = Path(args.dir) if args.dir else Path("evals") / skill_dir.resolve().name
    suite = init_suite(skill_dir, out, force=args.force)
    print(f"created {suite}\n\nnext:\n"
          f"  1. edit {suite}: write 2-4 realistic tasks and their success checks\n"
          f"  2. put each task's starting files in {out}/fixtures/<task>/\n"
          f"  3. skillgraph extract {suite}     # then review {out}/claims/*.json\n"
          f"  4. skillgraph pipeline {suite} --out runs/{out.name} --runs 1   # smoke test first")


def cmd_install_skill(args) -> None:
    from skillgraph.scaffold import install_skill

    root = Path.home() if args.user else Path(args.project or ".").resolve()
    dst = install_skill(root, force=args.force)
    print(f"installed the skillgraph skill at {dst}\n"
          "In Claude Code, ask to evaluate or harden a skill, or type /skillgraph.")


async def _ping(ref) -> str:
    from skillgraph import providers

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
    try:
        res = await providers.structured("Return ok=true.", schema, system="Reply with JSON.", model=ref)
        return f"ok (${res.cost_usd:.4f})"
    except Exception as exc:
        return f"FAILED: {str(exc)[:160]}"


async def cmd_doctor(args) -> int:
    print(f"skillgraph {__version__} on Python {sys.version.split()[0]}")
    problems = 0

    def line(label, ok, detail):
        nonlocal problems
        problems += 0 if ok in (True, None) else 1
        mark = {True: "ok  ", False: "FAIL", None: "--  "}[ok]
        print(f"  {mark} {label:28} {detail}")

    print("\nproviders")
    try:
        import claude_agent_sdk
        sdk = f"claude-agent-sdk {getattr(claude_agent_sdk, '__version__', '?')}"
        sdk_ok = True
    except ImportError:
        sdk, sdk_ok = "not installed (pip install 'skillgraph[claude]')", False
    auth = "ANTHROPIC_API_KEY" if os.environ.get("ANTHROPIC_API_KEY") else \
        "your Claude Code login (run `claude` once and /login if a call fails)"
    line("claude", sdk_ok or None, f"{sdk}; auth: {auth}" if sdk_ok else sdk)
    has_or = bool(os.environ.get("OPENROUTER_API_KEY"))
    line("openrouter", True if has_or else None,
         "OPENROUTER_API_KEY set" if has_or else "OPENROUTER_API_KEY not set (needed for openrouter:* models)")
    line("git", bool(shutil.which("git")) or False, shutil.which("git") or "not found; fixtures need git")

    print("\nmodels" + (f" (suite: {args.suite})" if getattr(args, "suite", None) else ""))
    resolved = config.describe(_suite_models(args), agent=args.agent_model, extractor=args.extractor_model,
                               judge=args.judge_model)
    for role, (ref, source) in resolved.items():
        usable = (ref.provider == "claude" and sdk_ok) or (ref.provider == "openrouter" and has_or)
        detail = f"{ref}  [{source}]" + ("" if usable else f"  <- {ref.provider} isn't set up")
        line(role, usable, detail)

    if args.ping:
        print("\nping (one tiny structured call per distinct model)")
        for ref in dict.fromkeys(r for r, _ in resolved.values()):
            result = await _ping(ref)
            line(str(ref), result.startswith("ok"), result)
    print("\nall good" if not problems else f"\n{problems} problem(s) above")
    return 1 if problems else 0


async def cmd_models(args) -> None:
    from skillgraph.providers import openrouter

    term = (args.search or "").lower()
    print("claude (via the Claude Agent SDK; prefix claude:)")
    for mid, note in config.CLAUDE_MODELS.items():
        if term in mid:
            print(f"  claude:{mid:34} {note}")
    try:
        models = await openrouter.list_models()
    except Exception as exc:
        print(f"\nopenrouter: couldn't fetch the model list ({exc})")
        return
    rows = []
    for m in models:
        if m["id"].endswith(":batch"):  # batch endpoints can't run an interactive tool loop
            continue
        params = set(m.get("supported_parameters") or [])
        agent_ok = "tools" in params
        if not args.all and not agent_ok:
            continue
        if term and term not in m["id"].lower() and term not in (m.get("name") or "").lower():
            continue
        p = m.get("pricing") or {}
        try:
            price = f"${float(p.get('prompt', 0)) * 1e6:7.2f} / ${float(p.get('completion', 0)) * 1e6:7.2f}"
        except ValueError:
            price = "?"
        flags = ("agent " if agent_ok else "      ") + ("json" if "structured_outputs" in params else "    ")
        rows.append((m.get("created", 0), f"  openrouter:{m['id']:44} {price}  {flags}  {m.get('context_length', '?'):>8}"))
    rows.sort(reverse=True)
    print("\nopenrouter (prefix openrouter:; $ per 1M tokens in / out; agent = tool calling, json = structured outputs)")
    for _, row in rows[: args.limit]:
        print(row)
    if len(rows) > args.limit:
        print(f"  ... {len(rows) - args.limit} more (use --limit or --search)")


# ---------------------------------------------------------------- extract / lint / graph

async def cmd_extract(args) -> None:
    suite = load_suite(args.suite)
    model = config.resolve("extractor", args.extractor_model, suite.models)
    for s in suite.skills:
        if args.skills and s.name not in args.skills:
            continue
        md = (s.dir / "SKILL.md").read_text()
        if s.claims_path.exists() and not args.force and not is_stale(load_claims(s.claims_path), md):
            print(f"{s.name}: claims up to date ({s.claims_path})")
        else:
            print(f"{s.name}: extracting with {model} ...", flush=True)
            doc, warnings = await extract(s.dir, model=model, effort=args.extractor_effort)
            save_json(s.claims_path, doc)
            for w in warnings:
                print(f"  warning: {w}")
            print(f"  {len(doc['claims'])} claims, {len(doc['conditions'])} conditions, "
                  f"{doc['classification']}, {checkability(doc):.0%} mechanically checkable -> {s.claims_path}")
        if s.reference_path:
            await _compile_reference(s, model, args.force)


async def _compile_reference(s, model, force: bool) -> None:
    from skillgraph.trees.reference import compile_reference, load_reference, reference_sha, validate_reference

    ref = load_reference(s.reference_path)
    problems = validate_reference(ref)
    if problems:
        raise ValueError(f"{s.reference_path}: " + "; ".join(problems))
    if s.ref_claims_path.exists() and not force and load_claims(s.ref_claims_path).get("reference_sha") == reference_sha(ref):
        print(f"{s.name}: reference claims up to date ({s.ref_claims_path})")
        return
    print(f"{s.name}: compiling reference {ref['name']!r} with {model} ...", flush=True)
    doc, warnings = await compile_reference(ref, skill=s.name, model=str(model))
    save_json(s.ref_claims_path, doc)
    for w in warnings:
        print(f"  warning: {w}")
    print(f"  {len(doc['claims'])} steps, {len(doc['conditions'])} branch conditions, "
          f"{checkability(doc):.0%} mechanically checkable -> {s.ref_claims_path}")


def cmd_lint(args) -> int:
    suite = load_suite(args.suite)
    worst = 0
    for s in suite.skills:
        if args.skills and s.name not in args.skills:
            continue
        if not s.claims_path.exists():
            print(f"{s.name}: no claims file yet; run `skillgraph extract` first")
            continue
        doc = load_claims(s.claims_path)
        md = (s.dir / "SKILL.md").read_text()
        print(f"\n{s.name}  ({doc['classification']}, {len(doc['claims'])} claims, "
              f"{checkability(doc):.0%} mechanically checkable){'  [STALE: SKILL.md changed]' if is_stale(doc, md) else ''}")
        for f in lint(doc, s.dir, md):
            span = f"L{f['span'][0]}-{f['span'][1]}" if f["span"] else ""
            print(f"  {f['severity']:5} {f['rule']:22} {span:9} {f['message']}")
            worst = max(worst, {"info": 0, "warn": 1, "error": 2}[f["severity"]])
    return 1 if worst == 2 else 0


def cmd_graph(args) -> None:
    suite = load_suite(args.suite)
    doc = load_claims(suite.skill(args.skill).claims_path)
    if args.format == "graphml":
        if not args.output:
            sys.exit("--output is required for graphml")
        to_graphml(doc, args.output)
        print(f"wrote {args.output}")
        return
    text = to_cypher(doc) if args.format == "cypher" else json.dumps(doc, indent=2)
    if args.output:
        Path(args.output).write_text(text)
        print(f"wrote {args.output}")
    else:
        print(text)


# ---------------------------------------------------------------- run / check / ablate / report

def _docs_for(out: Path) -> tuple[dict, dict, dict]:
    """(runset meta, skill claims docs, reference claims docs) for a run directory."""
    meta = load_runset(out)
    docs, ref_docs = {}, {}
    for name, info in meta["skills"].items():
        claims = Path(info["claims"])
        if claims.exists():
            docs[name] = load_claims(claims)
        ref_claims = claims.with_name(f"{name}.reference.claims.json")
        if info.get("reference") and ref_claims.exists():
            ref_docs[name] = load_claims(ref_claims)
    return meta, docs, ref_docs


def _gate(args, est, what: str) -> None:
    """Show the estimate and get a yes before spending (once per pipeline)."""
    if getattr(args, "_confirmed", False):
        return
    costs.confirm(est, yes=getattr(args, "yes", False), max_total_usd=getattr(args, "max_total_usd", None), what=what)


def _parse_lines(spec: str) -> list[list[int]]:
    spans = []
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        try:
            spans.append([int(a), int(b or a)])
        except ValueError:
            raise ValueError(f"--lines takes ranges like 12-14 or 12 (got {part!r})") from None
    for a, b in spans:
        Variant.ablate(a, b)  # validates the range
    return spans


def _run_plan(args, suite):
    cfg = RunConfig(agent=config.resolve("agent", args.agent_model, suite.models), effort=args.effort,
                    trigger=args.trigger, max_turns=args.max_turns, budget_usd=args.budget, enforce=args.enforce)
    variants = [Variant.parse(v) for v in (_csv(args.variants) or ["skill", "baseline"])]
    jobs = plan_jobs(suite, Path(args.out), variants=variants, runs=args.runs, skills=args.skills, tasks=_csv(args.tasks),
                     force=args.force, retry_errors=args.retry_errors)
    return cfg, variants, jobs


async def cmd_run(args) -> None:
    suite = load_suite(args.suite)
    cfg, variants, jobs = _run_plan(args, suite)
    if cfg.enforce and any(not s.claims_path.exists() for s in suite.skills):
        raise ValueError("--enforce needs claims files; run `skillgraph extract` first")
    judge = config.resolve("judge", None, suite.models)
    _gate(args, costs.estimate(agent_runs=len(jobs), judge_calls=0, agent=cfg.agent, judge=judge,
                               history_dirs=[Path(args.out)]), f"{len(jobs)} agent runs")
    await run_matrix(suite, Path(args.out), variants=variants, runs=args.runs, cfg=cfg, concurrency=args.concurrency,
                     max_total_usd=getattr(args, "max_total_usd", None), jobs=jobs)


async def cmd_check(args) -> None:
    out = Path(args.out)
    meta, docs, ref_docs = _docs_for(out)
    judge = config.resolve("judge", args.judge_model, load_suite(meta["suite"]).models if Path(meta["suite"]).exists() else {})
    run_dirs = [p.parent for p in sorted(out.glob("*/*/*/r*/trace.json")) if json.loads(p.read_text())["skill"] in docs]
    todo = pending_checks(run_dirs, docs, ref_docs=ref_docs, force=args.force)
    agent = parse_model(meta.get("model") or config.DEFAULTS["agent"])
    _gate(args, costs.estimate(agent_runs=0, judge_calls=len(todo), agent=agent, judge=judge, history_dirs=[out]),
          f"the judge on {len(todo)} traces (cached verdicts are free, so this is an upper bound)")
    n = await check_run_dirs(run_dirs, docs, judge_model=str(judge), judge_effort=args.judge_effort,
                             concurrency=args.concurrency, force=args.force, ref_docs=ref_docs)
    print(f"checked {n} traces" if n else "all traces already checked")


async def cmd_ablate(args) -> None:
    out = Path(args.out)
    meta, docs, ref_docs = _docs_for(out)
    main_suite = load_suite(meta["suite"])
    rows = collect(out)
    agent = config.resolve("agent", args.agent_model or meta.get("model"), main_suite.models)
    judge = config.resolve("judge", args.judge_model, main_suite.models)
    cfg = RunConfig(agent=agent, effort=args.effort, trigger=meta.get("trigger", "invoke"),
                    max_turns=args.max_turns, budget_usd=args.budget)
    plans = []
    for name, info in meta["skills"].items():
        if name not in docs or (args.skills and name not in args.skills):
            continue
        suite = load_suite(info.get("suite") or meta["suite"])
        s = suite.skill(name)
        if args.lines:
            spans = _parse_lines(args.lines)
        else:
            spans = pick_ablation_spans(aggregate_skill(s.name, docs[s.name], s.dir, rows), top=args.top)
        if not spans:
            print(f"{s.name}: nothing to ablate (needs checked skill and baseline runs)")
            continue
        variants = [Variant.ablate(a, b) for a, b in spans]
        jobs = plan_jobs(suite, out, variants=variants, runs=args.runs, skills=[s.name])
        plans.append((suite, s, variants, jobs))
    total = sum(len(j) for *_, j in plans)
    if not total:
        print("no ablation runs to do")
        return
    _gate(args, costs.estimate(agent_runs=total, agent=agent, judge=judge, history_dirs=[out]),
          f"{total} ablation runs plus grading")
    for suite, s, variants, jobs in plans:
        print(f"{s.name}: ablating {', '.join(v.name for v in variants)}")
        dirs = await run_matrix(suite, out, variants=variants, runs=args.runs, cfg=cfg, concurrency=args.concurrency,
                                max_total_usd=getattr(args, "max_total_usd", None), jobs=jobs)
        await check_run_dirs(dirs, docs, judge_model=str(judge), judge_effort=args.judge_effort,
                             concurrency=args.concurrency, ref_docs=ref_docs)


def cmd_report(args) -> None:
    from skillgraph.report import write_report

    out = Path(args.out)
    path = write_report(out, Path(args.output) if args.output else out / "report.html")
    print(f"wrote {path}")


def _pipeline_estimate(args) -> costs.Estimate:
    """Everything a pipeline will pay for: compiling, aligning, runs, grading, ablation."""
    from skillgraph.trees.reference import load_reference, reference_sha

    suite = load_suite(args.suite)
    extractor = config.resolve("extractor", args.extractor_model, suite.models)
    judge = config.resolve("judge", args.judge_model, suite.models)
    cfg, variants, jobs = _run_plan(args, suite)
    calls = 0
    for s in suite.skills:
        if args.skills and s.name not in args.skills:
            continue
        md = (s.dir / "SKILL.md").read_text()
        if args.force or not s.claims_path.exists() or is_stale(load_claims(s.claims_path), md):
            calls += 1
        if s.reference_path:
            ref = load_reference(s.reference_path)
            if not s.ref_claims_path.exists() or load_claims(s.ref_claims_path).get("reference_sha") != reference_sha(ref):
                calls += 1
            calls += 1  # coverage alignment (skipped when its lockfile is current)
    ablation = 0
    if args.top > 0:
        for s in suite.skills:
            if not args.skills or s.name in args.skills:
                ablation += 3 * args.top * len(s.tasks) * args.runs
    return costs.estimate(agent_runs=len(jobs) + ablation, judge_calls=len(jobs) + ablation, agent=cfg.agent,
                          judge=judge, history_dirs=[Path(args.out)], extract_calls=calls, extractor=extractor)


def cmd_estimate(args) -> None:
    est = _pipeline_estimate(args)
    print(est.render())
    if args.top > 0:
        print("  (ablation is counted at its maximum: 3 spans per skill per --top; the real number is often lower)")


async def cmd_pipeline(args) -> None:
    _gate(args, _pipeline_estimate(args), "this pipeline")
    args._confirmed = True
    await cmd_extract(args)
    if cmd_lint(args):
        if args.strict:
            raise ValueError("lint found errors (see above); fix them or drop --strict")
        print("continuing despite lint errors (pass --strict to stop here)")
    if any(sk.reference_path for sk in load_suite(args.suite).skills):
        await cmd_coverage(args)
    await cmd_run(args)
    await cmd_check(args)
    if args.top > 0:
        await cmd_ablate(args)
    cmd_report(args)


# ---------------------------------------------------------------- read results: summary, diff, show

def cmd_summary(args) -> int:
    from skillgraph.report import build_data
    from skillgraph.summary import FAIL_KEYS, failures, summarize, to_junit, to_text

    fail_on = set(_csv(args.fail_on) or [])
    unknown = fail_on - FAIL_KEYS
    if unknown:
        raise ValueError(f"unknown --fail-on value(s) {sorted(unknown)}; choose from {', '.join(sorted(FAIL_KEYS))}")
    summary = summarize(build_data(Path(args.out)))
    fails = failures(summary, fail_on)
    if args.junit:
        Path(args.junit).write_text(to_junit(summary, fail_on))
    if args.json:
        print(json.dumps({**summary, "failures": fails}, indent=2))
    else:
        print(to_text(summary, fails), end="")
    return 1 if fails else 0


def cmd_diff(args) -> None:
    from skillgraph.diff import compare, to_markdown, to_text
    from skillgraph.report import build_data

    d = compare(build_data(Path(args.before)), build_data(Path(args.after)), skills=args.skills)
    if args.json:
        print(json.dumps(d, indent=2))
    elif args.markdown:
        print(to_markdown(d), end="")
    else:
        print(to_text(d), end="")


def cmd_show(args) -> None:
    from skillgraph.show import render

    print(render(Path(args.run_dir), full=args.full, color=None if not args.no_color else False), end="")


# ---------------------------------------------------------------- trees: import, reference, coverage, tasks

async def cmd_import(args) -> int:
    from skillgraph.runner import run_dir_for, save_runset
    from skillgraph.trees import runs as importer

    suite = load_suite(args.suite)
    skill = suite.skill(args.skill)
    extra_aliases = {}
    if args.tool_map:
        import yaml
        extra_aliases.update(yaml.safe_load(Path(args.tool_map).read_text()) or {})
    traces = []
    for src in importer.expand_sources(args.sources):
        raws, mapping_aliases = importer.load_raw(src, args.format)
        aliases = importer.build_alias_map({**mapping_aliases, **extra_aliases})
        for raw in raws:
            t = importer.to_trace(raw, skill=skill.name, variant="skill", rep=1, aliases=aliases,
                                  from_skill_load=not args.whole_session)
            if not t["skill_loaded"] and not args.include_unloaded:
                continue
            kind = "skill" if t["skill_loaded"] else "baseline"
            t["variant"] = raw.variant or {"name": kind, "kind": kind, "remove": []}
            traces.append(t)
    seen: dict[tuple, int] = {}
    for t in traces:  # two sources with the same label must not overwrite each other
        key = (t["task"], t["variant"]["name"])
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            t["task"] = f"{t['task']}-{seen[key]}"
    info = importer.summarize(traces)
    print(f"{info['traces']} traces ({info['with_skill']} loaded {skill.name}), {info['tool_calls']} tool calls")
    print("  tools: " + ", ".join(f"{k} x{v}" for k, v in sorted(info["tools"].items(), key=lambda kv: -kv[1])))
    if info["unmapped"]:
        print("  not mapped to a known tool (add them with --tool-map if they are shell/file tools): "
              + ", ".join(f"{k} x{v}" for k, v in info["unmapped"].items()))
    if not traces:
        print("nothing imported: no trace loaded the skill (use --include-unloaded to import them as baseline runs)")
        return 1
    if args.dry_run:
        return 0
    out = Path(args.out)
    save_runset(out, suite, "imported", "observed")
    written = kept = 0
    for t in traces:
        rd = run_dir_for(out, skill.name, t["task"], t["variant"]["name"], t.get("rep", 1))
        if (rd / "trace.json").exists() and not args.force:
            kept += 1
            continue
        (rd / "check.json").unlink(missing_ok=True)
        save_json(rd / "trace.json", t)
        written += 1
    note = f"; kept {kept} existing (use --force to replace them)" if kept else ""
    print(f"wrote {written} traces under {out}{note}; next: skillgraph check {out} && skillgraph report {out}")
    return 0


async def cmd_reference(args) -> int:
    from skillgraph.trees.reference import all_paths, convert, save_reference

    model = config.resolve("extractor", args.extractor_model, {})
    src = Path(args.source)
    ref, problems = await convert(src, model=str(model), force_llm=args.llm)
    out = Path(args.output) if args.output else src.with_suffix(".ref.yaml")
    save_reference(ref, out)
    kinds = [n["kind"] for n in ref["nodes"].values()]
    print(f"wrote {out}: {kinds.count('action')} actions, {kinds.count('decision')} decisions, "
          f"{kinds.count('end')} end states, {len(ref['constraints'])} rules, {len(all_paths(ref))} paths")
    for p in problems:
        print(f"  check: {p}")
    print("Review it, then add `reference: <path>` to the skill in your suite.yaml.")
    return 1 if any("unknown node" in p or "not a node" in p for p in problems) else 0


async def cmd_coverage(args) -> int:
    from skillgraph.trees.coverage import align, coverage_doc, is_stale
    from skillgraph.trees.reference import load_reference

    suite = load_suite(args.suite)
    model = config.resolve("extractor", args.extractor_model, suite.models)
    worst = 0
    for s in suite.skills:
        if (args.skills and s.name not in args.skills) or not s.reference_path:
            continue
        if not s.claims_path.exists():
            print(f"{s.name}: no claims yet; run `skillgraph extract` first")
            continue
        ref, doc = load_reference(s.reference_path), load_claims(s.claims_path)
        cov = json.loads(s.coverage_path.read_text()) if s.coverage_path.exists() else None
        if cov is None or args.force or is_stale(cov, ref, doc):
            print(f"{s.name}: aligning with {ref['name']!r} using {model} ...", flush=True)
            alignment, cost = await align(ref, doc, model=str(model))
            cov = coverage_doc(ref, doc, alignment, str(model), cost)
            save_json(s.coverage_path, cov)
        sc = cov["score"]
        pct = f"{sc['coverage']:.0%}" if sc["coverage"] is not None else "n/a"
        print(f"\n{s.name} vs {ref['name']}: {pct} covered ({sc['covers']} covered, {sc['partial']} partial, "
              f"{sc['missing']} missing, {sc['conflicts']} conflicting of {sc['total']}); {cov['paths']} paths")
        for f in cov["findings"]:
            print(f"  {f['severity']:5} {f['rule']:18} {f['message']}")
            worst = max(worst, {"info": 0, "warn": 1, "error": 2}[f["severity"]])
    return 1 if worst == 2 else 0


async def cmd_tasks(args) -> None:
    from skillgraph.trees.coverage import tested_paths
    from skillgraph.trees.reference import all_paths, load_reference
    from skillgraph.trees.taskgen import generate

    suite = load_suite(args.suite)
    s = suite.skill(args.skill)
    if not s.reference_path:
        raise ValueError(f"{s.name} has no `reference:` in {suite.path}")
    ref = load_reference(s.reference_path)
    paths = all_paths(ref)
    only = None
    if args.untested_in:
        held = [{k: v.get("value") for k, v in json.loads(p.read_text()).get("reference", {}).get("conditions", {}).items()}
                for p in Path(args.untested_in).glob(f"{s.name}/*/skill/r*/check.json")]
        only = [i for i, t in enumerate(tested_paths(ref, held)) if not t]
        print(f"{len(paths) - len(only)} of {len(paths)} paths already tested in {args.untested_in}")
    elif args.paths:
        try:
            only = [int(x) for x in args.paths.split(",")]
        except ValueError:
            raise ValueError("--paths takes path indexes like 0,2") from None
        bad = [i for i in only if not 0 <= i < len(paths)]
        if bad:
            raise ValueError(f"--paths {bad} out of range; this reference has paths 0-{len(paths) - 1}")
    if only == []:
        print("nothing to generate")
        return
    model = config.resolve("extractor", args.extractor_model, suite.models)
    out = Path(args.output) if args.output else suite.path.with_name("suite.generated.yaml")
    print(f"generating {len(only) if only is not None else len(paths)} draft tasks with {model} ...", flush=True)
    path, summaries = await generate(ref, skill=s, suite_dir=suite.path.parent, out_file=out, only=only, model=str(model))
    for t in summaries:
        print(f"  {t['id']}: {', '.join(t['files']) or 'no files'}; {t['notes']}")
        for w in t["warnings"]:
            print(f"    REVIEW: {w if w.startswith('refused') else 'setup.sh ' + w}")
    print(f"wrote {path}. Review prompts, fixtures and setup.sh, then: skillgraph run {path} --out runs/{s.name}-paths")


# ---------------------------------------------------------------- argparse

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="skillgraph", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"skillgraph {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    def agent_opts(sp):
        sp.add_argument("--agent-model", "--model", dest="agent_model", metavar="SPEC",
                        help=f"model under test (default {config.DEFAULTS['agent']})")
        sp.add_argument("--effort", choices=EFFORTS, help="reasoning effort for the agent")

    def extractor_opts(sp):
        sp.add_argument("--extractor-model", metavar="SPEC", help=f"model that compiles claims (default {config.DEFAULTS['extractor']})")
        sp.add_argument("--extractor-effort", choices=EFFORTS, help="reasoning effort for the extractor")

    def judge_opts(sp):
        sp.add_argument("--judge-model", metavar="SPEC", help=f"model that grades judgment claims (default {config.DEFAULTS['judge']})")
        sp.add_argument("--judge-effort", choices=EFFORTS, help="lower is cheaper")

    def run_opts(sp):
        sp.add_argument("--runs", type=int, default=2, help="repeats per task and variant (default %(default)s)")
        sp.add_argument("--concurrency", type=int, default=4, help="runs in parallel (default %(default)s)")
        sp.add_argument("--max-turns", type=int, default=40, help="agent turns per run before it is stopped (default %(default)s)")
        sp.add_argument("--budget", type=float, default=3.0, help="per-run cost ceiling in USD (default %(default)s)")

    def spend_opts(sp):
        sp.add_argument("-y", "--yes", action="store_true", help="don't ask before spending (needed in scripts and CI)")
        sp.add_argument("--max-total-usd", type=float, metavar="USD",
                        help="refuse if the estimate is higher, and stop starting runs once this much is spent")

    def matrix_opts(sp):
        sp.add_argument("--variants", default="skill,baseline", help="comma list: skill, baseline, ablate-L<a>-<b>")
        sp.add_argument("--skills", type=_csv, help="only these skills (comma list)")
        sp.add_argument("--tasks", help="only these task ids (comma list)")
        sp.add_argument("--trigger", choices=["invoke", "auto"], default="invoke",
                        help="invoke: load the skill by name up front; auto: the agent decides whether to load it")
        sp.add_argument("--enforce", action="store_true", help="block must_not rules during runs")
        sp.add_argument("--force", action="store_true", help="redo work that already exists")
        sp.add_argument("--retry-errors", action="store_true", help="rerun runs that ended with an error")

    sp = sub.add_parser("doctor", help="check credentials, providers, and which models each role resolves to")
    sp.add_argument("suite", nargs="?", help="a suite.yaml, to include its models: block")
    sp.add_argument("--ping", action="store_true", help="make one tiny call per distinct model (costs a cent or so)")
    sp.add_argument("--agent-model", metavar="SPEC", help="check this agent model instead")
    sp.add_argument("--extractor-model", metavar="SPEC", help="check this extractor model instead")
    sp.add_argument("--judge-model", metavar="SPEC", help="check this judge model instead")

    sp = sub.add_parser("models", help="list usable models (Claude, and OpenRouter models with tool calling)")
    sp.add_argument("--search", help="filter by substring")
    sp.add_argument("--limit", type=int, default=40, help="how many OpenRouter models to list (default %(default)s)")
    sp.add_argument("--all", action="store_true", help="include OpenRouter models without tool calling")

    sp = sub.add_parser("init", help="scaffold an eval suite for a skill")
    sp.add_argument("skill_dir", help="directory containing SKILL.md")
    sp.add_argument("--dir", help="where to create the suite (default evals/<skill-dir-name>)")
    sp.add_argument("--force", action="store_true", help="overwrite an existing suite.yaml")

    sp = sub.add_parser("install-skill", help="install the skillgraph skill for Claude Code")
    where = sp.add_mutually_exclusive_group()
    where.add_argument("--user", action="store_true", help="into ~/.claude/skills (all projects)")
    where.add_argument("--project", metavar="DIR", help="into DIR/.claude/skills (default: current directory)")
    sp.add_argument("--force", action="store_true", help="replace an installed copy")

    sp = sub.add_parser("import", help="import agent traces (run trees) from other tools as runs to check")
    sp.add_argument("sources", nargs="+", help="trace files, or a directory of Claude Code transcripts")
    sp.add_argument("--suite", required=True)
    sp.add_argument("--skill", required=True, help="the skill these traces should be graded against")
    sp.add_argument("--out", required=True, help="run directory to write, e.g. runs/prod")
    sp.add_argument("--format", default="auto",
                    help="auto, claude-code, otel, langsmith, openai-agents, skillgraph, or a mapping .yaml")
    sp.add_argument("--tool-map", help="YAML of extra tool-name aliases, e.g. {execute_command: Bash}")
    sp.add_argument("--whole-session", action="store_true", help="keep events from before the skill loaded")
    sp.add_argument("--include-unloaded", action="store_true",
                    help="also import traces that never loaded the skill, as baseline runs")
    sp.add_argument("--dry-run", action="store_true", help="parse and summarize without writing")
    sp.add_argument("--force", action="store_true", help="replace traces already imported under the same name")

    sp = sub.add_parser("reference", help="convert an SOP / decision tree / flowchart into a reference tree")
    sp.add_argument("source", help="Markdown, text, Mermaid (.mmd or a ```mermaid block), or reference YAML")
    sp.add_argument("-o", "--output", help="default <source>.ref.yaml")
    sp.add_argument("--llm", action="store_true", help="use the model even when a Mermaid chart is present")
    extractor_opts(sp)

    sp = sub.add_parser("coverage", help="compare each skill with its reference tree (exit 1 on conflicts)")
    sp.add_argument("suite")
    sp.add_argument("--skills", type=_csv, help="only these skills (comma list)")
    sp.add_argument("--force", action="store_true", help="re-align even if the coverage lockfile is current")
    extractor_opts(sp)

    sp = sub.add_parser("tasks", help="draft eval tasks from a skill's reference tree, one per path")
    sp.add_argument("suite")
    sp.add_argument("--skill", required=True, help="the skill whose reference tree to use")
    sp.add_argument("--untested-in", metavar="RUNS_DIR", help="only paths no run in RUNS_DIR has taken")
    sp.add_argument("--paths", help="comma list of path indexes instead (0-based, in the order `reference` prints them)")
    sp.add_argument("-o", "--output", help="default suite.generated.yaml next to the suite")
    extractor_opts(sp)

    sp = sub.add_parser("extract", help="compile each SKILL.md (and reference tree) into a claims lockfile")
    sp.add_argument("suite")
    sp.add_argument("--skills", type=_csv, help="only these skills (comma list)")
    sp.add_argument("--force", action="store_true", help="re-extract even if the lockfile is current")
    extractor_opts(sp)

    sp = sub.add_parser("lint", help="static findings from the claim graph (exit 1 on errors)")
    sp.add_argument("suite")
    sp.add_argument("--skills", type=_csv, help="only these skills (comma list)")

    sp = sub.add_parser("graph", help="export a claim graph: cypher (Neo4j), graphml, json")
    sp.add_argument("suite")
    sp.add_argument("--skill", required=True, help="the skill to export")
    sp.add_argument("--format", choices=["cypher", "graphml", "json"], default="cypher", help="default %(default)s")
    sp.add_argument("-o", "--output", help="write to a file instead of stdout (required for graphml)")

    sp = sub.add_parser("estimate", help="what a pipeline would cost, without running anything")
    sp.add_argument("suite")
    sp.add_argument("--out", default=".skillgraph-estimate", help="run directory whose finished runs to skip and learn costs from")
    sp.add_argument("--top", type=int, default=1, help="ablation spans per category to count (0 for none)")
    matrix_opts(sp)
    agent_opts(sp)
    extractor_opts(sp)
    judge_opts(sp)
    run_opts(sp)

    sp = sub.add_parser("run", help="run the agent on the suite's tasks")
    sp.add_argument("suite")
    sp.add_argument("--out", required=True, help="run directory, e.g. runs/my-skill")
    matrix_opts(sp)
    agent_opts(sp)
    run_opts(sp)
    spend_opts(sp)

    sp = sub.add_parser("check", help="grade traces against the claims")
    sp.add_argument("out")
    sp.add_argument("--concurrency", type=int, default=4, help="traces graded in parallel (default %(default)s)")
    sp.add_argument("--force", action="store_true", help="re-grade everything, ignoring cached verdicts")
    judge_opts(sp)
    spend_opts(sp)

    sp = sub.add_parser("ablate", help="remove spans of the skill, rerun, and grade")
    sp.add_argument("out")
    sp.add_argument("--top", type=int, default=1, help="spans per category: working (control), inert, ignored")
    sp.add_argument("--lines", help="explicit spans instead, e.g. 12-14,20")
    sp.add_argument("--skills", type=_csv, help="only these skills (comma list)")
    agent_opts(sp)
    judge_opts(sp)
    run_opts(sp)
    spend_opts(sp)

    sp = sub.add_parser("report", help="write the HTML report")
    sp.add_argument("out")
    sp.add_argument("-o", "--output", help="default <out>/report.html")

    sp = sub.add_parser("pipeline", help="extract, lint, coverage, run, check, ablate, report",
                        description="Everything in order, after one cost confirmation: extract (and compile reference "
                                    "trees), lint, coverage (skills with a reference), run, check, ablate (unless --top 0), report.")
    sp.add_argument("suite")
    sp.add_argument("--out", required=True, help="run directory, e.g. runs/my-skill")
    sp.add_argument("--top", type=int, default=1, help="ablation spans per category (0 skips ablation)")
    sp.add_argument("--lines", default=None, help="ablate these spans instead, e.g. 12-14,20")
    sp.add_argument("--strict", action="store_true", help="stop when lint finds errors")
    sp.add_argument("-o", "--output", help="report path (default <out>/report.html)")
    matrix_opts(sp)
    agent_opts(sp)
    extractor_opts(sp)
    judge_opts(sp)
    run_opts(sp)
    spend_opts(sp)

    sp = sub.add_parser("summary", help="results as text, JSON, or JUnit; --fail-on for CI")
    sp.add_argument("out")
    sp.add_argument("--json", action="store_true", help="print JSON instead of text")
    sp.add_argument("--junit", metavar="FILE", help="also write JUnit XML (one test case per claim and reference step)")
    sp.add_argument("--fail-on", metavar="LIST",
                    help="exit 1 if anything matches: ignored, interfering, untested, gap, conflict, partial, unreached, "
                         "ref-ignored, lint-error, task-failure (comma list)")

    sp = sub.add_parser("diff", help="compare two run sets, e.g. before and after editing a skill")
    sp.add_argument("before")
    sp.add_argument("after")
    sp.add_argument("--skills", type=_csv, help="only these skills (comma list)")
    fmt = sp.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="print JSON")
    fmt.add_argument("--markdown", action="store_true", help="print Markdown (for pull-request comments)")

    sp = sub.add_parser("show", help="one run as a readable transcript with claim results inline")
    sp.add_argument("run_dir", help="e.g. runs/x/<skill>/<task>/<variant>/r1")
    sp.add_argument("--full", action="store_true", help="don't shorten long inputs and outputs")
    sp.add_argument("--no-color", action="store_true")
    return p


HANDLERS = {
    "doctor": cmd_doctor, "models": cmd_models, "init": cmd_init, "install-skill": cmd_install_skill,
    "import": cmd_import, "reference": cmd_reference, "coverage": cmd_coverage, "tasks": cmd_tasks,
    "estimate": cmd_estimate, "summary": cmd_summary, "diff": cmd_diff, "show": cmd_show,
    "extract": cmd_extract, "lint": cmd_lint, "graph": cmd_graph, "run": cmd_run, "check": cmd_check,
    "ablate": cmd_ablate, "report": cmd_report, "pipeline": cmd_pipeline,
}


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        result = HANDLERS[args.cmd](args)
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
    except costs.Declined as exc:
        sys.exit(f"skillgraph {args.cmd}: not started: {exc}")
    except (FileNotFoundError, FileExistsError, KeyError, ValueError, LLMError) as exc:
        if os.environ.get("SKILLGRAPH_DEBUG"):
            raise
        sys.exit(f"skillgraph {args.cmd}: {exc}  (set SKILLGRAPH_DEBUG=1 for a traceback)")
    except KeyboardInterrupt:
        sys.exit(130)
    if isinstance(result, int) and result:
        sys.exit(result)
