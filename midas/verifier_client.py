"""
VerifierClient (+ VerificationInputBuilder) — SPEC.md §14.

Thin adapter over verifier/checkpoint_builder.py (Phase 1). The accumulated-context
assembly (VerificationInputBuilder) lives inside build_checkpoint, so this class maps
its result into the §17 attempt status and the §19 compile.json shape.
"""
from __future__ import annotations
import os, sys
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
from verifier.checkpoint_builder import build_checkpoint, compile_file, CheckpointResult  # noqa: E402
from .models import CompileJson, CheckReport, Diagnostic  # noqa: E402
from .structure import StructureResult  # noqa: E402


def _to_diags(err_dicts) -> List[Diagnostic]:
    return [Diagnostic(**{k: d.get(k, "") for k in
                          ("file", "line", "col", "severity", "code", "message")})
            for d in err_dicts]


def _report(check) -> CheckReport:
    return CheckReport(status=check.status, errors=_to_diags(check.errors))


class VerifierClient:
    """Verifies a candidate in accumulated context; produces a CheckpointResult + compile.json."""

    def check(self, prelude: List[str], context: str, accepted_declarations: List[str],
              candidate_declaration: str, candidate_body: str) -> CheckpointResult:
        return build_checkpoint(prelude, context, accepted_declarations,
                                candidate_declaration, candidate_body)

    def compile_full_file(self, path: str):
        """Independent compile of a reconstructed full file (§14 final check / §4 input checks)."""
        return compile_file(path)


def build_compile_json(attempt_status: str, structure: StructureResult,
                       cp: CheckpointResult = None,
                       final_check: CheckReport = None) -> CompileJson:
    """Assemble the §19 compile.json from the structure result + checkpoint result."""
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
