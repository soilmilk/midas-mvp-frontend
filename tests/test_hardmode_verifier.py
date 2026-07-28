#!/usr/bin/env python3
"""Phase 1 Hard Mode source-order and verifier-contract gate."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from midas.reconstructor import reconstruct, reconstruct_hard, render_source
from midas.verifier_client import VerifierClient
from midas.warm_backend import WarmTxnBackend

FIX = ROOT / "tests" / "fixtures" / "hard_mode"


def read(name):
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

rows = []


def check(label, ok, detail=""):
    rows.append((label, bool(ok), detail))


# Exact renderer and source-region checks.
easy = render_source([], CONTEXT, [], theorem_body=BODY)
expected_easy = "\n" + CONTEXT.strip() + "\n\n" + BODY.strip() + "\n"
check("exact Easy source preserved", easy.text == expected_easy, repr(easy.text))
check("Easy reconstruct delegates to renderer",
      reconstruct([], CONTEXT, [], BODY) == easy.text)
check("Easy regions are exact",
      [(r.name, r.start_line, r.end_line) for r in easy.regions] ==
      [("context", 2, 2), ("theorem_body", 4, 5)], repr(easy.regions))

hard_exploration = render_source(
    [], CONTEXT, [VALID_DECL], placeholder=PLACEHOLDER, theorem_body=BODY)
expected_exploration = (
    "\n" + CONTEXT.strip() + "\n\n" + VALID_DECL.strip() + "\n\n" +
    PLACEHOLDER.strip() + "\n\n" + BODY.strip() + "\n")
check("exact Hard exploration source order",
      hard_exploration.text == expected_exploration)
check("Hard reconstruct delegates to renderer",
      reconstruct_hard([], CONTEXT, [VALID_DECL], PLACEHOLDER, BODY) ==
      hard_exploration.text)
check("Hard exploration regions are exact",
      [(r.name, r.start_line, r.end_line) for r in hard_exploration.regions] == [
          ("context", 2, 2),
          ("accepted_declarations", 4, 5),
          ("placeholder", 7, 8),
          ("theorem_body", 10, 11),
      ], repr(hard_exploration.regions))

hard_final = render_source(
    ["set_option autoImplicit false"], CONTEXT, [VALID_DECL],
    candidate_declarations=FINAL_DECL,
    placeholder=FILLED,
    theorem_body=FINAL_BODY,
)
check("Hard final source order",
      hard_final.text.index(VALID_DECL.strip()) <
      hard_final.text.index(FINAL_DECL.strip()) <
      hard_final.text.index(FILLED.strip()) <
      hard_final.text.index(FINAL_BODY.strip()))
check("Hard final region labels",
      [r.name for r in hard_final.regions] == [
          "prelude", "context", "accepted_declarations",
          "candidate_declarations", "placeholder", "theorem_body"])

empty_candidate = render_source(
    [], CONTEXT, [VALID_DECL], candidate_declarations=" \n",
    placeholder=PLACEHOLDER, theorem_body=BODY)
check("empty candidate declaration region omitted",
      "candidate_declarations" not in [r.name for r in empty_candidate.regions])

two_accepted = render_source(
    [], CONTEXT, [VALID_DECL, FINAL_DECL], placeholder=FILLED,
    theorem_body=FINAL_BODY)
check("multiple accepted blocks keep order and spans",
      [r.name for r in two_accepted.regions].count("accepted_declarations") == 2 and
      two_accepted.text.index(VALID_DECL.strip()) < two_accepted.text.index(FINAL_DECL.strip()))


# Fresh backend contract.
verifier = VerifierClient()
easy_cp = verifier.check([], CONTEXT, [], "", BODY)
check("original Easy checkpoint remains open and accepted",
      easy_cp.accepted and easy_cp.contains_sorry is True)

exploration = verifier.check(
    [], CONTEXT, [], VALID_DECL, BODY, placeholder=PLACEHOLDER)
check("unresolved Hard exploration is accepted",
      exploration.accepted and exploration.contains_sorry is True)

bad_declaration = verifier.check(
    [], CONTEXT, [], INVALID_DECL, BODY, placeholder=PLACEHOLDER)
check("declaration cannot refer to later placeholder",
      bad_declaration.declaration_check.status == "failed")
check("failed declaration short-circuits suffix",
      bad_declaration.body_check.status == "not_run" and
      bad_declaration.contains_sorry is None)

recovery = verifier.check(
    [], CONTEXT, [], VALID_DECL, BODY, placeholder=PLACEHOLDER)
check("rejected declaration does not leak",
      recovery.accepted and recovery.declaration_check.status == "passed")

closed = verifier.check(
    [], CONTEXT, [], FINAL_DECL, FINAL_BODY,
    placeholder=FILLED, require_closed=True)
check("valid Hard final transaction closes",
      closed.accepted and closed.contains_sorry is False)

bad_final_body = verifier.check(
    [], CONTEXT, [], "", INVALID_FINAL_BODY,
    placeholder=FILLED, require_closed=True)
check("valid placeholder plus invalid theorem fails",
      bad_final_body.declaration_check.passed and
      bad_final_body.body_check.status == "failed")

bad_filled = verifier.check(
    [], CONTEXT, [], "", FINAL_BODY,
    placeholder=INVALID_FILLED, require_closed=True)
check("invalid filled placeholder fails suffix",
      bad_filled.declaration_check.passed and bad_filled.body_check.status == "failed")

open_placeholder_closed_required = verifier.check(
    [], CONTEXT, [], "", FINAL_BODY,
    placeholder=PLACEHOLDER, require_closed=True)
check("closed check rejects sorry in placeholder",
      open_placeholder_closed_required.body_check.status == "failed" and
      open_placeholder_closed_required.contains_sorry is True)

open_theorem_closed_required = verifier.check(
    [], CONTEXT, [], "", BODY,
    placeholder=FILLED, require_closed=True)
check("closed check rejects sorry in theorem",
      open_theorem_closed_required.body_check.status == "failed" and
      open_theorem_closed_required.contains_sorry is True)

closed_body_nonfinal = verifier.check(
    [], "def IsCorrectAnswer (n : Nat) : Prop := True\n", [], "",
    "theorem main : IsCorrectAnswer answer := by\n  trivial\n",
    placeholder=PLACEHOLDER, require_closed=False)
check("non-final closed theorem body is not structurally required open",
      closed_body_nonfinal.accepted and closed_body_nonfinal.contains_sorry is True)


# Deterministic warm-wire contract without requiring a Mathlib process.
class FakeWarm(WarmTxnBackend):
    def __init__(self, responses):
        self.responses = list(responses)
        self.blocks = []

    def _submit(self, block):
        self.blocks.append(block)
        return self.responses.pop(0)


fake_open = FakeWarm(["ACCEPT", "OPEN"])
warm_open = fake_open.check(
    ["import Mathlib", "set_option autoImplicit false"],
    CONTEXT, [], VALID_DECL, BODY, placeholder=PLACEHOLDER)
check("warm exploration accepts OPEN",
      warm_open.accepted and warm_open.contains_sorry is True)
check("warm declaration block excludes placeholder",
      PLACEHOLDER.strip() not in fake_open.blocks[0])
check("warm suffix orders declaration before placeholder before theorem",
      fake_open.blocks[1].index(VALID_DECL.strip()) <
      fake_open.blocks[1].index(PLACEHOLDER.strip()) <
      fake_open.blocks[1].index(BODY.strip()))
check("warm renderer strips resident import only",
      "import Mathlib" not in fake_open.blocks[0] and
      "set_option autoImplicit false" in fake_open.blocks[0])

fake_closed_open = FakeWarm(["ACCEPT", "OPEN"])
warm_closed_open = fake_closed_open.check(
    ["import Mathlib"], CONTEXT, [], "", BODY,
    placeholder=FILLED, require_closed=True)
check("warm closed check rejects OPEN",
      warm_closed_open.body_check.status == "failed" and
      warm_closed_open.contains_sorry is True)

fake_closed = FakeWarm(["ACCEPT", "ACCEPT"])
warm_closed = fake_closed.check(
    ["import Mathlib"], CONTEXT, [], FINAL_DECL, FINAL_BODY,
    placeholder=FILLED, require_closed=True)
check("warm closed check accepts ACCEPT", warm_closed.accepted)

print(f"{'check':<62}result  detail")
print("-" * 100)
all_ok = True
for label, ok, detail in rows:
    all_ok &= ok
    print(f"{label:<62}{'PASS' if ok else 'FAIL':<8}{detail}")
print("-" * 100)
print(f"\nHARD MODE VERIFIER: {'PASS' if all_ok else 'FAIL'}")
sys.exit(0 if all_ok else 1)
