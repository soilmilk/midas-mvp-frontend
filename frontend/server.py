#!/usr/bin/env python3
"""
Read-only local bridge for the Midas Prover frontend.

It does NOT modify (or import) the `midas` package. It only:
  * invokes `python -m midas.cli run <problem>` as a subprocess (the existing CLI), and
  * reads the artifacts the loop writes under runs/<problem>/ (state.json, accepted/, final/).

Plus one extra GPT call: it summarizes the accepted steps into a plain-English paragraph
for the Progress panel, so the granular internal steps are never surfaced.

Run:  OPENROUTER_API_KEY=... ./.venv/bin/python frontend/server.py   (then open http://localhost:8000)
"""
import json, os, glob, re, subprocess, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROBLEMS = os.path.join(ROOT, "problems")
RUNS = os.path.join(ROOT, "runs")
PYTHON = os.path.join(ROOT, ".venv", "bin", "python")
if not os.path.exists(PYTHON):
    PYTHON = sys.executable

_procs = {}            # problem_id -> Popen
_started = {}          # problem_id -> wall-clock start of the current run
_summary_cache = {}    # (problem, n_steps, status) -> paragraph


def _is_mathlib(cfg):
    return cfg.get("verifier_backend") == "warm" or any("Mathlib" in l for l in cfg.get("lean_prelude", []))


def list_problems():
    out = []
    for d in sorted(glob.glob(os.path.join(PROBLEMS, "*"))):
        ip = os.path.join(d, "input", "informal_problem.md")
        cfgp = os.path.join(d, "config.json")
        if os.path.isfile(ip) and os.path.isfile(cfgp):
            pid = os.path.basename(d)
            out.append({"id": pid, "statement": open(ip).read().strip(),
                        "solved": os.path.isfile(os.path.join(RUNS, pid, "final", "solution.lean")),
                        "mathlib": _is_mathlib(json.load(open(cfgp)))})
    return out


_caps = None
def capabilities():
    """What THIS server can verify. Cached after first computation. Actually tests Mathlib
    when a LEAN_PATH is present (no warm exe); trusts the warm exe's presence otherwise."""
    global _caps
    if _caps is not None:
        return _caps
    lean_ver = ""
    try:
        lean_ver = subprocess.run(["lean", "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        pass
    warm = os.environ.get("MIDAS_WARM_BINARY", "")
    warm_ok = bool(warm and os.path.exists(warm))
    lean_path = os.environ.get("LEAN_PATH", "") or os.environ.get("MIDAS_WARM_LEAN_PATH", "")
    mathlib = warm_ok
    if not mathlib and lean_path:
        try:
            import tempfile
            d = tempfile.mkdtemp()
            f = os.path.join(d, "t.lean")
            open(f, "w").write("import Mathlib\n")
            env = dict(os.environ); env["LEAN_PATH"] = lean_path
            mathlib = subprocess.run(["lean", f], capture_output=True, timeout=180, env=env, cwd=d).returncode == 0
        except Exception:
            mathlib = False
    _caps = {"lean": bool(lean_ver), "leanVersion": lean_ver, "warm": warm_ok, "mathlib": bool(mathlib),
             "note": ("This server can verify Mathlib proofs." if mathlib
                      else "This server is not configured to verify Mathlib proofs — set MIDAS_WARM_BINARY + MIDAS_WARM_LEAN_PATH (see INTEGRATION.md).")}
    return _caps


def _read_state(problem):
    p = os.path.join(RUNS, problem, "state.json")
    if not os.path.isfile(p):
        return None
    try:
        return json.load(open(p))
    except Exception:
        return None  # caught mid-write; caller retries on next poll


def _assemble_lean(problem):
    rd = os.path.join(RUNS, problem)
    final = os.path.join(rd, "final", "solution.lean")
    if os.path.isfile(final):
        return open(final).read().strip()
    cfg = json.load(open(os.path.join(PROBLEMS, problem, "config.json")))
    prelude = cfg.get("lean_prelude", [])
    ctx = os.path.join(rd, "input", "context.lean")
    context = open(ctx).read().strip() if os.path.isfile(ctx) else ""
    decls, body = [], ""
    for s in sorted(glob.glob(os.path.join(rd, "accepted", "proof_step_*"))):
        dp, bp = os.path.join(s, "declarations.lean"), os.path.join(s, "body.lean")
        if os.path.isfile(dp) and open(dp).read().strip():
            decls.append(open(dp).read().strip())
        if os.path.isfile(bp) and open(bp).read().strip():
            body = open(bp).read().strip()
    if not body:
        bi = os.path.join(rd, "input", "body_initial.lean")
        body = open(bi).read().strip() if os.path.isfile(bi) else ""
    parts = list(prelude) + ([context] if context else []) + decls + ([body] if body else [])
    return "\n\n".join(parts).strip()


def _gpt_summary(problem, state):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
        cfg = json.load(open(os.path.join(PROBLEMS, problem, "config.json")))
        model = cfg.get("reasoning_model", "openai/gpt-5")
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key, timeout=45, max_retries=1)
        steps = "\n".join(f"- {k}" for k in state.get("current_knowledge", []))
        done = state.get("status") == "final_success"
        prompt = (
            "You are writing a short progress note for a user watching an automated theorem prover work.\n"
            "Below are the proof steps established so far. Write ONE short paragraph (2-4 sentences) in plain "
            "English for a mathematician, summarizing where the proof stands"
            + (" — the proof is now COMPLETE." if done else " and what remains.") +
            " Do NOT enumerate or list the individual steps and do NOT expose the step-by-step mechanics; "
            "give a high-level sense of progress only.\n\nSteps so far:\n" + steps
        )
        r = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}],
            max_tokens=8000, reasoning_effort="low")
        return (r.choices[0].message.content or "").strip() or None
    except Exception as e:
        sys.stderr.write(f"[summary] {type(e).__name__}: {e}\n")
        return None


def _summary(problem, state):
    know = state.get("current_knowledge", [])
    ck = (problem, len(know), state.get("status"))
    if ck in _summary_cache:
        return _summary_cache[ck]
    if not know:
        txt = "Getting started — analyzing the problem and planning the opening move."
    else:
        txt = _gpt_summary(problem, state) or ("Progress so far: " + " ".join(know))
    _summary_cache[ck] = txt
    return txt


def _goal(problem, state):
    hdr = (state or {}).get("formal_theorem_header", "")
    if not hdr:
        for bi in (os.path.join(RUNS, problem, "input", "body_initial.lean"),
                   os.path.join(PROBLEMS, problem, "input", "body_initial.lean")):
            if os.path.isfile(bi):
                m = re.search(r"(?m)^\s*(?:theorem|lemma)\b.*", open(bi).read())
                hdr = m.group(0) if m else ""
                break
    m = re.search(r":\s*(.*?)\s*:=", hdr)
    return m.group(1).strip() if m else problem


def build_view(problem):
    cfg = json.load(open(os.path.join(PROBLEMS, problem, "config.json")))
    running = problem in _procs and _procs[problem].poll() is None
    statement = open(os.path.join(PROBLEMS, problem, "input", "informal_problem.md")).read().strip()
    state = _read_state(problem)
    maxs = cfg.get("max_proof_steps", 1)
    budget = cfg.get("max_runtime_seconds", 0)
    if running and problem in _started:
        elapsed = time.time() - _started[problem]
    elif state:
        elapsed = state.get("stats", {}).get("runtime_seconds", 0) or 0
    else:
        elapsed = 0
    is_ml = _is_mathlib(cfg)
    base = {"problem": problem, "statement": statement, "maxSteps": maxs, "requiresMathlib": is_ml,
            "elapsed": round(elapsed, 1), "budget": budget}
    if not state:
        empty = {"goal": _goal(problem, None), "percent": 0, "steps": 0, "knowledge": [], "lean": "", "stats": {}}
        if running:
            # subprocess launched but no state.json yet
            if is_ml:
                base.update(empty, status="loading", running=True,
                            progress="Loading Mathlib into the verifier — a one-time step (~15–40 s). "
                                     "Proof steps begin once the library is resident.")
            else:
                base.update(empty, status="starting", running=True, progress="Starting up…")
            return base
        if problem in _started:  # started this session, died before writing state
            tail = ""
            lg = os.path.join(RUNS, problem, "_frontend_run.log")
            if os.path.isfile(lg):
                tail = "".join(open(lg).read().splitlines(True)[-6:]).strip()
            base.update(empty, status="error", running=False,
                        progress="The run stopped before producing a proof.\n\n" + (tail or "(no log output)"))
            return base
        msg = "Idle — press Run to start proving."
        if is_ml and not capabilities()["mathlib"]:
            msg = ("This problem requires Mathlib, which this server isn't configured to verify yet. "
                   "Running it will fail until Mathlib is set up on the server (see the footer status).")
        base.update(empty, status="idle", running=False, progress=msg)
        return base
    stats = state.get("stats", {})
    status = state.get("status", "running")
    accepted = stats.get("accepted_proof_steps", 0)
    pct = 100 if status == "final_success" else (min(96, round(100 * accepted / max(1, maxs))) if running or accepted else 0)
    base.update({"goal": _goal(problem, state), "status": status, "running": running,
                 "percent": pct, "steps": accepted,
                 "knowledge": state.get("current_knowledge", []),
                 "lean": _assemble_lean(problem), "progress": _summary(problem, state),
                 "stats": stats})
    return base


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            self._send(200, open(os.path.join(HERE, "index.html"), "rb").read(), "text/html; charset=utf-8")
        elif u.path == "/api/problems":
            self._send(200, json.dumps(list_problems()))
        elif u.path == "/api/capabilities":
            self._send(200, json.dumps(capabilities()))
        elif u.path == "/api/state":
            prob = q.get("problem", [None])[0]
            if not prob or not os.path.isdir(os.path.join(PROBLEMS, prob)):
                self._send(400, json.dumps({"error": "unknown problem"}))
            else:
                self._send(200, json.dumps(build_view(prob)))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/run":
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(n) or "{}")
            prob = body.get("problem")
            pdir = os.path.join(PROBLEMS, prob or "")
            if not prob or not os.path.isdir(pdir):
                self._send(400, json.dumps({"error": "unknown problem"}))
                return
            if prob in _procs and _procs[prob].poll() is None:
                self._send(200, json.dumps({"ok": True, "already": True}))
                return
            for k in [kk for kk in _summary_cache if kk[0] == prob]:
                _summary_cache.pop(k, None)
            rd = os.path.join(RUNS, prob)
            os.makedirs(rd, exist_ok=True)
            log = open(os.path.join(rd, "_frontend_run.log"), "w")
            _started[prob] = time.time()
            _procs[prob] = subprocess.Popen(
                [PYTHON, "-m", "midas.cli", "run", pdir], cwd=ROOT,
                stdout=log, stderr=subprocess.STDOUT, env=os.environ.copy())
            self._send(200, json.dumps({"ok": True}))
        elif u.path == "/api/upload":
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(n) or "{}")
            pid = (body.get("id") or "").strip()
            informal = (body.get("informal") or "").strip()
            btxt = (body.get("body") or "").strip()
            if not re.match(r"^[A-Za-z0-9_-]{1,40}$", pid):
                self._send(400, json.dumps({"error": "Name must be 1-40 chars: letters, digits, _ or -."})); return
            if os.path.exists(os.path.join(PROBLEMS, pid)):
                self._send(400, json.dumps({"error": f"A problem named '{pid}' already exists."})); return
            if not informal or not btxt:
                self._send(400, json.dumps({"error": "Statement and initial theorem are both required."})); return
            mathlib = bool(body.get("mathlib"))
            effort = body.get("effort") if body.get("effort") in ("minimal", "low", "medium", "high") else ("high" if mathlib else "low")
            try:
                budget = max(60, int(body.get("budget") or (3600 if mathlib else 900)))
            except (TypeError, ValueError):
                budget = 3600 if mathlib else 900
            inp = os.path.join(PROBLEMS, pid, "input")
            os.makedirs(inp)
            open(os.path.join(inp, "informal_problem.md"), "w").write(informal + "\n")
            open(os.path.join(inp, "context.lean"), "w").write((body.get("context") or "").strip() + "\n")
            open(os.path.join(inp, "body_initial.lean"), "w").write(btxt + "\n")
            cfg = {"max_proof_steps": 40 if mathlib else 12,
                   "max_informal_candidates_per_proof_step": 3,
                   "max_lean_translation_attempts_per_candidate": 3,
                   "max_total_lean_attempts": 300 if mathlib else 80,
                   "max_runtime_seconds": budget,
                   "lean_prelude": (["import Mathlib", "import Aesop", "set_option maxHeartbeats 0",
                                     "open BigOperators Real Nat Topology Rat"] if mathlib else []),
                   "reasoning_model": "openai/gpt-5", "translation_model": "anthropic/claude-sonnet-5",
                   "reasoning_effort": effort}
            if mathlib:
                cfg["verifier_backend"] = "warm"
            json.dump(cfg, open(os.path.join(PROBLEMS, pid, "config.json"), "w"), indent=2)
            self._send(200, json.dumps({"ok": True, "id": pid, "mathlib": mathlib}))
        else:
            self._send(404, json.dumps({"error": "not found"}))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    key = "set" if os.environ.get("OPENROUTER_API_KEY") else "MISSING (Progress summary + runs will fail)"
    print(f"Midas Prover  →  http://127.0.0.1:{port}   (OPENROUTER_API_KEY: {key})")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
