#!/usr/bin/env python3
"""
Offline end-to-end loop test (no API). Canned reasoning + translation queues drive
run_problem() through the full §18 control flow, INCLUDING a candidate whose every
translation fails, to exercise failed-step feedback, then to final_success.
Verifies the §6 artifact tree, state.json, and status transitions.
"""
import os, sys, shutil, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from midas.loop import run_problem
from midas.parser import parse_reasoning_action
from midas.artifacts import StateManager

PROB = os.path.join(ROOT, "problems", "toy")
RUNS = os.path.join(ROOT, ".test_runs")
shutil.rmtree(RUNS, ignore_errors=True)

def T(decls, body):
    return f"reasoning...\n\nNEW DECLARATIONS:\n```lean4\n{decls}\n```\n\nUPDATED THEOREM BODY:\n\n```\n{body}\n```\n"

reasoning = [
    "",                                                                            # candidate1: empty reasoning output
    "NEXT STEP:\nShow both of these facts:\nA = 5 and B = 5.\n\nPROOF:\nEvaluate both expressions.\n\nIS_FINAL_STEP: False",
    "NEXT STEP:\nShow A = 5.\n\nPROOF:\nA is 2+3 which evaluates to 5.\n\nIS_FINAL_STEP: False",
    "NEXT STEP:\nShow B = 5.\n\nPROOF:\nB is 15/3 which evaluates to 5.\n\nIS_FINAL_STEP: False",
    "NEXT STEP:\nCombine to finish.\n\nPROOF:\nRewrite with A=5 and B=5.\n\nIS_FINAL_STEP: True",
]
translation = [
    T("theorem broken : A = 5 := by exact missing_identifier",
      "theorem main : f A = f B := by\n  sorry"),                         # compiler failure, then retry
    "unparseable attempt two",
    "unparseable attempt three",
    T("-- A evaluates to 5.\ntheorem A_eq : A = 5 := by decide",
      "theorem main : f A = f B := by\n  have hA : A = 5 := A_eq\n  sorry"),         # candidate2: accepted OPEN
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
multiline_candidate = """INTERMEDIATE REASONING:
irrelevant

NEXT STEP:
First line of the proposition.
Second line of the proposition.

PROOF:
The proof.

IS_FINAL_STEP: False
"""
checks.append(("missing NEXT STEP is rejected",
               not parse_reasoning_action("only reasoning", "easy").ok))
multiline_action = parse_reasoning_action(multiline_candidate, "easy")
checks.append(("multiline NEXT STEP is retained",
               multiline_action.ok and multiline_action.next_step ==
               "First line of the proposition.\nSecond line of the proposition."))
# §6 layout: step1 candidate1 is empty, candidate2 is exhausted, candidate3 is accepted.
candidate1_dir = os.path.join(root, "artifacts", "proof_steps", "proof_step_001", "informal_candidate_001")
la1 = os.path.join(root, "artifacts", "proof_steps", "proof_step_001", "informal_candidate_002", "lean4_attempt_001", "compile.json")
la3 = os.path.join(root, "artifacts", "proof_steps", "proof_step_001", "informal_candidate_002", "lean4_attempt_003", "compile.json")
retry_translation_prompt = os.path.join(root, "artifacts", "proof_steps", "proof_step_001",
                                        "informal_candidate_002", "lean4_attempt_002",
                                        "translator_prompt.md")
candidate3_prompt = os.path.join(root, "artifacts", "proof_steps", "proof_step_001", "informal_candidate_003", "reasoning_prompt.md")
checks.append(("empty reasoning candidate skips Lean attempts",
               os.path.exists(os.path.join(candidate1_dir, "informal_step.md")) and
               not os.path.exists(os.path.join(candidate1_dir, "lean4_attempt_001"))))
checks.append(("candidate2 exhausts three Lean attempts", os.path.exists(la1) and os.path.exists(la3)))
if os.path.exists(la1):
    cj = json.load(open(la1))
    checks.append(("lean4_attempt_001 attempt_status == lemma_failed", cj["attempt_status"] == "lemma_failed"))
if os.path.exists(retry_translation_prompt):
    retry_prompt = open(retry_translation_prompt).read()
    checks.append(("translator retry includes rejected declaration",
                   "theorem broken : A = 5 := by exact missing_identifier" in retry_prompt))
    checks.append(("translator retry includes complete rejected body",
                   "theorem main : f A = f B := by\n  sorry" in retry_prompt))
    checks.append(("translator retry includes located compiler error",
                   "Unknown identifier `missing_identifier`" in retry_prompt and
                   "Source region: **rejected NEW DECLARATIONS**" in retry_prompt and
                   "Nearby submitted Lean code" in retry_prompt))
else:
    checks.append(("translator retry prompt written", False))
if os.path.exists(candidate3_prompt):
    prompt = open(candidate3_prompt).read()
    checks.append(("retry prompt includes complete failed NEXT STEP",
                   "Show both of these facts:\nA = 5 and B = 5." in prompt))
    checks.append(("retry prompt excludes failed candidate PROOF",
                   "Evaluate both expressions." not in prompt))
    checks.append(("retry prompt asks not to repeat the step",
                   "Do not repeat it unchanged." in prompt))
else:
    checks.append(("candidate3 reasoning prompt written", False))
checks.append(("accepted/proof_step_001..003 present",
               all(os.path.exists(os.path.join(root, "accepted", f"proof_step_{n:03d}", "body.lean")) for n in (1, 2, 3))))
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
