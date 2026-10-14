"""The claim graph: NetworkX model, static lint, and exports (Cypher, GraphML)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import networkx as nx

# Paths a SKILL.md commonly points into; anything matching must exist in the skill dir.
_BUNDLE_REF = re.compile(r"(?<![\w/.-])((?:scripts|references|assets|templates|examples)/[\w./-]+\w)")


def build_graph(doc: dict) -> nx.DiGraph:
    """Nodes are claims and conditions. Edges: `precedes` (a -> b means a before b)
    and `branch` (condition -> claim, labelled with the branch value)."""
    g = nx.DiGraph(skill=doc["skill"])
    for k in doc["conditions"]:
        g.add_node(k["id"], node_type="condition", text=k["text"], span=k["span"], check=k["check"]["type"])
    for c in doc["claims"]:
        g.add_node(c["id"], node_type="claim", kind=c["kind"], text=c["text"], span=c["span"], check=c["check"]["type"])
    for c in doc["claims"]:
        for a in c["after"]:
            g.add_edge(a, c["id"], edge_type="precedes")
        if c["when"]:
            g.add_edge(c["when"]["cond"], c["id"], edge_type="branch", value=c["when"]["value"])
    return g


def checkability(doc: dict) -> float:
    claims = doc["claims"]
    if not claims:
        return 0.0
    return sum(c["check"]["type"] != "judge" for c in claims) / len(claims)


def _paragraphs(lines: list[str]) -> list[tuple[int, int]]:
    """1-based (start, end) ranges of instruction-bearing blocks: no frontmatter,
    headings, blank lines, or code-fence markers."""
    start_body = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start_body = i + 1
                break
    blocks, cur = [], None
    for i in range(start_body, len(lines)):
        s = lines[i].strip()
        boundary = not s or s.startswith("#") or s.startswith("```")
        # each list item is its own block so attribution stays line-level
        item = bool(re.match(r"^(\d+[.)]|[-*+])\s", s))
        if boundary or (item and cur):
            if cur:
                blocks.append(tuple(cur))
            cur = None
        if boundary:
            continue
        if cur is None:
            cur = [i + 1, i + 1]
        else:
            cur[1] = i + 1
    if cur:
        blocks.append(tuple(cur))
    return blocks


def lint(doc: dict, skill_dir: str | Path | None = None, skill_md: str | None = None) -> list[dict]:
    """Static findings over the claim graph and the skill text."""
    findings: list[dict] = []

    def add(severity, rule, message, span=None, ids=None):
        findings.append({"severity": severity, "rule": rule, "message": message, "span": span, "ids": ids or []})

    g = build_graph(doc)

    # Ordering cycles make the skill impossible to follow as written.
    order = nx.DiGraph([(u, v) for u, v, d in g.edges(data=True) if d["edge_type"] == "precedes"])
    for cycle in nx.simple_cycles(order):
        add("error", "order-cycle", f"Ordering cycle: {' -> '.join(cycle + cycle[:1])}", ids=cycle)

    # Branches
    for k in doc["conditions"]:
        sides = {d["value"] for _, _, d in g.out_edges(k["id"], data=True) if d["edge_type"] == "branch"}
        if not sides:
            add("warn", "unused-condition", f"Condition {k['id']} ({k['text']!r}) gates no instruction.", k["span"], [k["id"]])
        elif len(sides) == 1:
            missing = "otherwise" if True in sides else "when it holds"
            add("info", "one-sided-branch",
                f"Condition {k['id']} ({k['text']!r}) says what to do only on one side; nothing covers {missing}.",
                k["span"], [k["id"]])
        if k["check"]["type"] == "judge":
            add("warn", "subjective-condition",
                f"Condition {k['id']} ({k['text']!r}) can't be observed mechanically, so the agent's branch choice is a judgment call.",
                k["span"], [k["id"]])

    # Instructions that can only be graded by a judge usually lack a concrete done-criterion.
    judged = [c for c in doc["claims"] if c["check"]["type"] == "judge" and c["kind"] in ("step", "artifact")]
    if judged:
        add("info", "no-done-criterion",
            f"{len(judged)} instructions can only be graded by a judge ({', '.join(c['id'] for c in judged)}). "
            "A concrete done-criterion (a file, a command, an exact string) would make them mechanically checkable.",
            judged[0]["span"], [c["id"] for c in judged])

    if doc.get("classification") == "procedural" and not any(c["after"] for c in doc["claims"]):
        add("info", "no-ordering", "Procedural skill with no ordering requirements extracted.")

    # Text-level checks
    if skill_md is None and skill_dir is not None:
        skill_md = (Path(skill_dir) / "SKILL.md").read_text()
    if skill_md is not None:
        lines = skill_md.splitlines()
        if skill_dir is not None:
            for ln, line in enumerate(lines, 1):
                for ref in _BUNDLE_REF.findall(line):
                    if not (Path(skill_dir) / ref).exists():
                        add("error", "dangling-file", f"Line {ln} references {ref}, which is not in the skill directory.", [ln, ln])
        covered = set()
        for item in doc["claims"] + doc["conditions"]:
            covered.update(range(item["span"][0], item["span"][1] + 1))
        for a, b in _paragraphs(lines):
            if not covered.intersection(range(a, b + 1)):
                add("info", "no-claims", f"Lines {a}-{b} produced no checkable instruction (context or reference text).", [a, b])

    sev = {"error": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: (sev[f["severity"]], (f["span"] or [10**9])[0]))
    return findings


def to_cypher(doc: dict) -> str:
    """Cypher that recreates the graph in Neo4j (run in Neo4j Browser or cypher-shell)."""
    def lit(v):
        return json.dumps(v, ensure_ascii=False)

    skill = doc["skill"]
    out = [f"MERGE (s:Skill {{name: {lit(skill)}}}) SET s.classification = {lit(doc.get('classification'))};"]
    for k in doc["conditions"]:
        out.append(
            f"MERGE (n:Condition {{skill: {lit(skill)}, id: {lit(k['id'])}}}) "
            f"SET n.text = {lit(k['text'])}, n.line_start = {k['span'][0]}, n.line_end = {k['span'][1]}, "
            f"n.check = {lit(k['check']['type'])};"
        )
    for c in doc["claims"]:
        out.append(
            f"MERGE (n:Claim {{skill: {lit(skill)}, id: {lit(c['id'])}}}) "
            f"SET n.kind = {lit(c['kind'])}, n.text = {lit(c['text'])}, n.line_start = {c['span'][0]}, "
            f"n.line_end = {c['span'][1]}, n.check = {lit(c['check']['type'])}, n.check_spec = {lit(json.dumps(c['check']))};"
        )
        out.append(
            f"MATCH (s:Skill {{name: {lit(skill)}}}), (n:Claim {{skill: {lit(skill)}, id: {lit(c['id'])}}}) MERGE (s)-[:HAS_CLAIM]->(n);"
        )
    for c in doc["claims"]:
        for a in c["after"]:
            out.append(
                f"MATCH (a:Claim {{skill: {lit(skill)}, id: {lit(a)}}}), (b:Claim {{skill: {lit(skill)}, id: {lit(c['id'])}}}) "
                f"MERGE (a)-[:PRECEDES]->(b);"
            )
        if c["when"]:
            out.append(
                f"MATCH (k:Condition {{skill: {lit(skill)}, id: {lit(c['when']['cond'])}}}), (b:Claim {{skill: {lit(skill)}, id: {lit(c['id'])}}}) "
                f"MERGE (k)-[:GATES {{value: {str(c['when']['value']).lower()}}}]->(b);"
            )
    return "\n".join(out) + "\n"


def to_graphml(doc: dict, path: str | Path) -> None:
    g = build_graph(doc)
    for _, data in g.nodes(data=True):
        data["span"] = f"{data['span'][0]}-{data['span'][1]}"
    nx.write_graphml(g, path)
