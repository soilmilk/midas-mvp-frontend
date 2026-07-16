#!/usr/bin/env python3
"""
Phase 2 offline validation — the deterministic spine of §18 with NO LLM calls.
Canned translator outputs (what §10 says Claude must produce) are driven through
OutputParser → StructureChecker → VerifierClient → Reconstructor → independent
final compile, reproducing the toy proof to final_success. Also exercises the
parser-failure path and the defensive `:= by` header check.
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from midas.parser import parse_translator_output
from midas.structure import (extract_header, check_structure, body_contains_sorry,
                             HeaderError, declared_names)
from midas.verifier_client import VerifierClient, build_compile_json
from midas.reconstructor import reconstruct
from midas.models import CheckReport, Diagnostic
from midas.agents import TranslationAgent, TranslationRepairContext
from midas.loop import _submitted_source, _render_repair_diagnostics
from midas.warm_backend import _warm_diags

def _names(decl_list):
    out = []
    for d in decl_list:
        out += declared_names(d)
    return out

FIX = os.path.join(ROOT, "verifier", "phase1_fixtures")
def R(n): return open(os.path.join(FIX, n)).read()

PRELUDE = []
context = R("context.lean")
body_initial = R("body_initial.lean")

# ---- canned translator outputs (§10 format: prose + NEW DECLARATIONS + UPDATED THEOREM BODY) ----
STEP1 = """Reasoning: first evaluate the recursive object A.

NEW DECLARATIONS:
```lean4
-- A evaluates to 5.
theorem A_eq : A = 5 := by decide
```

UPDATED THEOREM BODY:

```
theorem main : f A = f B := by
  have hA : A = 5 := A_eq
  sorry
```
"""
STEP2 = """Now the closed-form object B.

NEW DECLARATIONS:
```lean4
-- B evaluates to 5.
theorem B_eq : B = 5 := by decide
```

UPDATED THEOREM BODY:

```
theorem main : f A = f B := by
  have hA : A = 5 := A_eq
  have hB : B = 5 := B_eq
  sorry
```
"""
STEP3 = """Combine the two and finish.

NEW DECLARATIONS:
```lean4
-- Combine A_eq and B_eq into the target equality.
theorem key : f A = f B := by rw [A_eq, B_eq]
```

UPDATED THEOREM BODY:

```
theorem main : f A = f B := by
  exact key
```
"""
MALFORMED = "Here is my reasoning but I forgot the required sections entirely."

vc = VerifierClient()
rows = []
def row(label, ok, detail): rows.append((label, ok, detail))

# ---- defensive header check (user tweak #3) ----
header = extract_header(body_initial)
row("header extracted", header == "theorem main : f A = f B := by", repr(header))
try:
    extract_header("theorem oops : True :=\n  trivial\n")   # no ':= by'
    row("defensive: no ':= by' fails loudly", False, "did NOT raise")
except HeaderError as e:
    row("defensive: no ':= by' fails loudly", True, str(e).split(" — ")[0])

# ---- parser-failure path (top-priority finding surface) ----
pf = parse_translator_output(MALFORMED)
row("malformed output -> format_failed", (not pf.ok), pf.error)

# ---- strict empty-declarations contract ----
_BODY = "```lean4\ntheorem main : True := by trivial\n```"
_EMPTY_DECLARATIONS = f"""NEW DECLARATIONS:
```lean4
```

UPDATED THEOREM BODY:
{_BODY}
"""
_EMPTY_UNLABELED_DECLARATIONS = f"""NEW DECLARATIONS:
```
```

UPDATED THEOREM BODY:
{_BODY}
"""
_NONE_DECLARATIONS = f"""NEW DECLARATIONS:
(none)

UPDATED THEOREM BODY:
{_BODY}
"""
_MISSING_DECLARATIONS_FENCE = f"""NEW DECLARATIONS:

UPDATED THEOREM BODY:
{_BODY}
"""
_EXTRA_DECLARATIONS_TEXT = f"""NEW DECLARATIONS:
No declarations are needed.
```lean4
```

UPDATED THEOREM BODY:
{_BODY}
"""

empty_decls = parse_translator_output(_EMPTY_DECLARATIONS)
row("empty lean4 declarations fence parses", empty_decls.ok and empty_decls.declarations == "", empty_decls.error)
empty_unlabeled = parse_translator_output(_EMPTY_UNLABELED_DECLARATIONS)
row("empty unlabeled declarations fence parses", empty_unlabeled.ok and empty_unlabeled.declarations == "", empty_unlabeled.error)
for label, raw in [
    ("(none) declarations rejected", _NONE_DECLARATIONS),
    ("missing declarations fence rejected", _MISSING_DECLARATIONS_FENCE),
    ("extra declarations prose rejected", _EXTRA_DECLARATIONS_TEXT),
]:
    parsed = parse_translator_output(raw)
    row(label, not parsed.ok, parsed.error)

# ---- translator repair context: full output + every located diagnostic ----
repair_decls = "theorem broken : A = 5 := by\n  exact missing_one"
repair_body = "theorem main : f A = f B := by\n  exact missing_two"
submitted = _submitted_source(PRELUDE, context, [], repair_decls, repair_body, warm=False)
decl_line = submitted[:submitted.index("missing_one")].count("\n") + 1
body_line = submitted[:submitted.index("missing_two")].count("\n") + 1
repair_errors = [
    Diagnostic(file="check.lean", line=decl_line, col=8, code="lean.unknownIdentifier",
               message="Unknown identifier missing_one"),
    Diagnostic(file="check.lean", line=body_line, col=8,
               message="Unknown identifier missing_two"),
]
located = _render_repair_diagnostics(repair_errors, submitted, repair_decls, repair_body)
previous_raw = ("INTERMEDIATE REASONING:\nold reasoning\n\nNEW DECLARATIONS:\n```lean4\n"
                + repair_decls + "\n```\n\nUPDATED THEOREM BODY:\n```lean4\n"
                + repair_body + "\n```")
repair = TranslationRepairContext("body_check", previous_raw, repair_decls, repair_body, located)
agent = TranslationAgent("offline", "test considerations", offline_responses=[])
repair_prompt = agent.build_prompt(header, "problem", PRELUDE, context, [], body_initial,
                                   "NEXT STEP: repair", repair_context=repair)
row("repair prompt contains complete raw output", previous_raw in repair_prompt, "raw prior response")
row("repair prompt does not duplicate parsed declarations",
    "### Parsed NEW DECLARATIONS" not in repair_prompt and repair_prompt.count(repair_decls) == 1,
    "declaration appears only in raw output")
row("repair prompt does not duplicate parsed body",
    "### Parsed UPDATED THEOREM BODY" not in repair_prompt and repair_prompt.count(repair_body) == 1,
    "body appears only in raw output")
row("repair prompt contains every diagnostic",
    "Error 1" in repair_prompt and "Error 2" in repair_prompt and
    "missing_one" in repair_prompt and "missing_two" in repair_prompt, "two errors")
row("repair prompt maps declaration location",
    "Source region: **rejected NEW DECLARATIONS**" in repair_prompt, "declaration region")
row("repair prompt maps body location",
    "Source region: **rejected UPDATED THEOREM BODY**" in repair_prompt, "body region")
row("repair prompt requires complete replacement",
    "do not return a diff or patch" in repair_prompt, "repair contract")

warm_errors = _warm_diags(
    "REJECT :: <req>:20:10: error: first failure\ncontinued detail\n"
    "<req>:24:2: error(lean.test): second failure")
row("warm parser preserves all diagnostics", len(warm_errors) == 2, repr(warm_errors))
row("warm parser preserves multiline detail",
    warm_errors[0]["message"] == "first failure\ncontinued detail", warm_errors[0]["message"])
row("warm parser preserves diagnostic code", warm_errors[1]["code"] == "lean.test", warm_errors[1]["code"])

# ---- run the toy through the deterministic spine ----
accepted_decls = []
final_status = "running"
for i, raw in enumerate([STEP1, STEP2, STEP3], start=1):
    pr = parse_translator_output(raw)
    assert pr.ok, f"step{i} parse failed: {pr.error}"
    sr = check_structure(pr.declarations, pr.body, header,
                         previous_accepted_names=_names(accepted_decls) if (i > 1) else [])
    if not sr.ok:
        row(f"step{i} structure", False, "; ".join(sr.violations)); continue
    cp = vc.check(PRELUDE, context, accepted_decls, pr.declarations, pr.body)
    if not cp.accepted:
        cj = build_compile_json("lemma_failed" if not cp.declaration_check.passed else "body_failed", sr, cp)
        row(f"step{i} verify", False, f"decl={cp.declaration_check.status} body={cp.body_check.status}")
        continue
    if body_contains_sorry(pr.body):
        accepted_decls.append(pr.declarations)
        row(f"step{i} accept (OPEN)", True,
            f"decl+body pass, sorry=True, +{','.join(_names([pr.declarations]))}")
    else:
        # final step: accept decl, reconstruct, compile independently (§14 hard rule)
        accepted_decls.append(pr.declarations)
        full = reconstruct(PRELUDE, context, accepted_decls, pr.body)
        final_path = os.path.join(ROOT, "tests", "_final_solution.lean")
        open(final_path, "w").write(full)
        fr = vc.compile_full_file(final_path)
        final_ok = fr.ok and not fr.contains_sorry
        os.remove(final_path)
        final_status = "final_success" if final_ok else "final_reconstruction_failed"
        row(f"step{i} FINAL (reconstruct+compile)", final_ok,
            f"independent compile ok={fr.ok}, sorry={fr.contains_sorry} -> {final_status}")

print(f"{'check':<42}{'result':<8}detail")
print("-" * 92)
all_ok = True
for label, ok, detail in rows:
    all_ok &= ok
    print(f"{label:<42}{'PASS' if ok else 'FAIL':<8}{detail}")
print("-" * 92)
print(f"final ProofRunState.status = {final_status}")
print(f"\nPHASE 2 OFFLINE SPINE: {'PASS' if (all_ok and final_status == 'final_success') else 'FAIL'}")
sys.exit(0 if (all_ok and final_status == "final_success") else 1)
