"""Reference trees: the process a skill is supposed to encode (an SOP, runbook,
decision tree, or flowchart), as a small tree of actions and decisions.

    name: release-process
    root: find-tag
    nodes:
      find-tag:  {kind: action, text: Find the latest release tag, next: breaking}
      breaking:
        kind: decision
        text: Does any commit since the tag contain a breaking change?
        branches:
          - {label: "yes", next: bump-major}
          - {label: "no", next: bump-minor}
      bump-major: {kind: action, text: Bump the major version, next: done}
      bump-minor: {kind: action, text: Bump the minor version, next: done}
      done: {kind: end, text: Release notes are written}
    constraints:
      - {id: no-push, text: Never push tags}

Sources: this YAML/JSON directly, Mermaid flowcharts (parsed without a model), or
any prose document (converted by an LLM). A reference compiles into a claims doc,
so the same checker grades runs against it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from skillgraph import providers
from skillgraph.config import DEFAULTS
from skillgraph.schema import CHECK_SCHEMA, CLAIM_KINDS, normalize_check, sha256_text

KINDS = ("action", "decision", "end")


# ---------------------------------------------------------------- model

def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s[:24] or "x"


def normalize_reference(raw: dict) -> dict:
    """Accept nodes as a mapping or a list of {id, ...}; fill defaults; infer root."""
    nodes_in = raw.get("nodes") or {}
    if isinstance(nodes_in, list):
        nodes_in = {n["id"]: {k: v for k, v in n.items() if k != "id"} for n in nodes_in}
    nodes = {}
    for nid, n in nodes_in.items():
        n = dict(n or {})
        kind = n.get("kind", "action")
        node = {"kind": kind if kind in KINDS else "action", "text": str(n.get("text", nid)).strip()}
        if node["kind"] == "decision":
            node["branches"], seen = [], set()
            for i, b in enumerate(n.get("branches") or [], 1):
                label = str(b.get("label", "")) or f"branch {i}"
                if _slug(label) in seen:  # "Yes"/"yes" or long labels sharing a prefix would share a condition id
                    label = f"{label} {i}"
                seen.add(_slug(label))
                node["branches"].append({"label": label, "next": b.get("next") or None})
        elif n.get("next"):
            node["next"] = n["next"]
        if n.get("lines"):
            node["lines"] = list(n["lines"])
        nodes[str(nid)] = node
    incoming = {t for n in nodes.values() for t in _targets(n)}
    root = raw.get("root") or next((nid for nid in nodes if nid not in incoming), next(iter(nodes), None))
    constraints = [{"id": str(c.get("id") or f"rule-{i}"), "text": str(c.get("text", "")).strip(),
                    **({"lines": list(c["lines"])} if c.get("lines") else {})}
                   for i, c in enumerate(raw.get("constraints") or [], 1)]
    return {"name": raw.get("name") or "reference", "description": raw.get("description", ""),
            "source": raw.get("source", ""), "root": root, "nodes": nodes, "constraints": constraints}


def _targets(node: dict) -> list[str]:
    if node["kind"] == "decision":
        return [b["next"] for b in node.get("branches", []) if b.get("next")]
    return [node["next"]] if node.get("next") else []


def validate_reference(ref: dict) -> list[str]:
    problems = []
    nodes = ref["nodes"]
    if not nodes:
        return ["the reference has no nodes"]
    if ref.get("root") not in nodes:
        problems.append(f"root {ref.get('root')!r} is not a node")
    for nid, n in nodes.items():
        for t in _targets(n):
            if t not in nodes:
                problems.append(f"{nid} points to unknown node {t!r}")
        if n["kind"] == "decision" and len(n.get("branches", [])) < 2:
            problems.append(f"decision {nid} has fewer than two branches")
        if n["kind"] == "end" and n.get("next"):
            problems.append(f"end node {nid} has a next step")
    reachable, stack = set(), [ref.get("root")]
    while stack:
        cur = stack.pop()
        if cur in reachable or cur not in nodes:
            continue
        reachable.add(cur)
        stack.extend(_targets(nodes[cur]))
    for nid in nodes:
        if nid not in reachable:
            problems.append(f"{nid} can't be reached from the root")
    ids = [c["id"] for c in ref["constraints"]]
    if len(set(ids)) != len(ids) or set(ids) & set(nodes):
        problems.append("constraint ids must be unique and differ from node ids")
    return problems


def load_reference(path: str | Path) -> dict:
    data = yaml.safe_load(Path(path).read_text())
    return normalize_reference(data or {})


def save_reference(ref: dict, path: str | Path) -> None:
    out = {k: ref[k] for k in ("name", "description", "source", "root") if ref.get(k)}
    out["nodes"] = ref["nodes"]
    if ref["constraints"]:
        out["constraints"] = ref["constraints"]
    header = "# Reference tree for skillgraph. Review it: coverage and correctness are only as good as this file.\n"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(header + yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=100))


def reference_sha(ref: dict) -> str:
    core = {"root": ref["root"], "nodes": ref["nodes"], "constraints": ref["constraints"]}
    return sha256_text(json.dumps(core, sort_keys=True))[:16]


# ---------------------------------------------------------------- paths

def all_paths(ref: dict, limit: int = 500) -> list[list[tuple[str, str | None]]]:
    """Root-to-leaf paths as [(node, branch label taken or None)]; a cycle ends a path."""
    nodes, paths = ref["nodes"], []

    def walk(nid, path, seen):
        if len(paths) >= limit:
            return
        if nid is None or nid not in nodes or nid in seen:
            paths.append(path)
            return
        n = nodes[nid]
        if n["kind"] == "decision" and n.get("branches"):
            for b in n["branches"]:
                walk(b.get("next"), path + [(nid, b["label"])], seen | {nid})
        elif n.get("next"):
            walk(n["next"], path + [(nid, None)], seen | {nid})
        else:
            paths.append(path + [(nid, None)])

    walk(ref["root"], [], frozenset())
    return [p for p in paths if p]


def path_id(path: list[tuple[str, str | None]]) -> str:
    choices = [f"{_slug(n)}-{_slug(lbl)}" for n, lbl in path if lbl is not None]
    return "path-" + ("-".join(choices) if choices else "main")


def cond_id(decision: str, label: str) -> str:
    return f"{decision}={_slug(label)}"


def dominating_branches(ref: dict) -> dict[str, list[str]]:
    """For each node, every branch condition that holds on all paths to it, outermost first."""
    on_paths: dict[str, list[list[str]]] = {}
    for path in all_paths(ref):
        taken: list[str] = []
        for nid, label in path:
            on_paths.setdefault(nid, []).append(list(taken))
            if label is not None:
                taken.append(cond_id(nid, label))
    out = {}
    for nid, lists in on_paths.items():
        common = set(lists[0]).intersection(*map(set, lists[1:])) if lists else set()
        out[nid] = [c for c in lists[0] if c in common]
    return out


def dominating_branch(ref: dict) -> dict[str, str | None]:
    """For each node, the innermost branch condition that holds on every path to it."""
    on_paths: dict[str, list[list[str]]] = {}
    for path in all_paths(ref):
        taken: list[str] = []
        for nid, label in path:
            on_paths.setdefault(nid, []).append(list(taken))
            if label is not None:
                taken.append(cond_id(nid, label))
    out = {}
    for nid, lists in on_paths.items():
        common = set(lists[0]).intersection(*map(set, lists[1:])) if lists else set()
        out[nid] = next((c for c in reversed(lists[0]) if c in common), None)
    return out


def action_predecessors(ref: dict) -> dict[str, list[str]]:
    """Nearest non-decision predecessors of each node (decisions are looked through)."""
    nodes = ref["nodes"]
    parents: dict[str, set[str]] = {nid: set() for nid in nodes}
    for nid, n in nodes.items():
        for t in _targets(n):
            if t in parents:
                parents[t].add(nid)

    def resolve(nid, seen=frozenset()):
        out = set()
        for p in parents.get(nid, ()):
            if p in seen:
                continue
            if nodes[p]["kind"] == "decision":
                out |= resolve(p, seen | {p})
            else:
                out.add(p)
        return out

    return {nid: sorted(resolve(nid)) for nid in nodes}


def precedes(ref: dict) -> set[tuple[str, str]]:
    """(a, b) when b is reachable from a and not the other way round."""
    nodes = ref["nodes"]
    reach: dict[str, set[str]] = {}
    for start in nodes:
        seen, stack = set(), list(_targets(nodes[start]))
        while stack:
            cur = stack.pop()
            if cur in seen or cur not in nodes:
                continue
            seen.add(cur)
            stack.extend(_targets(nodes[cur]))
        reach[start] = seen
    return {(a, b) for a in nodes for b in reach[a] if a not in reach.get(b, set())}


# ---------------------------------------------------------------- mermaid

_SHAPES = [  # (open, close, kind hint)
    ("([", "])", "terminal"), ("((", "))", "terminal"), ("{{", "}}", "decision"), ("[[", "]]", "action"),
    ("[(", ")]", "action"), ("[/", "/]", "action"), ("[\\", "\\]", "action"), ("{", "}", "decision"),
    ("[", "]", "action"), ("(", ")", "action"), (">", "]", "action"),
]
_ID = re.compile(r"\s*([A-Za-z0-9_](?:\w|-(?![-.>=]))*)")  # a hyphen is part of an id unless an arrow starts there
_EDGE = re.compile(
    r"\s*(?:--\s*(?P<l1>[^-|>\s][^|>]*?)\s*-->|==\s*(?P<l2>[^=|>\s][^|>]*?)\s*==>|-\.\s*(?P<l3>[^.|>\s][^|>]*?)\s*\.->"
    r"|(?P<op><-->|-\.->|==>|-->|---|--x|--o))\s*(?:\|(?P<l4>[^|]*)\|)?")
_SKIP = re.compile(r"^\s*(?:(?:flowchart|graph|classDef|class|style|linkStyle|subgraph|click|direction)\b(?![-\w])|end\s*$|%%)")


def _clean(text: str) -> str:
    text = text.strip().strip('"').strip("'")
    return re.sub(r"\s+", " ", re.sub(r"<br\s*/?>", " ", text)).strip()


def _parse_node(line: str, pos: int):
    m = _ID.match(line, pos)
    if not m:
        return None, pos
    nid, pos = m.group(1), m.end()
    for open_, close, kind in _SHAPES:
        if line.startswith(open_, pos):
            end = line.find(close, pos + len(open_))
            if end >= 0:
                return (nid, _clean(line[pos + len(open_):end]), kind), end + len(close)
    return (nid, None, None), pos


def parse_mermaid(text: str, name: str = "reference") -> tuple[dict, list[str]]:
    """Flowchart subset: node shapes, chained edges, `-->|label|` and `-- label -->` labels, `&`."""
    block = re.search(r"```mermaid\s*\n(.*?)```", text, re.S)
    body = block.group(1) if block else text
    shapes: dict[str, tuple[str, str]] = {}
    order: list[str] = []
    edges: list[tuple[str, str, str]] = []
    warnings: list[str] = []

    def remember(node):
        nid, label, kind = node
        if nid not in shapes:
            order.append(nid)
            shapes[nid] = (label or nid, kind or "action")
        elif label:
            shapes[nid] = (label, kind or shapes[nid][1])

    for raw in body.splitlines():
        line = raw.split("%%")[0].rstrip().rstrip(";")
        if not line.strip() or _SKIP.match(line):
            continue
        pos, prev = 0, None
        while pos < len(line):
            group = []
            node, pos = _parse_node(line, pos)
            if node is None:
                break
            group.append(node)
            while True:
                amp = re.match(r"\s*&", line[pos:])
                if not amp:
                    break
                node, pos = _parse_node(line, pos + amp.end())
                if node is None:
                    break
                group.append(node)
            for n in group:
                remember(n)
            if prev:
                for a in prev[0]:
                    for b in group:
                        edges.append((a[0], b[0], prev[1]))
            edge = _EDGE.match(line, pos)
            if not edge:
                break
            label = next((edge.group(g) for g in ("l1", "l2", "l3", "l4") if edge.group(g)), "") or ""
            prev, pos = (group, _clean(label)), edge.end()

    if not order:
        raise ValueError("no Mermaid flowchart nodes found")
    out: dict[str, list[tuple[str, str]]] = {nid: [] for nid in order}
    incoming: dict[str, int] = {nid: 0 for nid in order}
    for a, b, label in edges:
        out[a].append((b, label))
        incoming[b] += 1

    root = next((nid for nid in order if incoming[nid] == 0), order[0])
    dropped = set()
    if shapes[root][1] == "terminal" and len(out[root]) == 1:  # a "Start" marker
        dropped.add(root)
        root = out[root][0][0]

    nodes = {}
    for nid in order:
        if nid in dropped:
            continue
        label, hint = shapes[nid]
        succ = out[nid]
        if hint == "decision" or len(succ) > 1:
            if hint != "decision":
                warnings.append(f"{nid} has several next steps; treated as a decision")
            branches = [{"label": lbl or shapes[t][0], "next": t} for t, lbl in succ]
            if len(branches) < 2:
                warnings.append(f"decision {nid} has {len(branches)} branch(es)")
            nodes[nid] = {"kind": "decision", "text": label, "branches": branches}
        elif succ:
            nodes[nid] = {"kind": "action", "text": label, "next": succ[0][0]}
        else:
            nodes[nid] = {"kind": "end" if hint == "terminal" else "action", "text": label}
    return normalize_reference({"name": name, "root": root, "nodes": nodes}), warnings


def rules_section(text: str) -> list[dict]:
    """Bullets under a Markdown heading named Rules, Constraints, or Guardrails."""
    rules, inside = [], False
    for n, line in enumerate(text.splitlines(), 1):
        heading = re.match(r"^#{1,6}\s+(.*)$", line)
        if heading:
            inside = bool(re.match(r"(rules|constraints|guardrails)\b", heading.group(1).strip(), re.I))
            continue
        bullet = re.match(r"^\s*[-*+]\s+(.+)$", line)
        if inside and bullet:
            body = bullet.group(1).strip()
            rules.append({"id": "rule-" + "-".join(_slug(body).split("-")[:4]), "text": body, "lines": [n, n]})
    return rules


# ---------------------------------------------------------------- LLM conversion

CONVERT_SYSTEM = (
    "You turn process documents (SOPs, runbooks, checklists, decision trees) into reference trees "
    "for an evaluation harness. You keep the document's meaning and wording, and you never invent steps."
)

CONVERT_PROMPT = """Convert this process document into a reference tree.

<document name="{name}">
{numbered}
</document>

A reference tree has:
- actions: things the agent should do, one per node, in the document's wording. Split compound sentences only when the parts can happen independently.
- decisions: points where the process forks. Phrase each as a question about a fact of the situation ("Does any commit contain a breaking change?"), with one branch per outcome. Use short branch labels ("yes", "no", or the case name).
- end nodes: outcomes that finish the process ("Release notes are written").
- constraints: rules that apply throughout rather than at one step ("Never push tags", "Always keep the input file unchanged").

Rules:
- `next` links an action to the step after it; leave it "" for the last step on a path. Decisions use `branches` instead, and actions and end nodes leave `branches` empty.
- Ids are short kebab-case names (`find-tag`, `breaking-change`). root is the first step.
- line_start / line_end point at the 1-based document lines each node comes from.
- If the document is a plain list with no forks, produce a single chain.
- Don't add steps the document doesn't state, even obvious ones."""

REFERENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "root": {"type": "string"},
        "nodes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "kind": {"type": "string", "enum": list(KINDS)},
                "text": {"type": "string"},
                "next": {"type": "string"},
                "branches": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"label": {"type": "string"}, "next": {"type": "string"}},
                    "required": ["label", "next"], "additionalProperties": False}},
                "line_start": {"type": "integer"},
                "line_end": {"type": "integer"},
            },
            "required": ["id", "kind", "text", "next", "branches", "line_start", "line_end"],
            "additionalProperties": False}},
        "constraints": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "text": {"type": "string"},
                           "line_start": {"type": "integer"}, "line_end": {"type": "integer"}},
            "required": ["id", "text", "line_start", "line_end"], "additionalProperties": False}},
    },
    "required": ["name", "description", "root", "nodes", "constraints"],
    "additionalProperties": False,
}


async def convert_with_llm(text: str, name: str, model: str = DEFAULTS["extractor"]) -> dict:
    numbered = "\n".join(f"{i:4d}| {line}" for i, line in enumerate(text.splitlines(), 1))
    res = await providers.structured(CONVERT_PROMPT.format(name=name, numbered=numbered), REFERENCE_SCHEMA,
                                     system=CONVERT_SYSTEM, model=model)
    raw = res.data
    for n in raw["nodes"]:
        n["lines"] = [n.pop("line_start"), n.pop("line_end")]
        if not n.get("next"):
            n.pop("next", None)
    for c in raw["constraints"]:
        c["lines"] = [c.pop("line_start"), c.pop("line_end")]
    return normalize_reference(raw)


async def convert(source: Path, *, model: str = DEFAULTS["extractor"], force_llm: bool = False) -> tuple[dict, list[str]]:
    """YAML/JSON load directly, Mermaid parses without a model, anything else goes to the LLM."""
    text = source.read_text()
    name = source.stem
    if source.suffix in (".yaml", ".yml", ".json") and not force_llm:
        ref = normalize_reference(yaml.safe_load(text) or {})
        warnings: list[str] = []
    elif not force_llm and (source.suffix in (".mmd", ".mermaid") or "```mermaid" in text):
        ref, warnings = parse_mermaid(text, name)
        ref["constraints"] = rules_section(text)
        title = re.search(r"^#\s+(.+)$", text, re.M)
        ref["description"] = title.group(1).strip() if title else ""
    else:
        ref, warnings = await convert_with_llm(text, name, model), []
    ref["source"] = ref.get("source") or source.name
    return ref, warnings + validate_reference(ref)


# ---------------------------------------------------------------- compile to a claims doc

COMPILE_SYSTEM = (
    "You write checks that tell an evaluation harness whether an AI agent's run carried out each step "
    "of a reference process. You prefer mechanical checks and never change what a step asks for."
)

COMPILE_PROMPT = """Write a check for every step and branch condition of this reference process.

<reference name="{name}">
{numbered}
</reference>

The harness runs an agent (with Claude Code tool names: Read, Write, Edit, Bash, Glob, Grep, Skill) and records each tool call (name, JSON input, text output), the agent's text, and the files left in the working directory.

items: one entry per id marked [action], [end] or [rule] above.
- kind: step (an action), artifact (an outcome checkable in the final files or answer), must_not (a prohibition; its check describes the forbidden behavior, so a match is a violation), must_read (reading a specific file).
- check, with every field filled ("" or [] when unused):
  - type "tool": tools = tool names ([] = any), input_any = Python regexes against the JSON tool input (any may match), output_any = regexes against the tool output.
  - type "file": path_glob relative to the working directory, content_any = regexes (any may match; [] = existence).
  - type "judge": question = a yes/no question answerable from the trace, where "yes" means the step was done (for must_not, "yes" means the forbidden thing happened).
- Prefer tool and file checks when the step names a command, file, or exact output; accept reasonable equivalents. Use judge for anything that needs judgment.
- Agents often chain shell commands in one Bash call (`cd x && a; b | c`), so don't anchor command regexes to the start or end of the input.
- [end] items are outcomes. Check the observable result of reaching that state (a file exists, the final answer reports the outcome), not whether the agent used particular words to announce it.

conditions: one entry per id marked [branch] above (decision=label). text restates the branch as a fact about the task's inputs or situation ("a commit since the tag contains BREAKING CHANGE"), not as something the agent observed, because the harness also judges runs where the agent skipped the step that would reveal it. Use a judge check unless a tool output reliably reveals the fact."""

COMPILE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "kind": {"type": "string", "enum": CLAIM_KINDS}, "check": CHECK_SCHEMA},
            "required": ["id", "kind", "check"], "additionalProperties": False}},
        "conditions": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "text": {"type": "string"}, "check": CHECK_SCHEMA},
            "required": ["id", "text", "check"], "additionalProperties": False}},
    },
    "required": ["items", "conditions"],
    "additionalProperties": False,
}


def render(ref: dict) -> tuple[str, dict[str, int]]:
    """One line per node, branch, and rule; returns (text, id -> line)."""
    lines, at = [], {}
    for nid in _walk_order(ref):
        n = ref["nodes"][nid]
        if n["kind"] == "decision":
            lines.append(f"[decision {nid}] {n['text']}")
            at[nid] = len(lines)
            for b in n["branches"]:
                cid = cond_id(nid, b["label"])
                lines.append(f"  [branch {cid}] {b['label']} -> {b.get('next') or '(stop)'}")
                at[cid] = len(lines)
        else:
            tag = "end" if n["kind"] == "end" else "action"
            lines.append(f"[{tag} {nid}] {n['text']}" + (f"  -> {n['next']}" if n.get("next") else ""))
            at[nid] = len(lines)
    for c in ref["constraints"]:
        lines.append(f"[rule {c['id']}] {c['text']}")
        at[c["id"]] = len(lines)
    return "\n".join(f"{i:4d}| {line}" for i, line in enumerate(lines, 1)), at


def _walk_order(ref: dict) -> list[str]:
    seen, order, stack = set(), [], [ref["root"]]
    while stack:
        nid = stack.pop()
        if nid in seen or nid not in ref["nodes"]:
            continue
        seen.add(nid)
        order.append(nid)
        stack.extend(reversed(_targets(ref["nodes"][nid])))
    return order + [nid for nid in ref["nodes"] if nid not in seen]


def build_claims_doc(ref: dict, compiled: dict, skill: str) -> tuple[dict, list[str]]:
    """Structure comes from the tree (after edges, branch gating); checks come from `compiled`."""
    warnings: list[str] = []
    text, at = render(ref)
    items = {it["id"]: it for it in compiled.get("items", [])}
    conds = {c["id"]: c for c in compiled.get("conditions", [])}
    preds, gates, order = action_predecessors(ref), dominating_branches(ref), precedes(ref)

    conditions = []
    for nid, n in ref["nodes"].items():
        if n["kind"] != "decision":
            continue
        for b in n["branches"]:
            cid = cond_id(nid, b["label"])
            c = conds.get(cid) or {"text": f"{n['text']} {b['label']}", "check": {"type": "judge", "question": ""}}
            check = normalize_check(c["check"], warnings, cid)
            if check["type"] == "judge" and not check["question"]:
                check["question"] = f"For this task, is the answer to \"{n['text']}\" {b['label']}?"
            conditions.append({"id": cid, "text": c.get("text") or f"{n['text']}: {b['label']}",
                               "span": [at.get(cid, 1)] * 2, "check": check, "node": nid, "label": b["label"]})

    claims = []
    targets = [(nid, n["text"], n["kind"]) for nid, n in ref["nodes"].items() if n["kind"] != "decision"]
    targets += [(c["id"], c["text"], "rule") for c in ref["constraints"]]
    for nid, body, kind in targets:
        it = items.get(nid)
        if it is None:
            warnings.append(f"no check written for {nid}; using a judge question")
            it = {"kind": "must_not" if kind == "rule" and re.search(r"\b(never|don't|do not|must not)\b", body, re.I)
                  else "artifact" if kind == "end" else "step",
                  "check": {"type": "judge", "question": f"Did the agent do this: {body}?"}}
        claim_kind = it["kind"] if it["kind"] in CLAIM_KINDS else "step"
        g = gates.get(nid, []) if kind != "rule" else []
        claim = {
            "id": nid, "kind": claim_kind, "text": body, "span": [at.get(nid, 1)] * 2,
            "after": [p for p in preds.get(nid, []) if kind != "rule" and (p, nid) in order],
            "when": {"cond": g[-1], "value": True} if g else None,
            "check": normalize_check(it["check"], warnings, nid), "node": nid,
        }
        if len(g) > 1:  # nested branches: every gate on the way must hold
            claim["when_all"] = [{"cond": c, "value": True} for c in g]
        claims.append(claim)
    doc = {"skill": skill, "reference": ref["name"], "reference_sha": reference_sha(ref),
           "source_sha256": sha256_text(text), "classification": "procedural",
           "summary": ref.get("description", ""), "conditions": conditions, "claims": claims}
    return doc, warnings


async def compile_reference(ref: dict, *, skill: str, model: str = DEFAULTS["extractor"]) -> tuple[dict, list[str]]:
    text, _ = render(ref)
    res = await providers.structured(COMPILE_PROMPT.format(name=ref["name"], numbered=text), COMPILE_SCHEMA,
                                     system=COMPILE_SYSTEM, model=model)
    doc, warnings = build_claims_doc(ref, res.data, skill)
    doc["extracted"] = {"model": str(providers.parse_model(model)), "cost_usd": round(res.cost_usd, 4)}
    return doc, warnings


# ---------------------------------------------------------------- merging with a skill doc

REF_PREFIX = "ref:"


def merge_docs(skill_doc: dict, ref_doc: dict | None) -> dict:
    """One doc for one judge call: reference ids are prefixed with `ref:`."""
    if not ref_doc:
        return skill_doc
    p = REF_PREFIX
    claims = list(skill_doc["claims"]) + [
        {**c, "id": p + c["id"], "after": [p + a for a in c["after"]],
         "when": {"cond": p + c["when"]["cond"], "value": c["when"]["value"]} if c.get("when") else None,
         **({"when_all": [{"cond": p + w["cond"], "value": w["value"]} for w in c["when_all"]]} if c.get("when_all") else {})}
        for c in ref_doc["claims"]]
    conditions = list(skill_doc["conditions"]) + [{**k, "id": p + k["id"]} for k in ref_doc["conditions"]]
    return {**skill_doc, "claims": claims, "conditions": conditions}


def split_result(result: dict) -> dict:
    """Move `ref:` entries of a check result under result["reference"]."""
    p = REF_PREFIX
    ref = {"claims": {k[len(p):]: v for k, v in result["claims"].items() if k.startswith(p)},
           "conditions": {k[len(p):]: v for k, v in result["conditions"].items() if k.startswith(p)}}
    if not ref["claims"] and not ref["conditions"]:
        return result
    out = dict(result)
    out["claims"] = {k: v for k, v in result["claims"].items() if not k.startswith(p)}
    out["conditions"] = {k: v for k, v in result["conditions"].items() if not k.startswith(p)}
    out["reference"] = ref
    return out
