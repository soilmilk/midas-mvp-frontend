#!/usr/bin/env python3
"""
Offline end-to-end loop test (no API). Canned reasoning + translation queues drive
run_problem() through the full §18 control flow, INCLUDING a deliberate first-attempt
translation failure (bad output) to exercise the retry path, then to final_success.
Verifies the §6 artifact tree, state.json, and status transitions.
"""
import os, sys, shutil, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from midas.loop import run_problem
from midas.artifacts import StateManager

PROB = os.path.join(ROOT, "problems", "toy")
RUNS = os.path.join(ROOT, ".test_runs")
shutil.rmtree(RUNS, ignore_errors=True)

def T(decls, body):
    return f"reasoning...\n\nNEW DECLARATIONS:\n```lean4\n{decls}\n```\n\nUPDATED THEOREM BODY:\n\n```\n{body}\n```\n"

reasoning = [
    "NEXT STEP:\nShow A = 5.\n\nPROOF:\nA is 2+3 which evaluates to 5.",
    "NEXT STEP:\nShow B = 5.\n\nPROOF:\nB is 15/3 which evaluates to 5.",
    "NEXT STEP:\nCombine to finish.\n\nPROOF:\nRewrite with A=5 and B=5.",
]
translation = [
    "here is my answer without the required sections",                              # step1 attempt1: format_failed
    T("-- A evaluates to 5.\ntheorem A_eq : A = 5 := by decide",
      "theorem main : f A = f B := by\n  have hA : A = 5 := A_eq\n  sorry"),         # step1 attempt2: accepted OPEN
    T("-- B evaluates to 5.\ntheorem B_eq : B = 5 := by decide",
      "theorem main : f A = f B := by\n  have hA : A = 5 := A_eq\n  have hB : B = 5 := B_eq\n  sorry"),  # step2: OPEN
    T("-- Combine.\ntheorem key : f A = f B := by rw [A_eq, B_eq]",
      "theorem main : f A = f B := by\n  exact key"),                               # step3: final_success
]

state = run_problem(PROB, runs_root=RUNS,
                    reasoning_offline=list(reasoning), translation_offline=list(translation))

root = os.path.join(RUNS, "toy")
checks = []
checks.append(("final status == final_success", state.status == "final_success"))
checks.append(("accepted proof steps == 3", state.stats.accepted_proof_steps == 3))
checks.append(("state.json written", os.path.exists(os.path.join(root, "state.json"))))
checks.append(("final/solution.lean written", os.path.exists(os.path.join(root, "final", "solution.lean"))))
# §6 layout: step1 has la001 (format_failed) and la002 (accepted)
la1 = os.path.join(root, "artifacts", "proof_steps", "ps001", "ic001", "la001", "compile.json")
la2 = os.path.join(root, "artifacts", "proof_steps", "ps001", "ic001", "la002", "compile.json")
checks.append(("ps001 has la001 + la002", os.path.exists(la1) and os.path.exists(la2)))
if os.path.exists(la1):
    cj = json.load(open(la1))
    checks.append(("la001 attempt_status == format_failed", cj["attempt_status"] == "format_failed"))
checks.append(("accepted/ps001..003 present",
               all(os.path.exists(os.path.join(root, "accepted", f"ps{n:03d}", "body.lean")) for n in (1, 2, 3))))
# reload state.json and re-verify invariant: exactly one accepted attempt per accepted step
st = StateManager.load(root)
inv = all(sum(1 for c in s.informal_candidates if c.status == "accepted") == 1
          for s in st.proof_steps if s.status in ("accepted", "final_success"))
checks.append(("invariant: 1 accepted candidate per accepted step", inv))

print(f"{'check':<50}result")
print("-" * 62)
ok_all = True
for label, ok in checks:
    ok_all &= ok
    print(f"{label:<50}{'PASS' if ok else 'FAIL'}")
print("-" * 62)
print(f"llm_calls={state.stats.total_llm_calls} lean_compiles={state.stats.total_lean_compiles} "
      f"lean_attempts={state.stats.total_lean_attempts} runtime={state.stats.runtime_seconds:.2f}s")
print(f"\nOFFLINE LOOP: {'PASS' if ok_all else 'FAIL'}")
shutil.rmtree(RUNS, ignore_errors=True)
sys.exit(0 if ok_all else 1)
