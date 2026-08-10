#!/usr/bin/env python3
"""Phase 2 strict reasoner actions and English-only prompt gate."""
import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from midas.agents import ReasoningAgent
from midas.models import AcceptedKnowledge, ProofRunState, attempt_kind_for
from midas.parser import parse_reasoning_action


rows = []


def check(label, condition, detail=""):
    rows.append((label, bool(condition), detail))


EASY_FALSE = """INTERMEDIATE REASONING:
We should establish one small fact.

NEXT STEP:
Show the first equality.

PROOF:
Evaluate the left side directly.

IS_FINAL_STEP: False

IDEAS FOR THE FUTURE:
[High] Use the equality in the main goal.
"""
EASY_TRUE = EASY_FALSE.replace("IS_FINAL_STEP: False", "IS_FINAL_STEP: True")
HARD_TRUE = EASY_TRUE.replace(
    "\nIDEAS FOR THE FUTURE:", "\nANSWER:\n5\n\nIDEAS FOR THE FUTURE:"
)

easy = parse_reasoning_action(EASY_FALSE, "easy")
check("valid Easy action parses", easy.ok)
check("NEXT STEP is retained", easy.next_step == "Show the first equality.")
check("PROOF is retained", easy.proof == "Evaluate the left side directly.")
check("action has no usefulness field", not hasattr(easy, "step_usefulness"))
check("future ideas are retained",
      easy.future_ideas == "[High] Use the equality in the main goal.")
check("false flag remains Boolean false", easy.is_final_step is False)
check("accepted action starts at NEXT STEP",
      easy.informal_step.startswith("NEXT STEP:") and
      "INTERMEDIATE REASONING" not in easy.informal_step and
      "STEP USEFULNESS" not in easy.informal_step and
      "IDEAS FOR THE FUTURE" not in easy.informal_step)

hard = parse_reasoning_action(HARD_TRUE, "hard")
check("valid Hard final action parses",
      hard.ok and hard.is_final_step is True and hard.answer == "5")

invalid = [
    ("missing NEXT STEP", "PROOF:\np\n\nIS_FINAL_STEP: False", "easy"),
    ("empty NEXT STEP", "NEXT STEP:\n\nPROOF:\np\n\nIS_FINAL_STEP: False", "easy"),
    ("missing PROOF", "NEXT STEP:\nn\n\nIS_FINAL_STEP: False", "easy"),
    ("empty PROOF", "NEXT STEP:\nn\n\nPROOF:\n\nIS_FINAL_STEP: False", "easy"),
    ("missing final flag", "NEXT STEP:\nn\nPROOF:\np", "easy"),
    ("obsolete usefulness", EASY_FALSE.replace(
        "IS_FINAL_STEP: False",
        "STEP USEFULNESS:\nHigh\n\nIS_FINAL_STEP: False"), "easy"),
    ("missing future ideas", EASY_FALSE.replace(
        "\nIDEAS FOR THE FUTURE:\n[High] Use the equality in the main goal.", ""), "easy"),
    ("empty future ideas", EASY_FALSE.replace(
        "[High] Use the equality in the main goal.", ""), "easy"),
    ("lowercase Boolean", EASY_FALSE.replace("False", "false"), "easy"),
    ("non-Boolean final flag", EASY_FALSE.replace("False", "No"), "easy"),
    ("duplicate NEXT STEP", EASY_FALSE + "\nNEXT STEP:\nagain", "easy"),
    ("duplicate final flag", EASY_FALSE + "\nIS_FINAL_STEP: False", "easy"),
    ("misordered fields",
     "PROOF:\np\n\nNEXT STEP:\nn\n\nIS_FINAL_STEP: False", "easy"),
    ("future ideas before final flag", EASY_FALSE.replace(
        "IS_FINAL_STEP: False\n\nIDEAS FOR THE FUTURE:\n"
        "[High] Use the equality in the main goal.",
        "IDEAS FOR THE FUTURE:\n[High] Use the equality in the main goal.\n\n"
        "IS_FINAL_STEP: False"), "easy"),
    ("Easy ANSWER", EASY_TRUE.replace(
        "\nIDEAS FOR THE FUTURE:", "\nANSWER:\n5\n\nIDEAS FOR THE FUTURE:"), "easy"),
    ("Hard non-final ANSWER", EASY_FALSE.replace(
        "\nIDEAS FOR THE FUTURE:", "\nANSWER:\n5\n\nIDEAS FOR THE FUTURE:"), "hard"),
    ("Hard final missing ANSWER", EASY_TRUE, "hard"),
    ("Hard final empty ANSWER", EASY_TRUE.replace(
        "\nIDEAS FOR THE FUTURE:", "\nANSWER:\n\nIDEAS FOR THE FUTURE:"), "hard"),
]
for label, raw, mode in invalid:
    parsed = parse_reasoning_action(raw, mode)
    check(label + " is rejected", not parsed.ok, parsed.error)

check("attempt kind: Easy exploration",
      attempt_kind_for("easy", False) == "exploration")
check("attempt kind: Hard exploration",
      attempt_kind_for("hard", False) == "exploration")
check("attempt kind: Easy finalization",
      attempt_kind_for("easy", True) == "easy_finalization")
check("attempt kind: Hard finalization",
      attempt_kind_for("hard", True) == "hard_finalization")
legacy_state = ProofRunState.model_validate({
    "problem_id": "legacy",
    "informal_problem_path": "problem.md",
    "context_path": "context.lean",
    "initial_body_path": "body.lean",
    "current_knowledge": ["A legacy proved fact."],
})
check("legacy state defaults to an empty roadmap", legacy_state.future_ideas == "")
check("legacy string knowledge is migrated",
      legacy_state.current_knowledge[0].statement == "A legacy proved fact.")
legacy_rated_state = ProofRunState.model_validate({
    "problem_id": "legacy-rated",
    "informal_problem_path": "problem.md",
    "context_path": "context.lean",
    "initial_body_path": "body.lean",
    "current_knowledge": [
        {"statement": "An old proved fact.", "step_usefulness": "Low"}
    ],
    "proof_steps": [{
        "proof_step_index": 1,
        "informal_candidates": [{
            "informal_candidate_index": 1,
            "step_usefulness": "High",
        }],
    }],
})
legacy_dump = legacy_rated_state.model_dump()
check("legacy ratings load but are not re-serialized",
      legacy_rated_state.current_knowledge[0].statement == "An old proved fact." and
      "step_usefulness" not in legacy_dump["current_knowledge"][0] and
      "step_usefulness" not in legacy_dump["proof_steps"][0]["informal_candidates"][0])

legacy_action_text = EASY_FALSE.replace(
    "IS_FINAL_STEP: False",
    "STEP USEFULNESS:\nHigh\n\nIS_FINAL_STEP: False",
)
legacy_action = parse_reasoning_action(
    legacy_action_text,
    "easy",
    allow_legacy_step_usefulness=True,
)
check("legacy saved action parses only in compatibility mode",
      legacy_action.ok and legacy_action.proof == "Evaluate the left side directly.")

considerations = open(
    os.path.join(ROOT, "considerations", "INFORMAL_REASONING_CONSIDERATIONS.md")
).read()
agent = ReasoningAgent("offline", considerations, offline_responses=[])
easy_prompt = agent.build_prompt(
    "ENGLISH_PROBLEM_SENTINEL",
    [AcceptedKnowledge(statement="ENGLISH_PROGRESS_SENTINEL")],
    problem_mode="easy",
    failure_feedback="Use a smaller English step.",
    failed_next_step="FAILED_ENGLISH_STEP_SENTINEL",
)
hard_prompt = agent.build_prompt(
    "ENGLISH_PROBLEM_SENTINEL",
    [],
    problem_mode="hard",
)
check("Easy prompt requires final flag", "IS_FINAL_STEP" in easy_prompt)
check("Easy prompt contains no ANSWER substring", "ANSWER" not in easy_prompt)
check("Easy prompt omits usefulness", "STEP USEFULNESS" not in easy_prompt)
check("prompt lists verified progress without ratings",
      "Step 1:\nENGLISH_PROGRESS_SENTINEL" in easy_prompt and
      "usefulness:" not in easy_prompt)
check("empty proved progress has an explicit marker",
      "## Previously proved steps\n\n(none yet)" in hard_prompt)
check("Easy prompt ends the schema with future ideas",
      "IDEAS FOR THE FUTURE must be the final section" in easy_prompt)
check("prompt distinguishes future ideas from proved progress",
      "planning context; not yet proved" in easy_prompt)
check("Hard prompt explains conditional ANSWER",
      "ANSWER is required and non-empty exactly for a final action" in hard_prompt)
check("Hard prompt requests an English or mathematical answer",
      "the concrete mathematical answer in English or mathematical notation" in hard_prompt)
check("reasoner retry retains failed NEXT STEP",
      "FAILED_ENGLISH_STEP_SENTINEL" in easy_prompt)
check("reasoner prompt contains only supplied English state",
      all(secret not in easy_prompt for secret in (
          "LEAN_CONTEXT_SENTINEL",
          "PLACEHOLDER_LEAN_SENTINEL",
          "ACCEPTED_DECL_SENTINEL",
          "THEOREM_BODY_SENTINEL",
          "COMPILER_DIAGNOSTIC_SENTINEL",
      )))
signature = inspect.signature(ReasoningAgent.build_prompt)
check("reasoner signature excludes Lean-state inputs",
      all(name not in signature.parameters for name in (
          "knowledge", "context_summary", "accepted_decls_summary", "current_body"
      )))

print(f"{'check':<56}{'result':<8}detail")
print("-" * 100)
all_ok = True
for label, ok, detail in rows:
    all_ok &= ok
    print(f"{label:<56}{'PASS' if ok else 'FAIL':<8}{detail}")
print("-" * 100)
print(f"\nREASONING PROTOCOL: {'PASS' if all_ok else 'FAIL'}")
sys.exit(0 if all_ok else 1)
