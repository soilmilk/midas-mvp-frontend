"""
VerifierClient (+ VerificationInputBuilder) — SPEC.md §14, with a PLUGGABLE backend.

The checkpoint semantics (declaration check no-sorry → body check sorry-allowed, run in
accumulated context) are fixed; the *engine* that runs them is swappable:

  FreshCompileBackend  (default) — a fresh `lean` subprocess per checkpoint (Phase 1). Right for
                                   Core/Std, where a cold compile is ~0.3–0.6 s.
  WarmTxnBackend       (opt-in)  — routes to the sibling `midas_proof_verifier` warm server, which
                                   keeps Mathlib resident and pays `import Mathlib` ONCE. Right for a
                                   Mathlib prelude, where a cold compile is ~15–40 s. See
                                   midas/warm_backend.py and INTEGRATION.md.

Both satisfy the same `VerifierBackend` interface and return the same `CheckpointResult`, so the
loop never changes. Select with `config.verifier_backend` ("fresh" | "warm").
"""
from __future__ import annotations
import os, sys
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
from verifier.checkpoint_builder import build_checkpoint, compile_file, CheckpointResult, CompileResult  # noqa: E402
from .models import CompileJson, CheckReport, Diagnostic  # noqa: E402
from .structure import StructureResult  # noqa: E402


# ---------------- backend interface (the midas_proof_verifier link point) ----------------
class VerifierBackend:
    """Interface both backends implement. This is the seam where midas_proof_verifier plugs in."""
    def check(self, prelude: List[str], context: str, accepted_declarations: List[str],
              candidate_declaration: str, candidate_body: str) -> CheckpointResult:
        raise NotImplementedError

    def compile_full_file(self, path: str) -> CompileResult:
        raise NotImplementedError

    def close(self):
        pass


class FreshCompileBackend(VerifierBackend):
    """Default: fresh `lean` per checkpoint (SPEC §14). No Mathlib-load amortization."""
    def check(self, prelude, context, accepted_declarations, candidate_declaration, candidate_body):
        return build_checkpoint(prelude, context, accepted_declarations,
                                candidate_declaration, candidate_body)

    def compile_full_file(self, path):
        return compile_file(path)


class VerifierClient:
    """Verifies a candidate in accumulated context via a pluggable backend (§14)."""
    def __init__(self, backend: Optional[VerifierBackend] = None):
        self.backend = backend or FreshCompileBackend()

    def check(self, prelude, context, accepted_declarations, candidate_declaration, candidate_body):
        return self.backend.check(prelude, context, accepted_declarations,
                                  candidate_declaration, candidate_body)

    def compile_full_file(self, path):
        return self.backend.compile_full_file(path)

    def close(self):
        self.backend.close()


def make_verifier(config) -> VerifierClient:
    """Build the VerifierClient the config asks for. Default 'fresh' keeps the MVP unchanged."""
    kind = getattr(config, "verifier_backend", "fresh")
    if kind == "fresh":
        return VerifierClient(FreshCompileBackend())
    if kind == "warm":
        from .warm_backend import WarmTxnBackend   # imported lazily (needs the warm binary)
        return VerifierClient(WarmTxnBackend(config))
    raise ValueError(f"unknown verifier_backend {kind!r} (expected 'fresh' or 'warm')")


# ---------------- compile.json assembly (§19) ----------------
def _to_diags(err_dicts) -> List[Diagnostic]:
    return [Diagnostic(**{k: d.get(k, "") for k in
                          ("file", "line", "col", "severity", "code", "message")})
            for d in err_dicts]


def _report(check) -> CheckReport:
    return CheckReport(status=check.status, errors=_to_diags(check.errors))


def build_compile_json(attempt_status: str, structure: StructureResult,
                       cp: CheckpointResult = None,
                       final_check: CheckReport = None) -> CompileJson:
    sc = CheckReport(status="passed" if structure.ok else "failed",
                     errors=[Diagnostic(message=v, severity="error") for v in structure.violations])
    cj = CompileJson(attempt_status=attempt_status, structure_check=sc)
    if cp is not None:
        cj.declaration_check = _report(cp.declaration_check)
        cj.body_check = _report(cp.body_check)
        cj.raw_verifier_output = (cp.declaration_raw or "") + (cp.body_raw or "")
    if final_check is not None:
        cj.final_check = final_check
    return cj
