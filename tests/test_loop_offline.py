#!/usr/bin/env python3
"""
Offline end-to-end loop test (no API). Canned reasoning + translation queues drive
run_problem() through the full §18 control flow, INCLUDING a candidate whose every
translation fails, to exercise failed-step feedback, then to final_success.
Verifies the §6 artifact tree, state.json, and status transitions.
"""
import io, os, sys, shutil, json, subprocess
from contextlib import redirect_stdout
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from midas.loop import run_problem
from midas.parser import parse_reasoning_action
from midas.artifacts import RunDirectoryExistsError, RunEventLogger, StateManager

PROB = os.path.join(ROOT, "problems", "toy")
RUNS = os.path.join(ROOT, ".test_runs")
shutil.rmtree(RUNS, ignore_errors=True)
observed = {}


def fail_reasoning_after_inspection(prompt):
    candidate = os.path.join(
        RUNS, "toy", "artifacts", "proof_steps", "proof_step_001",
        "informal_candidate_001",
    )
    state = json.load(open(os.path.join(RUNS, "toy", "state.json")))
    observed["reasoning_prompt_precedes_call"] = (
        open(os.path.join(candidate, "reasoning_prompt.md")).read() == prompt
        and state["proof_steps"][0]["informal_candidates"][0]["status"] == "pending"
    )
    raise RuntimeError("simulated reasoner timeout")


def fail_translation_after_inspection(prompt):
    attempt = os.path.join(
        RUNS, "toy", "artifacts", "proof_steps", "proof_step_001",
        "informal_candidate_003", "lean4_attempt_001",
    )
    state = json.load(open(os.path.join(RUNS, "toy", "state.json")))
    pending = state["proof_steps"][0]["informal_candidates"][2]["lean_translation_attempts"][0]
    observed["translation_prompt_precedes_call"] = (
        open(os.path.join(attempt, "translator_prompt.md")).read() == prompt
        and pending["status"] == "pending"
        and not os.path.exists(os.path.join(attempt, "raw_translator_output.md"))
    )
    raise RuntimeError("simulated translator timeout")

def T(decls, body, final=False):
    heading = "FINAL THEOREM BODY" if final else "UPDATED THEOREM BODY"
    return (f"INTERMEDIATE REASONING:\nTranslate the exact claim.\n\n"
            f"PLAN:\nProve the declaration and update the body.\n\n"
            f"NEW DECLARATIONS:\n```lean4\n{decls}\n```\n\n"
            f"{heading}:\n\n```\n{body}\n```\n")

def R(next_step, proof, final=False, idea="[High] Continue the proof."):
    return (
        f"NEXT STEP:\n{next_step}\n\n"
        f"PROOF:\n{proof}\n\n"
        f"IS_FINAL_STEP: {'True' if final else 'False'}\n\n"
        f"IDEAS FOR THE FUTURE:\n{idea}"
    )

ALIGNED = (
    "INTERMEDIATE REASONING:\nThe compiled transaction proves the requested step.\n\n"
    "VERDICT: ALIGNED\n\nFEEDBACK:\nNone"
)

reasoning = [
    fail_reasoning_after_inspection,                                                # candidate1: failed call
    R("Show both of these facts:\nA = 5 and B = 5.", "Evaluate both expressions.",
      idea="[Low] Failed candidate roadmap must not persist."),
    R("Show A = 5.", "A is 2+3 which evaluates to 5.",
      idea="[High] Prove B = 5 next."),
    R("Assume B = 5 locally.", "Insert the claim as a local fact.",
      idea="[Low] A cheating roadmap must not persist."),
    R("Show B = 5 in one large automation call.",
      "Ask automation to discover the entire proof.",
      idea="[Low] This rejected roadmap must not persist."),
    R("Show B = 5.", "B is 15/3 which evaluates to 5.",
      idea="[High] Combine both equalities."),
    R("Combine to finish.", "Rewrite with A=5 and B=5.", final=True,
      idea="None — the theorem is complete"),
]
translation = [
    T("theorem broken : A = 5 := by exact missing_identifier",
      "theorem main : f A = f B := by\n  sorry"),                         # compiler failure, then retry
    "unparseable attempt two",
    "unparseable attempt three",
    fail_translation_after_inspection,                                     # call failure must still be logged
    T("-- A evaluates to 5.\ntheorem A_eq : A = 5 := by decide",
      "theorem main : f A = f B := by\n  decide"),                                  # candidate2: non-final CLOSED, still exploration
    T("", "theorem main : f A = f B := by\n  have hB : B = 5 := by sorry\n  sorry"),
    ("INTERMEDIATE REASONING:\nThe claim is valid but looks tedious.\n\n"
     "TRANSLATION REJECTED:\nKIND: HARD_TO_FORMALIZE\nREASON:\n"
     "I do not know which automation lemma to use."),
    ("INTERMEDIATE REASONING:\nThe broad claim does not follow as stated.\n\n"
     "TRANSLATION REJECTED:\nKIND: MISSING_ASSUMPTION\nREASON:\n"
     "Use a direct evaluation lemma instead of unsupported broad automation."),
    T("-- B evaluates to 5.\ntheorem B_eq : B = 5 := by decide",
      "theorem main : f A = f B := by\n  decide"),  # step2: remains closed
    T("-- Combine.\ntheorem key : f A = f B := by rw [A_eq, B_eq]",
      "theorem main : f A = f B := by\n  exact key",
      final=True),                                                                  # step3: final_success
]

console_capture = io.StringIO()
with redirect_stdout(console_capture):
    state = run_problem(
        PROB,
        runs_root=RUNS,
        reasoning_offline=list(reasoning),
        translation_offline=list(translation),
        reviewer_offline=[ALIGNED] * 20,
    )
console_output = console_capture.getvalue()

root = os.path.join(RUNS, "toy")
checks = []
checks.append(("final status == final_success", state.status == "final_success"))
checks.append(("accepted proof steps == 3", state.stats.accepted_proof_steps == 3))
checks.append(("non-final closed body does not finish the run",
               len(state.proof_steps) == 3 and
               state.proof_steps[0].status == "accepted"))
checks.append(("state.json written", os.path.exists(os.path.join(root, "state.json"))))
log_path = os.path.join(root, "log.txt")
checks.append(("run log written", os.path.exists(log_path)))
run_log = open(log_path).read()
checks.append(("run log starts at exact zero timestamp",
               run_log.startswith("[00h 00m 00s 000ms] Run started: toy\n")))
checks.append(("run log timestamps have fixed width",
               RunEventLogger._timestamp(0) == "00h 00m 00s 000ms" and
               RunEventLogger._timestamp(192406) == "00h 03m 12s 406ms"))
checks.append(("run log uses hierarchical indentation",
               "]   Proof step 1 started" in run_log and
               "]     Candidate 1 started" in run_log and
               "]       Waiting for reasoner response" in run_log and
               "]         Waiting for translator response" in run_log))
checks.append(("run log records detailed artifact references",
               "Reasoning prompt:" in run_log and
               "Translator response artifact:" in run_log and
               "compile.json" not in console_output))
checks.append(("run log records per-call and total LLM usage",
               "Reasoner usage:" in run_log and
               "Translator usage:" in run_log and
               "cost_credits=unavailable" in run_log and
               "accounted_cost_calls=0/" in run_log))
checks.append(("console announces blocking work and outcomes in order",
               console_output.index("Waiting for reasoner response") <
               console_output.index("Reasoner call failed") <
               console_output.index("Waiting for translator response") <
               console_output.index("Compiling Lean 4 checkpoint") <
               console_output.index("Run finished: status=final_success")))
checks.append(("console keeps parse failures concise",
               "Translator output parse failed; retrying" in console_output and
               "Translator output parse failed:" not in console_output))
log_before_collision = open(log_path).read()
state_before_collision = open(os.path.join(root, "state.json")).read()
collision_error = ""
try:
    run_problem(PROB, runs_root=RUNS, reasoning_offline=[], translation_offline=[],
                reviewer_offline=[])
except RunDirectoryExistsError as error:
    collision_error = str(error)
checks.append(("existing run directory is rejected with rename instruction",
               "Run folder already exists:" in collision_error and
               "Rename the existing run folder" in collision_error))
checks.append(("existing run directory remains untouched",
               open(log_path).read() == log_before_collision and
               open(os.path.join(root, "state.json")).read() == state_before_collision))
cli_collision = subprocess.run(
    [sys.executable, "-m", "midas.cli", "--runs-root", RUNS, "run", "toy"],
    cwd=ROOT,
    text=True,
    capture_output=True,
)
checks.append(("CLI rejects existing run before starting work",
               cli_collision.returncode != 0 and
               "Rename the existing run folder" in cli_collision.stderr and
               "Run started" not in cli_collision.stdout))
checks.append(("initial validation reports written",
               all(os.path.exists(os.path.join(root, "input", name)) for name in
                   ("context_check.json", "initial_body_check.json"))))
checks.append(("final/solution.lean written", os.path.exists(os.path.join(root, "final", "solution.lean"))))
checks.append(("final/body.lean written", os.path.exists(os.path.join(root, "final", "body.lean"))))
multiline_candidate = """INTERMEDIATE REASONING:
irrelevant

NEXT STEP:
First line of the proposition.
Second line of the proposition.

PROOF:
The proof.

IS_FINAL_STEP: False

IDEAS FOR THE FUTURE:
[Medium] Try rewriting with it.
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
failed_call_dir = os.path.join(root, "artifacts", "proof_steps", "proof_step_001",
                               "informal_candidate_003", "lean4_attempt_001")
checks.append(("failed reasoning call skips Lean attempts",
               os.path.exists(os.path.join(candidate1_dir, "informal_step.md")) and
               not os.path.exists(os.path.join(candidate1_dir, "lean4_attempt_001"))))
checks.append(("failed reasoning call preserves prompt and error",
               os.path.getsize(os.path.join(candidate1_dir,
                                            "reasoning_prompt.md")) > 0 and
               "simulated reasoner timeout" in open(os.path.join(
                   candidate1_dir, "reasoning_call_error.txt")).read()))
checks.append(("reasoning prompt and pending state precede call",
               observed.get("reasoning_prompt_precedes_call")))
checks.append(("candidate2 exhausts three Lean attempts", os.path.exists(la1) and os.path.exists(la3)))
if os.path.exists(la1):
    cj = json.load(open(la1))
    checks.append(("lean4_attempt_001 attempt_status == lemma_failed", cj["attempt_status"] == "lemma_failed"))
    la1_dir = os.path.dirname(la1)
    checks.append(("verifier input sources are retained",
                   all(os.path.exists(os.path.join(la1_dir, name)) for name in
                       ("declaration_check_input.lean", "body_check_input.lean"))))
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
    checks.append(("failed candidate roadmap does not persist",
                   "Failed candidate roadmap must not persist." not in prompt))
else:
    checks.append(("candidate3 reasoning prompt written", False))
failed_call_compile = os.path.join(failed_call_dir, "compile.json")
if os.path.exists(failed_call_compile):
    failed_call = json.load(open(failed_call_compile))
    checks.append(("failed translator call has an attempt artifact",
                   failed_call["attempt_status"] == "translation_call_failed"))
    checks.append(("failed translator call records empty output",
                   failed_call["translator_output_empty"] is True and
                   os.path.getsize(os.path.join(failed_call_dir,
                                                "raw_translator_output.md")) == 0))
    checks.append(("failed translator call records its error",
                   "simulated translator timeout" in
                   failed_call["translation_call_error"]))
    checks.append(("failed translator call preserves its prompt",
                   os.path.getsize(os.path.join(failed_call_dir,
                                                "translator_prompt.md")) > 0))
else:
    checks.append(("failed translator call artifact written", False))
checks.append(("translator prompt and pending state precede call",
               observed.get("translation_prompt_precedes_call")))
checks.append(("accepted/proof_step_001..003 present",
               all(os.path.exists(os.path.join(root, "accepted", f"proof_step_{n:03d}", "body.lean")) for n in (1, 2, 3))))
final_compile = os.path.join(
    root, "artifacts", "proof_steps", "proof_step_003",
    "informal_candidate_001", "lean4_attempt_001", "compile.json",
)
if os.path.exists(final_compile):
    checks.append(("Easy final attempt kind is explicit",
                   json.load(open(final_compile))["attempt_kind"] == "easy_finalization"))
    checks.append(("independent final-check input is retained",
                   os.path.exists(os.path.join(os.path.dirname(final_compile),
                                               "final_check_input.lean"))))
else:
    checks.append(("Easy final compile artifact written", False))
# reload state.json and re-verify invariant: exactly one accepted attempt per accepted step
st = StateManager.load(root)
inv = all(sum(1 for c in s.informal_candidates if c.status == "accepted") == 1
          for s in st.proof_steps if s.status in ("accepted", "final_success"))
checks.append(("invariant: 1 accepted candidate per accepted step", inv))
step2_prompt = open(os.path.join(
    root, "artifacts", "proof_steps", "proof_step_002",
    "informal_candidate_001", "reasoning_prompt.md",
)).read()
step2_retry_prompt = open(os.path.join(
    root, "artifacts", "proof_steps", "proof_step_002",
    "informal_candidate_002", "reasoning_prompt.md",
)).read()
step2_rejection_prompt = open(os.path.join(
    root, "artifacts", "proof_steps", "proof_step_002",
    "informal_candidate_003", "reasoning_prompt.md",
)).read()
step3_prompt = open(os.path.join(
    root, "artifacts", "proof_steps", "proof_step_003",
    "informal_candidate_001", "reasoning_prompt.md",
)).read()
step1_translator_prompt = open(os.path.join(
    root, "artifacts", "proof_steps", "proof_step_001",
    "informal_candidate_003", "lean4_attempt_002", "translator_prompt.md",
)).read()
checks.append(("accepted roadmap appears in the next prompt",
               "[High] Prove B = 5 next." in step2_prompt))
checks.append(("accepted progress appears without usefulness",
               "Step 1:\nShow A = 5." in step2_prompt and
               "usefulness:" not in step2_prompt))
rejected_attempt_dir = os.path.join(
    root, "artifacts", "proof_steps", "proof_step_002",
    "informal_candidate_002", "lean4_attempt_002",
)
rejected_candidate_dir = os.path.dirname(rejected_attempt_dir)
difficulty_attempt_dir = os.path.join(
    rejected_candidate_dir, "lean4_attempt_001",
)
difficulty_compile = json.load(open(os.path.join(
    difficulty_attempt_dir, "compile.json",
)))
rejected_compile = json.load(open(os.path.join(
    rejected_attempt_dir, "compile.json",
)))
checks.append(("difficulty rejection is retried as a parse error",
               difficulty_compile["attempt_status"] == "parse_error" and
               os.path.exists(rejected_attempt_dir)))
checks.append(("translator rejection short-circuits without Lean inputs",
               rejected_compile["attempt_status"] == "translator_rejected_step" and
               rejected_compile["translator_rejection_kind"] ==
               "MISSING_ASSUMPTION" and
               not os.path.exists(os.path.join(
                   rejected_attempt_dir, "declaration_check_input.lean")) and
               not os.path.exists(os.path.join(
                   rejected_candidate_dir, "lean4_attempt_003"))))
checks.append(("reasoner receives translator rejection feedback",
               "MISSING_ASSUMPTION" in step2_rejection_prompt and
               "Use a direct evaluation lemma" in step2_rejection_prompt and
               "Show B = 5 in one large automation call."
               in step2_rejection_prompt))
checks.append(("rejected roadmap does not replace accepted roadmap",
               "[High] Prove B = 5 next." in step2_rejection_prompt and
               "This rejected roadmap must not persist."
               not in step2_rejection_prompt))
cheating_attempt_dir = os.path.join(
    root, "artifacts", "proof_steps", "proof_step_002",
    "informal_candidate_001", "lean4_attempt_001",
)
cheating_compile = json.load(open(os.path.join(
    cheating_attempt_dir, "compile.json",
)))
checks.append(("nested sorry candidate fails before compilation",
               cheating_compile["attempt_status"] == "unproved_body_fact" and
               not os.path.exists(os.path.join(
                   cheating_attempt_dir, "declaration_check_input.lean")) and
               "tried to justify a new fact with `sorry`" in step2_retry_prompt))
checks.append(("new accepted roadmap replaces the older roadmap",
               "[High] Combine both equalities." in step3_prompt and
               "[High] Prove B = 5 next." not in step3_prompt))
checks.append(("proved progress remains chronological",
               step3_prompt.index("Step 1:\nShow A = 5.") <
               step3_prompt.index("Step 2:\nShow B = 5.")))
checks.append(("translator excludes planning metadata",
               "STEP USEFULNESS" not in step1_translator_prompt and
               "IDEAS FOR THE FUTURE" not in step1_translator_prompt))
checks.append(("state stores candidate roadmap and final roadmap",
               st.proof_steps[0].informal_candidates[2].future_ideas ==
               "[High] Prove B = 5 next." and
               st.future_ideas == "None — the theorem is complete"))
checks.append(("state stores accepted statements",
               [item.statement for item in st.current_knowledge] ==
               ["Show A = 5.", "Show B = 5."]))
checks.append(("state stores translator rejection metadata",
               st.proof_steps[1].informal_candidates[1]
               .lean_translation_attempts[1].translator_rejection_kind ==
               "MISSING_ASSUMPTION"))
usage = st.stats.llm_usage
checks.append(("offline usage accounts calls without inventing cost",
               usage.total.calls == st.stats.total_llm_calls and
               usage.total.calls == (usage.reasoner.calls + usage.translator.calls +
                                     usage.reviewer.calls) and
               usage.total.calls_with_token_usage == 0 and
               usage.total.calls_with_cost == 0 and
               usage.total.cost_credits == 0))

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
