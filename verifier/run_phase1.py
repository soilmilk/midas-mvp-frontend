#!/usr/bin/env python3
"""
Phase 1 gate: validate checkpoint_builder.py in isolation (no LLM).

Runs a short sequence of successful checkpoints plus a REQUIRED negative test on
a hand-authored toy problem, and confirms:
  - successful checkpoints pass declaration + body checks with correct contains_sorry
  - the negative case FAILS declaration_check, and body_check is SKIPPED (not_run)
  - the negative diagnostic is structured per the §19 schema (file/line/col/severity/code/message)
  - nothing from the rejected attempt leaks into the next checkpoint's accepted state
Prints a pass/fail table. Exits nonzero if any expectation is unmet.
"""
import os, sys
from checkpoint_builder import build_checkpoint, Diag

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phase1_fixtures")
def R(name): return open(os.path.join(FIX, name)).read()

PRELUDE = []                        # core/std only, no imports
context = R("context.lean")
body_initial = R("body_initial.lean")

rows = []   # (label, decl, body, sorry, ms, expected, ok)
def add(label, cr, exp_decl, exp_body, exp_sorry, expect_str):
    ok = (cr.declaration_check.status == exp_decl
          and cr.body_check.status == exp_body
          and cr.contains_sorry is exp_sorry)
    rows.append((label, cr.declaration_check.status, cr.body_check.status,
                 str(cr.contains_sorry), cr.wall_ms, expect_str, ok))
    return ok

SCHEMA = {"file", "line", "col", "severity", "code", "message"}
accepted = []          # the loop's accepted-declaration state; only grows on acceptance

# CP0: context + body_initial (sorry allowed) — no candidate declaration
cp0 = build_checkpoint(PRELUDE, context, accepted, "", body_initial)
add("CP0  context+body_initial", cp0, "passed", "passed", True, "decl passed(empty)/body pass/sorry")

# CP1: + lemma about A
cp1 = build_checkpoint(PRELUDE, context, accepted, R("cp1_decl.lean"), R("cp1_body.lean"))
if add("CP1  +A_eq", cp1, "passed", "passed", True, "decl+body pass, sorry=True") and cp1.accepted:
    accepted.append(R("cp1_decl.lean"))

# CP2 NEGATIVE: broken declaration on top of accepted=[cp1]
neg = build_checkpoint(PRELUDE, context, accepted, R("bad_decl.lean"), R("cp2_body.lean"))
neg_errs = neg.declaration_check.errors
structured = bool(neg_errs) and all(set(e.keys()) >= SCHEMA and e.get("code") for e in neg_errs)
neg_ok = (neg.declaration_check.status == "failed"
          and neg.body_check.status == "not_run"
          and neg.contains_sorry is None
          and structured)
rows.append(("CP2* bad_decl (NEGATIVE)", neg.declaration_check.status, neg.body_check.status,
             str(neg.contains_sorry), neg.wall_ms,
             "decl FAIL / body not_run / structured+code", neg_ok))
# leak check: `accepted` must be unchanged (bad_decl never appended)
leak_ok = (accepted == [R("cp1_decl.lean")])

# CP2 correct: + lemma about B (accepted still just [cp1])
cp2 = build_checkpoint(PRELUDE, context, accepted, R("cp2_decl.lean"), R("cp2_body.lean"))
if add("CP2  +B_eq (recovers)", cp2, "passed", "passed", True, "decl+body pass, sorry=True") and cp2.accepted:
    accepted.append(R("cp2_decl.lean"))

# CP3: combine, completes (no sorry)
cp3 = build_checkpoint(PRELUDE, context, accepted, R("cp3_decl.lean"), R("cp3_body.lean"))
add("CP3  +key (completes)", cp3, "passed", "passed", False, "decl+body pass, sorry=False")

# ---------- table ----------
print(f"{'checkpoint':<30}{'decl':<9}{'body':<9}{'sorry':<8}{'ms':>7}   result")
print("-" * 76)
all_ok = True
for label, d, b, s, ms, exp, ok in rows:
    all_ok &= ok
    print(f"{label:<30}{d:<9}{b:<9}{s:<8}{ms:>7.0f}   {'PASS' if ok else 'FAIL'}   (expect: {exp})")
print("-" * 76)
print(f"leak check (rejected decl absent from accepted state): {'PASS' if leak_ok else 'FAIL'}")
all_ok &= leak_ok
print(f"\nPHASE 1 GATE: {'PASS — proceed to Phase 2' if all_ok else 'FAIL — fix the verifier'}")

# ---------- negative-test detail ----------
# Keep successful setup output clean: the negative case intentionally produces
# a Lean error, and the table above is the success signal for that check.
if not all_ok:
    print("\n" + "=" * 76)
    print("NEGATIVE TEST — raw declaration-check output + parsed structured diagnostics:")
    print("=" * 76)
    print(neg.declaration_raw.rstrip())
    print("-" * 76)
    for e in neg.declaration_check.errors:
        print(f"  {{ file:{e['file']!r} line:{e['line']} col:{e['col']} "
              f"severity:{e['severity']!r} code:{e['code']!r} message:{e['message']!r} }}")

sys.exit(0 if all_ok else 1)
