"""`skillgraph dashboard`: browse run sets in a local web app, or export them as a static site.

The server binds to 127.0.0.1 and only reads: it serves the files in static/ and JSON built
from the run sets named on the command line. It never runs agents or calls a model, so
opening it costs nothing. Responses are cached until a file they depend on changes. The
product spec is docs/dashboard-prd.md; the look comes from design/DESIGN.md (OpenDesigner),
whose CSS export is static/tokens.css.

API (GET only):
    /api/sets                      the run sets: name, model, counts, what they contain
    /api/set/<id>                  report data (claims carry every run's result), summary, run index
    /api/run/<id>?path=<p>         one run: trace, check, digest, turn of each event, claim texts
    /api/diff/<a>/<b>              what changed between two run sets
    /report/<id>                   the HTML report (claim graph and reference tree)
"""

from __future__ import annotations

import json
import re
import shutil
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

STATIC = Path(__file__).parent / "static"
STATIC_FILES = {"index.html": "text/html", "core.js": "text/javascript", "pages.js": "text/javascript",
                "trace.js": "text/javascript", "app.js": "text/javascript", "app.css": "text/css",
                "tokens.css": "text/css", "favicon.svg": "image/svg+xml"}
RUN_PATH = re.compile(r"^[\w.@+-]+/[\w.@+-]+/[\w.@+-]+/r\d+$")


def _slug(name: str) -> str:
    return re.sub(r"[^\w.-]+", "-", name).strip("-") or "runs"


class RunSets:
    """The run sets the dashboard shows, by a short id taken from the folder name."""

    def __init__(self, paths: list[str | Path]):
        self.paths: dict[str, Path] = {}
        self._cache: dict[tuple, tuple] = {}
        self._lock = threading.Lock()
        for p in paths:
            p = Path(p).expanduser().resolve()
            if not (p / "runset.json").exists():
                raise FileNotFoundError(f"{p} has no runset.json; pass directories written by `skillgraph run` or `import`")
            base, n = _slug(p.name), 1
            sid = base
            while sid in self.paths:
                n += 1
                sid = f"{base}-{n}"
            self.paths[sid] = p

    def get(self, sid: str) -> Path:
        if sid not in self.paths:
            raise KeyError(f"no run set {sid!r}")
        return self.paths[sid]

    # ------------------------------------------------------------ data

    # ------------------------------------------------------------ cache

    def _files(self, out: Path) -> list[Path]:
        files = [out / "runset.json"]
        for pattern in ("*/*/*/r*/trace.json", "*/*/*/r*/check.json", "*/*/*/r*/digest.json"):
            files.extend(out.glob(pattern))
        try:
            from skillgraph.runner import load_runset

            for info in load_runset(out).get("skills", {}).values():
                for key in ("claims", "reference", "dir"):
                    if info.get(key):
                        files.append(Path(info[key]) if key != "dir" else Path(info[key]) / "SKILL.md")
                if info.get("claims"):
                    stem = Path(info["claims"])
                    name = stem.name.removesuffix(".claims.json")
                    files += [stem.with_name(f"{name}.reference.claims.json"), stem.with_name(f"{name}.coverage.json")]
        except (FileNotFoundError, ValueError):
            pass
        return files

    def signature(self, sid: str) -> tuple:
        """What a run set's responses depend on: every trace, check, digest and claims file, by mtime and size."""
        sig = []
        for f in sorted(set(self._files(self.get(sid)))):
            try:
                st = f.stat()
                sig.append((str(f), st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append((str(f), None, None))
        return tuple(sig)

    def _cached(self, key: tuple, deps: tuple, compute):
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] == deps:
                return hit[1]
        value = compute()
        with self._lock:
            self._cache[key] = (deps, value)
        return value

    # ------------------------------------------------------------ data

    def sets(self) -> list[dict]:
        return [self._cached(("sets", sid), self.signature(sid), lambda sid=sid: self._set_row(sid)) for sid in self.paths]

    def _set_row(self, sid: str) -> dict:
        from datetime import datetime, timezone

        from skillgraph.runner import load_runset

        p = self.get(sid)
        meta = load_runset(p)
        traces = sorted(p.glob("*/*/*/r*/trace.json"))
        rows = [json.loads(t.read_text()) for t in traces]
        mtimes = [f.stat().st_mtime for t in traces for f in (t, t.with_name("check.json")) if f.exists()]
        return {"id": sid, "name": p.name, "model": meta.get("model"), "trigger": meta.get("trigger"),
                "skills": list(meta.get("skills", {})), "runs": len(traces),
                "checked": sum((t.parent / "check.json").exists() for t in traces),
                "tasks": len({(r["skill"], r["task"]) for r in rows}),
                "has_reference": any(info.get("reference") for info in meta.get("skills", {}).values()),
                "has_baseline": any(r["variant"]["name"] == "baseline" for r in rows),
                "imported": any((r.get("result") or {}).get("subtype") == "imported" for r in rows),
                "updated": datetime.fromtimestamp(max(mtimes), timezone.utc).isoformat(timespec="seconds") if mtimes else None}

    def set(self, sid: str) -> dict:
        return self._cached(("set", sid), self.signature(sid), lambda: self._set(sid))

    def _set(self, sid: str) -> dict:
        from skillgraph.report import build_data
        from skillgraph.summary import summarize

        out = self.get(sid)
        try:
            data = build_data(out)
            summary = summarize(data)
            error = None
        except (FileNotFoundError, KeyError, ValueError) as exc:  # e.g. claims files moved
            data, summary, error = {"runset": out.name, "skills": []}, {"skills": []}, str(exc)
        return {"id": sid, "data": data, "summary": summary, "runs": self.runs(sid), "error": error}

    def runs(self, sid: str) -> list[dict]:
        from skillgraph.checker import render_trace
        from skillgraph.digest import approx_tokens, trace_sha

        out = self.get(sid)
        rows = []
        for tp in sorted(out.glob("*/*/*/r*/trace.json")):
            rd = tp.parent
            trace = json.loads(tp.read_text())
            check = json.loads((rd / "check.json").read_text()) if (rd / "check.json").exists() else None
            dg = json.loads((rd / "digest.json").read_text()) if (rd / "digest.json").exists() else None
            if dg and dg.get("trace_sha") != trace_sha(trace):
                dg = None  # built from an older trace
            counts: dict[str, int] = {}
            failed = []
            for cid, r in (check or {}).get("claims", {}).items():
                counts[r["status"]] = counts.get(r["status"], 0) + 1
                if r["status"] in ("missed", "violated", "out_of_order"):
                    failed.append(cid)
            ref_counts: dict[str, int] = {}
            for r in ((check or {}).get("reference") or {}).get("claims", {}).values():
                ref_counts[r["status"]] = ref_counts.get(r["status"], 0) + 1
            res = trace.get("result") or {}
            win = (check or {}).get("window") or {}
            stats = (dg or {}).get("stats") or {}
            prompt = " ".join(str(trace.get("task_prompt", "")).split())
            rows.append({
                "path": rd.relative_to(out).as_posix(),
                "skill": trace["skill"], "task": trace["task"], "variant": trace["variant"]["name"], "rep": trace["rep"],
                "model": trace.get("model"),
                "prompt": prompt if len(prompt) <= 160 else prompt[:159] + "…",
                "success": trace.get("success") or {},
                "all_success": all(trace["success"].values()) if trace.get("success") else None,
                "skill_loaded": trace.get("skill_loaded"),
                "error": trace.get("error"),
                "imported": res.get("subtype") == "imported",
                "events": len(trace["events"]),
                "tool_calls": sum(e["kind"] == "tool" for e in trace["events"]),
                "messages": sum(e["kind"] == "text" for e in trace["events"]),
                "turns": stats.get("turns"),
                "tokens": stats.get("tokens_full") or approx_tokens(render_trace(trace)),
                "wall_s": trace.get("wall_s"),
                "duration_ms": res.get("duration_ms"),
                "blocked": len(trace.get("blocked") or []),
                "cost_usd": res.get("cost_usd"),
                "judge_cost_usd": (check or {}).get("judge_cost_usd"),
                "checked": check is not None,
                "statuses": counts,
                "failed": failed,
                "unclear": counts.get("unclear", 0),
                "reference": ref_counts,
                "judge_mode": (check or {}).get("judge_mode", "full") if check else None,
                "tokens_sent": win.get("tokens_sent"),
                "episode": trace.get("episode"),
                "task_intent": ((dg or {}).get("intents") or {}).get("data", {}).get("task"),
            })
        return rows

    def run(self, sid: str, path: str) -> dict:
        if not RUN_PATH.match(path or "") or any(part in (".", "..") for part in path.split("/")):
            raise ValueError("run path must look like <skill>/<task>/<variant>/r<N>")
        out = self.get(sid)
        rd = (out / path).resolve()
        if out not in rd.parents or not (rd / "trace.json").exists():
            raise KeyError(f"no run {path!r} in {sid}")
        return self._cached(("run", sid, path), self.signature(sid), lambda: self._run(out, rd, path))

    def _run(self, out: Path, rd: Path, path: str) -> dict:
        from skillgraph import digest as digests
        from skillgraph.runner import load_runset
        from skillgraph.schema import load_claims

        trace = json.loads((rd / "trace.json").read_text())
        check = json.loads((rd / "check.json").read_text()) if (rd / "check.json").exists() else None
        dg = digests.load_or_build(rd / "digest.json", trace)
        dg.pop("routes", None)
        turn_of_event = [None] * len(trace["events"])
        for t in dg["turns"]:
            for i in range(t["start"], t["end"] + 1):
                if 0 <= i < len(turn_of_event):
                    turn_of_event[i] = t["n"]
        claims, conditions, ref = {}, {}, {}
        info = (load_runset(out).get("skills") or {}).get(trace["skill"]) or {}
        cp = Path(info.get("claims", ""))
        if info.get("claims") and cp.exists():
            d = load_claims(cp)
            claims = {c["id"]: {k: c.get(k) for k in ("kind", "text", "span", "check", "after", "when")} for c in d["claims"]}
            conditions = {k["id"]: {"text": k["text"], "span": k["span"]} for k in d["conditions"]}
            rp = cp.with_name(f"{trace['skill']}.reference.claims.json")
            if info.get("reference") and rp.exists():
                rd_doc = load_claims(rp)
                ref = {c["id"]: {"kind": c.get("kind", "condition"), "text": c["text"]} for c in rd_doc["claims"] + rd_doc["conditions"]}
        return {"path": path, "trace": trace, "check": check, "digest": dg, "turn_of_event": turn_of_event,
                "claims": claims, "conditions": conditions, "reference": ref}

    def diff(self, a: str, b: str) -> dict:
        from skillgraph.diff import compare

        return self._cached(("diff", a, b), (self.signature(a), self.signature(b)),
                            lambda: compare(self.set(a)["data"], self.set(b)["data"]))

    def report(self, sid: str) -> str:
        from skillgraph.report import render

        return self._cached(("report", sid), self.signature(sid), lambda: render(self.set(sid)["data"]))


# ---------------------------------------------------------------- server

def _json_bytes(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")


def make_handler(sets: RunSets, port_ref: dict):
    allowed_hosts = lambda: {f"127.0.0.1:{port_ref['port']}", f"localhost:{port_ref['port']}",  # noqa: E731
                             f"[::1]:{port_ref['port']}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "skillgraph-dashboard"

        def log_message(self, fmt, *args):  # quiet; errors still reach the client
            pass

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8" if ctype.startswith(("text", "application/json")) else ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, message: str) -> None:
            self._send(status, _json_bytes({"error": message}), "application/json")

        def do_GET(self):  # noqa: N802
            # Refuse other Host names, so a web page can't reach this server through DNS rebinding.
            if self.headers.get("Host") not in allowed_hosts():
                return self._error(HTTPStatus.FORBIDDEN, "unexpected Host header")
            url = urlparse(self.path)
            parts = [unquote(p) for p in url.path.split("/") if p]
            try:
                if not parts:
                    return self._static("index.html")
                if parts[0] == "static" and len(parts) == 2:
                    return self._static(parts[1])
                if parts[0] == "report" and len(parts) == 2:
                    return self._send(200, sets.report(parts[1]).encode("utf-8"), "text/html")
                if parts[0] != "api":
                    return self._error(HTTPStatus.NOT_FOUND, "not found")
                if parts[1:] == ["sets"]:
                    data = sets.sets()
                elif len(parts) == 3 and parts[1] == "set":
                    data = sets.set(parts[2])
                elif len(parts) == 3 and parts[1] == "run":
                    data = sets.run(parts[2], (parse_qs(url.query).get("path") or [""])[0])
                elif len(parts) == 4 and parts[1] == "diff":
                    data = sets.diff(parts[2], parts[3])
                else:
                    return self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
                return self._send(200, _json_bytes(data), "application/json")
            except KeyError as exc:
                return self._error(HTTPStatus.NOT_FOUND, str(exc).strip("'\""))
            except ValueError as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))

        def _static(self, name: str) -> None:
            if name not in STATIC_FILES:
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            csp = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                   "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self._send(200, (STATIC / name).read_bytes(), STATIC_FILES[name], {"Content-Security-Policy": csp})

    return Handler


class _Server(ThreadingHTTPServer):
    # A page load opens several connections at once; the default backlog of 5 makes macOS reset some.
    request_queue_size = 128
    daemon_threads = True


def serve(sets: RunSets, *, port: int = 8765, open_browser: bool = True, ready: threading.Event | None = None,
          server_ref: dict | None = None) -> None:
    port_ref = {"port": port}
    httpd = _Server(("127.0.0.1", port), make_handler(sets, port_ref))
    port_ref["port"] = httpd.server_address[1]
    url = f"http://127.0.0.1:{port_ref['port']}/"
    if server_ref is not None:
        server_ref.update(server=httpd, url=url)
    print(f"skillgraph dashboard on {url}  ({len(sets.paths)} run set{'s' if len(sets.paths) != 1 else ''}; Ctrl-C to stop)",
          flush=True)
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    if ready:
        ready.set()
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


# ---------------------------------------------------------------- static export

def export(sets: RunSets, dest: Path) -> Path:
    """Write a static copy: index.html, assets, data.js (every API response) and one HTML
    report per run set. Opens from disk or any static host; nothing is fetched at runtime."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    bundle: dict[str, object] = {"/api/sets": sets.sets()}
    for sid in sets.paths:
        data = sets.set(sid)
        bundle[f"/api/set/{sid}"] = data
        for row in data["runs"]:
            bundle[f"/api/run/{sid}?path={row['path']}"] = sets.run(sid, row["path"])
        if data["data"]["skills"]:
            (dest / f"report-{sid}.html").write_text(sets.report(sid))
    ids = list(sets.paths)
    for a in ids:
        for b in ids:
            if a != b:
                try:
                    bundle[f"/api/diff/{a}/{b}"] = sets.diff(a, b)
                except (FileNotFoundError, KeyError, ValueError):
                    pass
    payload = json.dumps(bundle, ensure_ascii=False, default=str).replace("<", "\\u003c")
    (dest / "data.js").write_text(f"window.SKILLGRAPH_DATA = {payload};\n")
    for name in STATIC_FILES:
        if name != "index.html" and (STATIC / name).exists():
            shutil.copyfile(STATIC / name, dest / name)
    index = (STATIC / "index.html").read_text()
    index = index.replace('href="/static/', 'href="').replace('src="/static/', 'src="')
    index = index.replace('<script src="core.js"', '<script src="data.js"></script>\n  <script src="core.js"', 1)
    (dest / "index.html").write_text(index)
    return dest / "index.html"
