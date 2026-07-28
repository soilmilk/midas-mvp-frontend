#!/usr/bin/env python3
"""Optional real-process parity gate for the fresh and warm verifier backends.

The gate is deterministic and makes no model or network calls.  It exits
successfully with an explicit SKIP when the local warm executable or Mathlib
LEAN_PATH is unavailable; all other startup and verdict mismatches are errors.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from midas.models import Config
from midas.reconstructor import render_source
from midas.verifier_client import FreshCompileBackend
from midas.warm_backend import WarmTxnBackend


FIX = ROOT / "tests" / "fixtures" / "hard_mode"


def read(name: str) -> str:
    return (FIX / name).read_text()


CONTEXT = read("context.lean")
PLACEHOLDER = read("placeholder.lean")
BODY = read("body_initial.lean")
VALID_DECL = read("valid_intermediate_declaration.lean")
INVALID_DECL = read("invalid_declaration_refers_to_placeholder.lean")
FILLED = read("valid_filled_placeholder.lean")
INVALID_FILLED = read("invalid_filled_placeholder.lean")
FINAL_BODY = read("valid_final_theorem.lean")
INVALID_FINAL_BODY = read("invalid_final_theorem.lean")
FINAL_DECL = read("optional_final_declaration.lean")


@dataclass(frozen=True)
class Case:
    name: str
    declarations: str
    placeholder: str
    body: str
    require_closed: bool
    expected: tuple
    diagnostic_region: Optional[str] = None


CASES = [
    Case(
        "exploration with both holes open",
        VALID_DECL,
        PLACEHOLDER,
        BODY,
        False,
        ("passed", "passed", True, True),
    ),
    Case(
        "declaration referring to later placeholder",
        INVALID_DECL,
        PLACEHOLDER,
        BODY,
        False,
        ("failed", "not_run", None, False),
        "candidate_declarations",
    ),
    Case(
        "invalid filled placeholder",
        "",
        INVALID_FILLED,
        FINAL_BODY,
        True,
        ("passed", "failed", False, False),
        "placeholder",
    ),
    Case(
        "valid placeholder with invalid theorem",
        "",
        FILLED,
        INVALID_FINAL_BODY,
        True,
        ("passed", "failed", False, False),
        "theorem_body",
    ),
    Case(
        "completely valid final transaction",
        FINAL_DECL,
        FILLED,
        FINAL_BODY,
        True,
        ("passed", "passed", False, True),
    ),
    Case(
        "sorry remaining in closed-required source",
        "",
        FILLED,
        BODY,
        True,
        ("passed", "failed", True, False),
    ),
]


def verdict(result) -> tuple:
    return (
        result.declaration_check.status,
        result.body_check.status,
        result.contains_sorry,
        result.accepted,
    )


def diagnostic_region(case: Case, result) -> Optional[str]:
    if result.declaration_check.errors:
        rendered = render_source(
            [], CONTEXT, [], candidate_declarations=case.declarations
        )
        error = result.declaration_check.errors[0]
    elif result.body_check.errors:
        rendered = render_source(
            [],
            CONTEXT,
            [],
            candidate_declarations=case.declarations,
            placeholder=case.placeholder,
            theorem_body=case.body,
        )
        error = result.body_check.errors[0]
    else:
        return None

    line = int(error.get("line") or 0)
    for region in rendered.regions:
        if region.start_line <= line <= region.end_line:
            return region.name
    return "stable_prefix"


def warm_config() -> Config:
    return Config(
        verifier_backend="warm",
        lean_prelude=[],
        warm_binary=os.environ.get("MIDAS_WARM_BINARY", ""),
        warm_lean_path=os.environ.get("MIDAS_WARM_LEAN_PATH", ""),
    )


rows = []


def check(label: str, condition: bool, detail: str = "") -> None:
    rows.append((label, bool(condition), detail))


fresh = FreshCompileBackend()
try:
    warm = WarmTxnBackend(warm_config())
except RuntimeError as error:
    message = str(error)
    if (
        "could not find a built warm executable" in message
        or "needs a Mathlib LEAN_PATH" in message
    ):
        print(f"HARD MODE BACKEND PARITY: SKIP — {message}")
        sys.exit(0)
    raise

try:
    for case in CASES:
        fresh_result = fresh.check(
            [],
            CONTEXT,
            [],
            case.declarations,
            case.body,
            placeholder=case.placeholder,
            require_closed=case.require_closed,
        )
        warm_result = warm.check(
            [],
            CONTEXT,
            [],
            case.declarations,
            case.body,
            placeholder=case.placeholder,
            require_closed=case.require_closed,
        )

        fresh_verdict = verdict(fresh_result)
        warm_verdict = verdict(warm_result)
        check(
            f"{case.name}: expected verdict",
            fresh_verdict == case.expected,
            f"fresh={fresh_verdict!r} expected={case.expected!r}",
        )
        check(
            f"{case.name}: fresh/warm verdict parity",
            warm_verdict == fresh_verdict,
            f"fresh={fresh_verdict!r} warm={warm_verdict!r}",
        )

        fresh_region = diagnostic_region(case, fresh_result)
        warm_region = diagnostic_region(case, warm_result)
        check(
            f"{case.name}: expected diagnostic region",
            fresh_region == case.diagnostic_region,
            f"fresh={fresh_region!r} expected={case.diagnostic_region!r}",
        )
        check(
            f"{case.name}: fresh/warm diagnostic parity",
            warm_region == fresh_region,
            f"fresh={fresh_region!r} warm={warm_region!r}",
        )
finally:
    warm.close()


print(f"{'check':<78}{'result':<8}detail")
print("-" * 128)
all_ok = True
for label, ok, detail in rows:
    all_ok &= ok
    print(f"{label:<78}{'PASS' if ok else 'FAIL':<8}{detail}")
print("-" * 128)
print(f"\nHARD MODE BACKEND PARITY: {'PASS' if all_ok else 'FAIL'}")
sys.exit(0 if all_ok else 1)
