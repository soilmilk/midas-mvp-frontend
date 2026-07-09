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
from midas.models import CheckReport

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
