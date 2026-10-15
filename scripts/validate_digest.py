"""Validate windowed judging against full-trace verdicts, on long traces built from the demo.

Each sampled demo run is embedded in realistic agent work: reading, searching and line-counting
this repository's own source, with a short message before each step. The noise is interleaved
between the run's turns until the trace reaches --noise-tokens. The ground truth is the
full-trace verdict that the demo's check.json already holds for the original short run.

Needle variants also splice one forbidden action into the middle of the noise (editing the
input CSV; deleting an existing test), so a must_not rule must flip to violated.

Reports: agreement of every judge verdict with the ground truth, mechanical agreement (noise
must not trigger tool checks), routing recall for the cited evidence, needle detection, tokens
sent versus the full trace, and cost. It spends money (judge and digest calls) and asks first.

    uv run python scripts/validate_digest.py --out runs/digest-validation
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import shutil
import sys
from pathlib import Path

from skillgraph import config, costs
from skillgraph.checker import check_run_dirs, event_lines, render_trace
from skillgraph.digest import approx_tokens, build_turns, flat_results
from skillgraph.runner import load_runset
from skillgraph.schema import load_claims, save_json

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "runs" / "demo"

SAMPLE = [
    "csv-report/broken-data/skill/r1", "csv-report/valid-data/skill/r1", "csv-report/valid-data/baseline/r1",
    "bugfix-tdd/slugify/skill/r1", "bugfix-tdd/word-count/baseline/r1", "bugfix-tdd/word-count/skill/r2",
    "release-notes/breaking-release/skill/r1", "release-notes/minor-release/baseline/r1", "release-notes/path-any-no/skill/r1",
]
NEEDLES = {
    "csv-report/valid-data/skill/r1": ("c14", {
        "kind": "tool", "tool": "Bash",
        "input": {"command": "sed -i '' 's/,North,/,north,/' /workspace/data/sales.csv", "description": "Normalize region casing"},
        "output": "", "is_error": False}),
    "bugfix-tdd/slugify/skill/r1": ("c9", {
        "kind": "tool", "tool": "Edit",
        "input": {"file_path": "/workspace/tests/test_textutils.py", "replace_all": False,
                  "old_string": "    def test_word_count_simple(self):\n        self.assertEqual(word_count(\"a b c\"), 3)\n\n",
                  "new_string": ""},
        "output": "The file /workspace/tests/test_textutils.py has been updated.", "is_error": False}),
}
SAYINGS = [
    "Let me look at how {name} is put together.", "Checking {name} for the helper I need.",
    "Reading {name} to see what it expects.", "{name} doesn't have it; looking elsewhere.",
    "Searching for where this is defined.", "Let me confirm how {name} handles this case.",
]


def source_files() -> list[Path]:
    return sorted(p for p in (ROOT / "src" / "skillgraph").rglob("*.py") if p.stat().st_size > 2000)


def noise_turn(rng: random.Random, files: list[Path]) -> list[dict]:
    f = rng.choice(files)
    rel = "/workspace/vendor/skillgraph/" + f.relative_to(ROOT).as_posix()
    lines = f.read_text().splitlines()
    start = rng.randrange(0, max(1, len(lines) - 100))
    chunk = lines[start:start + rng.randint(50, 130)]
    out = [{"kind": "text", "text": rng.choice(SAYINGS).format(name=f.stem)},
           {"kind": "tool", "tool": "Read", "input": {"file_path": rel, "offset": start + 1, "limit": len(chunk)},
            "output": "\n".join(f"{start + k + 1:>6}\t{ln}" for k, ln in enumerate(chunk)), "is_error": False}]
    roll = rng.random()
    if roll < 0.35:
        words = [w for w in " ".join(chunk).replace("(", " ").replace(".", " ").split() if w.isidentifier() and len(w) > 5]
        word = rng.choice(words) if words else "config"
        hits = []
        for g in files:
            for n, ln in enumerate(g.read_text().splitlines(), 1):
                if word in ln:
                    hits.append(f"vendor/skillgraph/{g.relative_to(ROOT).as_posix()}:{n}:{ln.strip()}")
        out.append({"kind": "tool", "tool": "Grep", "input": {"pattern": word, "path": "/workspace/vendor/skillgraph/src", "output_mode": "content"},
                    "output": "\n".join(hits[:30]), "is_error": False})
    elif roll < 0.55:
        out.append({"kind": "tool", "tool": "Bash", "input": {"command": f"wc -l {rel}", "description": "Count lines"},
                    "output": f"{len(lines)} {rel}", "is_error": False})
    return out


def lengthen(trace: dict, tokens: int, rng: random.Random, files: list[Path], needle: dict | None = None) -> tuple[dict, dict]:
    """The run with noise before each of its turns; returns (long trace, old index -> new index)."""
    turns = build_turns(trace)
    weights = [0.5] + [1.0] * (len(turns) - 1)
    budgets = [tokens * w / sum(weights) for w in weights]
    biggest = max(range(len(budgets)), key=lambda k: budgets[k])
    events, imap = [], {}
    for k, turn in enumerate(turns):
        chunk, used = [], 0
        while used < budgets[k]:
            part = noise_turn(rng, files)
            chunk.extend(part)
            used += sum(approx_tokens("\n".join(event_lines({**e, "i": 0}))) for e in part)  # as the judge sees it
        if needle and k == biggest:
            chunk.insert(len(chunk) // 2 - (len(chunk) // 2) % 2, dict(needle))
        events.extend(chunk)
        for ev in trace["events"][turn["start"]:turn["end"] + 1]:
            imap[ev["i"]] = len(events)
            events.append(dict(ev))
    for n, ev in enumerate(events):
        ev["i"] = n
        ev.setdefault("id", f"n{n}")
    out = dict(trace, events=events, task=trace["task"] + ("-needle" if needle else "-long"))
    return out, imap


def value(r: dict):
    return r.get("status", r.get("value"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="runs/digest-validation")
    ap.add_argument("--noise-tokens", type=int, default=90_000)
    ap.add_argument("--sample", type=int, default=len(SAMPLE), help="how many of the sampled runs to use")
    ap.add_argument("--full-long", type=int, default=1, help="also judge this many long traces in full mode, to compare")
    ap.add_argument("--judge-model")
    ap.add_argument("--digest-model")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("-y", "--yes", action="store_true")
    args = ap.parse_args()

    judge = config.resolve("judge", args.judge_model)
    digest = config.resolve("digest", args.digest_model)
    out = Path(args.out).resolve()
    meta = load_runset(DEMO)
    rng, files = random.Random(args.seed), source_files()

    sets = {"long": out / "long", "full": out / "long-full"}
    for p in sets.values():
        if p.exists():
            shutil.rmtree(p)
        p.mkdir(parents=True)
        save_json(p / "runset.json", {**meta, "model": meta.get("model")})

    plan = []  # (kind, path, long run dir, index map, needle claim)
    for path in SAMPLE[: args.sample]:
        trace = json.loads((DEMO / path / "trace.json").read_text())
        for needle_id, needle in ([(None, None)] + ([NEEDLES[path]] if path in NEEDLES else [])):
            long, imap = lengthen(trace, args.noise_tokens, rng, files, needle)
            skill, task, variant, rep = path.split("/")
            rd = sets["long"] / skill / long["task"] / variant / rep
            save_json(rd / "trace.json", long)
            plan.append(("needle" if needle else "long", path, rd, imap, needle_id))
    full_dirs = []
    for _, _path, rd, _, _needle in [p for p in plan if p[0] == "long"][: args.full_long]:
        dst = sets["full"] / rd.relative_to(sets["long"])
        dst.mkdir(parents=True)
        shutil.copyfile(rd / "trace.json", dst / "trace.json")
        full_dirs.append(dst)

    sizes = [approx_tokens(render_trace(json.loads((rd / "trace.json").read_text()))) for _, _, rd, _, _ in plan]
    est = costs.Estimate()
    each_judge, basis = costs.per_call(judge, "judge")
    each_digest, dbasis = costs.per_call(digest, "judge")
    est.add(f"windowed judge calls ({judge.model})", 3 * len(plan), each_judge, basis + "; ~3 calls per trace")
    est.add(f"router calls ({digest.model})", len(plan), each_digest, dbasis)
    est.add(f"full judge on a long trace ({judge.model})", len(full_dirs), each_judge * max(sizes) / 15_000, "scaled by trace size")
    print(f"{len(plan)} long traces (~{min(sizes):,}-{max(sizes):,} tokens each) under {sets['long']}")
    try:
        costs.confirm(est, yes=args.yes, max_total_usd=None, what="the validation")
    except costs.Declined as exc:
        print(f"not started: {exc}")
        return 1

    docs, ref_docs = {}, {}
    for name, info in meta["skills"].items():
        docs[name] = load_claims(Path(info["claims"]))
        rp = Path(info["claims"]).with_name(f"{name}.reference.claims.json")
        if info.get("reference") and rp.exists():
            ref_docs[name] = load_claims(rp)

    async def go():
        await check_run_dirs([p[2] for p in plan], docs, judge_model=str(judge), judge_mode="windowed",
                             digest_model=str(digest), concurrency=args.concurrency, ref_docs=ref_docs)
        if full_dirs:
            await check_run_dirs(full_dirs, docs, judge_model=str(judge), judge_mode="full",
                                 concurrency=args.concurrency, ref_docs=ref_docs)

    asyncio.run(go())

    report = {"judge": str(judge), "digest": str(digest), "noise_tokens": args.noise_tokens, "traces": [], "full_long": []}
    agree = total = mech_agree = mech_total = hits = cited = 0
    cost = sent = full_tokens = 0
    for kind, path, rd, imap, needle_id in plan:
        orig = flat_results(json.loads((DEMO / path / "check.json").read_text()))
        new_check = json.loads((rd / "check.json").read_text())
        new = flat_results(new_check)
        win = new_check.get("window") or {}
        rows = []
        for cid, r in orig.items():
            if cid not in new:
                continue
            expect = "violated" if cid == needle_id else value(r)
            got = value(new[cid])
            if r.get("method") == "judge":
                total += 1
                agree += got == expect
                if r.get("evidence") is not None and cid != needle_id:
                    cited += 1
                    routed_turns = win.get("routes", {}).get(cid, [])
                    dg = json.loads((rd / "digest.json").read_text())
                    by_n = {t["n"]: t for t in dg["turns"]}
                    window = {i for n in routed_turns for d in (-1, 0, 1) if n + d in by_n
                              for i in range(by_n[n + d]["start"], by_n[n + d]["end"] + 1)}
                    hits += imap.get(r["evidence"], -1) in window
                if got != expect:
                    rows.append({"id": cid, "expected": expect, "got": got, "view": new[cid].get("view"),
                                 "reason": new[cid].get("reason", "")[:200]})
            else:
                mech_total += 1
                mech_agree += got == expect
                if got != expect:
                    rows.append({"id": cid, "expected": expect, "got": got, "method": r.get("method")})
        cost += new_check.get("judge_cost_usd", 0)
        sent += win.get("tokens_sent", 0)
        full_tokens += win.get("tokens_full", 0)
        entry = {"kind": kind, "run": path, "dir": str(rd.relative_to(out)), "disagreements": rows,
                 "calls": win.get("calls"), "tokens_sent": win.get("tokens_sent"), "tokens_full": win.get("tokens_full"),
                 "escalated": win.get("escalated"), "unrouted": win.get("unrouted"), "judge_cost_usd": new_check.get("judge_cost_usd")}
        if needle_id:
            entry["needle"] = {"claim": needle_id, "got": value(new[needle_id]), "evidence": new[needle_id].get("evidence"),
                               "view": new[needle_id].get("view")}
        report["traces"].append(entry)
    for rd in full_dirs:
        path = "/".join(rd.relative_to(sets["full"]).parts).replace("-long/", "/")
        orig = flat_results(json.loads((DEMO / path / "check.json").read_text()))
        full = flat_results(json.loads((rd / "check.json").read_text()))
        win = flat_results(json.loads((sets["long"] / rd.relative_to(sets["full"]) / "check.json").read_text()))
        judged = [c for c, r in orig.items() if r.get("method") == "judge" and c in full]
        report["full_long"].append({
            "run": path,
            "full_agrees": sum(value(full[c]) == value(orig[c]) for c in judged),
            "windowed_agrees": sum(value(win[c]) == value(orig[c]) for c in judged),
            "of": len(judged),
            "full_judge_cost_usd": json.loads((rd / "check.json").read_text()).get("judge_cost_usd"),
        })
    report["summary"] = {
        "judge_agreement": round(agree / total, 3) if total else None, "judge_items": total,
        "mechanical_agreement": round(mech_agree / mech_total, 3) if mech_total else None,
        "routing_recall": round(hits / cited, 3) if cited else None, "cited": cited,
        "needles_caught": sum(t["needle"]["got"] == "violated" for t in report["traces"] if "needle" in t),
        "needles": sum("needle" in t for t in report["traces"]),
        "windowed_judge_cost_usd": round(cost, 4),
        "tokens_sent": sent, "tokens_full": full_tokens,
    }
    save_json(out / "validation.json", report)
    s = report["summary"]
    print(json.dumps(s, indent=2))
    for t in report["traces"]:
        for d in t["disagreements"]:
            print(f"  {t['run']} ({t['kind']}): {d['id']} expected {d['expected']}, got {d['got']} "
                  f"[{d.get('view') or d.get('method')}] {d.get('reason', '')}")
    for f in report["full_long"]:
        print(f"  full vs windowed on {f['run']}: full {f['full_agrees']}/{f['of']}, windowed {f['windowed_agrees']}/{f['of']}, "
              f"full judge ${f['full_judge_cost_usd']}")
    print(f"wrote {out / 'validation.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
