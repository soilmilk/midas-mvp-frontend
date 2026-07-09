"""
checkpoint_builder.py — the verification backend for the midas-mvp proof-search loop.

Implements SPEC.md §14's checkpoint model with a fresh `lean` subprocess per check
(deliberate for the MVP; a warm/persistent backend is an out-of-scope future swap).

    build_checkpoint(prelude, context, accepted_declarations, candidate_declaration,
                     candidate_body) -> CheckpointResult

  DECLARATION CHECK : prelude + context + accepted_declarations + candidate_declaration
                      must compile with NO `sorry`.
  BODY CHECK        : + candidate_body, `sorry` ALLOWED.
                      Run ONLY if the declaration check passes — a rejected declaration
                      must never reach the body check.

Diagnostics are parsed against this Lean 4.31 output format (confirmed, not guessed):
  - `file:line:col: error(<code>): <message>` — the `(code)` (e.g. `lean.unknownIdentifier`)
    is parsed into its own field so failure classification can bucket by code, not prose.
  - the sorry warning uses backticks: `` declaration uses `sorry` ``.
Per-diagnostic schema (SPEC.md §19): { file, line, col, severity, code, message }.
"""
from __future__ import annotations
import os, re, shutil, subprocess, time
from dataclasses import dataclass, field, asdict
from typing import Optional

LEAN = shutil.which("lean") or os.path.expanduser("~/.elan/bin/lean")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root
WORK = os.path.join(REPO, ".work")

# file:line:col: severity[(code)]: message   (message may continue on following lines)
_DIAG = re.compile(
    r"^(?P<file>[^:\n]*):(?P<line>\d+):(?P<col>\d+): "
    r"(?P<sev>error|warning)(?:\((?P<code>[^)]*)\))?: (?P<msg>.*)$")
_SORRY = re.compile(r"declaration uses [`']sorry[`']")


@dataclass
class Diag:
    file: str
    line: int
    col: int
    severity: str
    code: str
    message: str


@dataclass
class CompileResult:
    ok: bool                 # returncode 0 AND no error-severity diagnostics
    returncode: int
    diagnostics: list        # list[Diag]
    contains_sorry: bool
    raw: str
    ms: float

    @property
    def errors(self):
        return [d for d in self.diagnostics if d.severity == "error"]


@dataclass
class CheckResult:
    status: str              # "passed" | "failed" | "not_run"
    errors: list = field(default_factory=list)   # list[Diag-as-dict], SPEC §19 schema

    @property
    def passed(self):
        return self.status == "passed"


@dataclass
class CheckpointResult:
    declaration_check: CheckResult
    body_check: CheckResult
    contains_sorry: Optional[bool]     # None if body check not run
    decl_ms: float
    body_ms: float
    wall_ms: float
    declaration_raw: str = ""          # raw verifier output (for compile.json.raw_verifier_output)
    body_raw: str = ""

    @property
    def accepted(self) -> bool:
        return self.declaration_check.passed and self.body_check.passed


def _parse(out: str, tmp_basename: str) -> list:
    diags, cur = [], None
    for line in out.splitlines():
        m = _DIAG.match(line)
        if m:
            if cur:
                diags.append(cur)
            f = os.path.basename(m["file"]) or m["file"]
            cur = Diag(f, int(m["line"]), int(m["col"]), m["sev"], m["code"] or "", m["msg"])
        elif cur is not None:
            cur.message += "\n" + line
    if cur:
        diags.append(cur)
    return diags


def _compile(source: str, tag: str, prelude_lines_count: int) -> CompileResult:
    os.makedirs(WORK, exist_ok=True)
    path = os.path.join(WORK, f"{tag}.lean")
    with open(path, "w") as fh:
        fh.write(source)
    t0 = time.perf_counter()
    proc = subprocess.run([LEAN, path], capture_output=True, text=True, cwd=REPO)
    ms = (time.perf_counter() - t0) * 1000
    out = (proc.stdout or "") + (proc.stderr or "")
    diags = _parse(out, os.path.basename(path))
    contains_sorry = _SORRY.search(out) is not None
    ok = proc.returncode == 0 and not any(d.severity == "error" for d in diags)
    return CompileResult(ok, proc.returncode, diags, contains_sorry, out, ms)


def _assemble(prelude, decls, tail=None) -> str:
    parts = []
    for line in (prelude or []):
        parts.append(line)
    if prelude:
        parts.append("")
    for d in decls:
        if d and d.strip():
            parts.append(d.rstrip())
            parts.append("")
    if tail and tail.strip():
        parts.append(tail.rstrip())
    return "\n".join(parts).rstrip() + "\n"


def _errs(cr: CompileResult) -> list:
    return [asdict(d) for d in cr.diagnostics if d.severity == "error"]


def build_checkpoint(prelude, context, accepted_declarations,
                     candidate_declaration, candidate_body) -> CheckpointResult:
    """
    prelude: list of full Lean lines (SPEC §2 lean_prelude); [] for core/std.
    context: contents of input/context.lean (immutable base). Always included.
    accepted_declarations: list of previously-accepted declarations.lean contents.
    candidate_declaration / candidate_body: the attempt under test (declarations may be empty).
    """
    t0 = time.perf_counter()
    decls = [context] + list(accepted_declarations)
    n_pre = len(prelude or [])

    # 1. DECLARATION CHECK — no sorry allowed.
    decl_src = _assemble(prelude, decls + ([candidate_declaration] if candidate_declaration else []))
    dr = _compile(decl_src, "check_declarations", n_pre)
    decl_pass = dr.ok and not dr.contains_sorry
    declaration_check = CheckResult("passed" if decl_pass else "failed", _errs(dr))
    if decl_pass and dr.contains_sorry:  # (unreachable given decl_pass, kept explicit)
        declaration_check.status = "failed"

    # short-circuit: a rejected declaration never reaches the body check
    if not decl_pass:
        wall = (time.perf_counter() - t0) * 1000
        return CheckpointResult(declaration_check, CheckResult("not_run"), None,
                                dr.ms, 0.0, wall, dr.raw, "")

    # 2. BODY CHECK — sorry allowed.
    body_src = _assemble(prelude, decls + ([candidate_declaration] if candidate_declaration else []),
                         tail=candidate_body)
    br = _compile(body_src, "check_body", n_pre)
    body_check = CheckResult("passed" if br.ok else "failed", _errs(br))
    wall = (time.perf_counter() - t0) * 1000
    return CheckpointResult(declaration_check, body_check, br.contains_sorry,
                            dr.ms, br.ms, wall, dr.raw, br.raw)


def compile_file(path: str) -> CompileResult:
    """Compile an existing full .lean file independently (SPEC §14 final check / §4 input checks)."""
    return _compile(open(path).read(), "check_" + os.path.splitext(os.path.basename(path))[0], 0)
