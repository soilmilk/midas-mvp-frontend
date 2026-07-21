"""
WarmTxnBackend — routes midas-mvp's checkpoint checks to the in-repo `warm-server`
executable, which keeps Mathlib resident and pays `import Mathlib` ONCE.

WHY: with a Mathlib prelude, FreshCompileBackend re-pays ~15–40 s per checkpoint. The warm server
loads Mathlib once (~15–40 s) then verifies each block in ~10–500 ms — measured break-even ≈ 2
checkpoints (see INTEGRATION.md for checkpoint-validation timings).

HOW: one long-lived `warm` process; each `check()` submits the assembled block(s) over stdin and
reads the verdict. The `warm` (stateless) verifier gates each block against resident Mathlib, which
matches this backend's stateless `check()` (the loop passes `accepted_declarations` forward itself).

  declaration check : context + accepted + candidate_declaration  → expect ACCEPT
  body check        : + candidate_body                            → ACCEPT (closed) / OPEN (sorry)

VALIDATED (2026-07-09): verdicts match FreshCompileBackend on shared Mathlib cases (sorry body,
partial proof, broken body). To use: build `warm-server/.lake/build/bin/warm`, provide a Mathlib
LEAN_PATH, and set `verifier_backend: "warm"`. Optional hardening: have `warm` print a per-response
sentinel (`%%DONE`) so `_submit()` needn't scan for the next `[node …]` line.
"""
from __future__ import annotations
import os, re, subprocess, time
from dataclasses import asdict
from pathlib import Path

from verifier.checkpoint_builder import CheckpointResult, CheckResult, CompileResult, Diag

_NODE = re.compile(r"\[node \d+\]\s+\d+\s*ms\s+(.*)")
_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_WARM_BINARY = _REPO_ROOT / "warm-server" / ".lake" / "build" / "bin" / "warm"
_DEFAULT_LEAN_PATH_FILE = _REPO_ROOT / "warm-server" / "mathlib_leanpath.txt"


_WARM_DIAG = re.compile(
    r"^(?P<file>[^:\n]+):(?P<line>\d+):(?P<col>\d+): "
    r"(?P<sev>error|warning)(?:\((?P<code>[^)]*)\))?: (?P<msg>.*)$")


def _decode_wire(msg: str) -> str:
    return msg.replace("␤", "\n")


def _warm_diags(msg: str):
    """Parse every Lean diagnostic carried by a warm-server verdict."""
    text = _decode_wire(msg)
    payload = text.split("::", 1)[1].strip() if "::" in text else text.strip()
    diagnostics, current = [], None
    for line in payload.splitlines():
        match = _WARM_DIAG.match(line)
        if match:
            if current is not None:
                diagnostics.append(asdict(current))
            current = Diag(match["file"], int(match["line"]), int(match["col"]),
                           match["sev"], match["code"] or "", match["msg"])
        elif current is not None:
            current.message += "\n" + line
    if current is not None:
        diagnostics.append(asdict(current))
    return diagnostics or [asdict(Diag("<warm>", 0, 0, "error", "", payload))]


class WarmTxnBackend:
    def __init__(self, config):
        binary = config.warm_binary or os.environ.get("MIDAS_WARM_BINARY", "") or str(_DEFAULT_WARM_BINARY)
        lean_path = (config.warm_lean_path or os.environ.get("MIDAS_WARM_LEAN_PATH", "")
                     or os.environ.get("LEAN_PATH", ""))
        if not lean_path and _DEFAULT_LEAN_PATH_FILE.exists():
            lean_path = _DEFAULT_LEAN_PATH_FILE.read_text().strip()
        if not binary or not os.path.exists(binary):
            raise RuntimeError(
                "verifier_backend='warm' could not find a built warm executable. Run "
                "`cd warm-server && lake build warm` from the midas-mvp repo, or set "
                "config.warm_binary / $MIDAS_WARM_BINARY. See INTEGRATION.md.")
        if not lean_path:
            raise RuntimeError(
                "verifier_backend='warm' needs a Mathlib LEAN_PATH via config.warm_lean_path, "
                "$MIDAS_WARM_LEAN_PATH, $LEAN_PATH, or warm-server/mathlib_leanpath.txt. "
                "See INTEGRATION.md.")
        self._lib = "Mathlib" if any("Mathlib" in l for l in config.lean_prelude) else "Mathlib"
        print(f"[warm backend] loading {self._lib} once via {os.path.basename(binary)} …", flush=True)
        env = os.environ.copy()
        if lean_path:
            env["LEAN_PATH"] = lean_path
        self.proc = subprocess.Popen([binary, self._lib, "400000"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True, env=env, bufsize=1)
        # drain startup until the server is ready
        while True:
            line = self.proc.stdout.readline()
            if line == "":
                raise RuntimeError("warm process exited before becoming ready")
            if line.startswith("[ready]"):
                break

    def _submit(self, block: str) -> str:
        """Send one %%-delimited block; return the verdict text from the next `[node …]` line.
        Scanning to the next `[node …]` line naturally skips any trailing goal lines from a prior
        OPEN verdict — hence the sentinel-hardening recommendation in the header."""
        self.proc.stdin.write(block.rstrip() + "\n%%\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if line == "":
                raise RuntimeError("warm process ended mid-request")
            m = _NODE.match(line)
            if m:
                return _decode_wire(m.group(1).strip())

    @staticmethod
    def _strip_imports(prelude):
        return [l for l in prelude if not l.strip().startswith("import ")]

    def check(self, prelude, context, accepted_declarations, candidate_declaration, candidate_body):
        t0 = time.perf_counter()
        pre = self._strip_imports(prelude)
        parts = ([context] + list(accepted_declarations)
                 + ([candidate_declaration] if (candidate_declaration or "").strip() else []))
        decl_block = "\n".join(pre + ["\n\n".join(parts)])

        v1 = self._submit(decl_block)
        if not v1.startswith("ACCEPT"):
            wall = (time.perf_counter() - t0) * 1000
            return CheckpointResult(CheckResult("failed", _warm_diags(v1)), CheckResult("not_run"),
                                    None, 0.0, 0.0, wall, v1, "")

        body_block = decl_block + "\n\n" + (candidate_body or "")
        v2 = self._submit(body_block)
        body_ok = v2.startswith("ACCEPT") or v2.startswith("OPEN")
        contains_sorry = v2.startswith("OPEN")
        body_check = CheckResult("passed" if body_ok else "failed", [] if body_ok else _warm_diags(v2))
        wall = (time.perf_counter() - t0) * 1000
        return CheckpointResult(CheckResult("passed"), body_check, contains_sorry, 0.0, 0.0, wall, v1, v2)

    def compile_full_file(self, path: str) -> CompileResult:
        v = self._submit(open(path).read())
        ok = v.startswith("ACCEPT")
        return CompileResult(ok, 0 if ok else 1,
                             [] if ok else [Diag(**d) for d in _warm_diags(v)],
                             v.startswith("OPEN"), v, 0.0)

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.terminate()
        except Exception:
            pass
