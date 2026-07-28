#!/usr/bin/env python3
"""Phase 2 translator schemas, prompts, and repair transaction gate."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from midas.agents import TranslationAgent, TranslationRepairContext
from midas.parser import parse_translator_output
from midas.structure import check_structure


rows = []


def check(label, condition, detail=""):
    rows.append((label, bool(condition), detail))


DECL = "theorem helper : True := by trivial"
BODY_OPEN = "theorem main : True := by\n  sorry"
BODY_FINAL = "theorem main : True := by\n  trivial"
PLACEHOLDER_INITIAL = "def answer : Nat := by\n  sorry"
PLACEHOLDER_HEADER = "def answer : Nat := by"
PLACEHOLDER_FINAL = "def answer : Nat := by\n  exact 5"


def transaction(sections):
    return "INTERMEDIATE REASONING:\n...\n\nPLAN:\n...\n\n" + "\n\n".join(
        f"{heading}:\n```lean4\n{code}\n```" for heading, code in sections
    )


exploration_raw = transaction([
    ("NEW DECLARATIONS", DECL),
    ("UPDATED THEOREM BODY", BODY_OPEN),
])
easy_final_raw = transaction([
    ("NEW DECLARATIONS", ""),
    ("FINAL THEOREM BODY", BODY_FINAL),
])
hard_final_raw = transaction([
    ("NEW DECLARATIONS", DECL),
    ("FILLED PLACEHOLDER", PLACEHOLDER_FINAL),
    ("FINAL THEOREM BODY", BODY_FINAL),
])

exploration = parse_translator_output(exploration_raw, "exploration")
check("exploration schema parses",
      exploration.ok and exploration.placeholder is None and
      exploration.body == BODY_OPEN)
easy_final = parse_translator_output(easy_final_raw, "easy_finalization")
check("Easy final schema parses",
      easy_final.ok and easy_final.declarations == "" and
      easy_final.body == BODY_FINAL)
hard_final = parse_translator_output(hard_final_raw, "hard_finalization")
check("Hard final schema parses",
      hard_final.ok and hard_final.placeholder == PLACEHOLDER_FINAL)

invalid = [
    ("exploration placeholder section",
     transaction([
         ("NEW DECLARATIONS", ""),
         ("FILLED PLACEHOLDER", PLACEHOLDER_FINAL),
         ("UPDATED THEOREM BODY", BODY_OPEN),
     ]), "exploration"),
    ("exploration final-body heading", easy_final_raw, "exploration"),
    ("Easy final updated-body heading", exploration_raw, "easy_finalization"),
    ("Hard final missing placeholder", easy_final_raw, "hard_finalization"),
    ("Hard final wrong order",
     transaction([
         ("NEW DECLARATIONS", ""),
         ("FINAL THEOREM BODY", BODY_FINAL),
         ("FILLED PLACEHOLDER", PLACEHOLDER_FINAL),
     ]), "hard_finalization"),
    ("duplicate declarations",
     transaction([
         ("NEW DECLARATIONS", ""),
         ("NEW DECLARATIONS", ""),
         ("UPDATED THEOREM BODY", BODY_OPEN),
     ]), "exploration"),
    ("empty exploration body",
     transaction([
         ("NEW DECLARATIONS", ""),
         ("UPDATED THEOREM BODY", ""),
     ]), "exploration"),
    ("empty filled placeholder",
     transaction([
         ("NEW DECLARATIONS", ""),
         ("FILLED PLACEHOLDER", ""),
         ("FINAL THEOREM BODY", BODY_FINAL),
     ]), "hard_finalization"),
]
for label, raw, kind in invalid:
    parsed = parse_translator_output(raw, kind)
    check(label + " is rejected", not parsed.ok, parsed.error)

imports_raw = transaction([
    ("NEW DECLARATIONS", "import Mathlib\n" + DECL),
    ("UPDATED THEOREM BODY", BODY_OPEN),
])
imports_parsed = parse_translator_output(imports_raw)
check("live parser does not silently strip imports",
      imports_parsed.ok and imports_parsed.declarations.startswith("import Mathlib"))
imports_structure = check_structure(
    imports_parsed.declarations, imports_parsed.body,
    "theorem main : True := by", [],
)
check("import injection becomes a structure error",
      not imports_structure.ok and
      any("forbidden top-level command: import" in v
          for v in imports_structure.violations))

agent = TranslationAgent("offline", "formal considerations", offline_responses=[])
base = dict(
    header="theorem main : True := by",
    informal_problem="Prove True.",
    prelude=[],
    context="def contextMarker : Nat := 1",
    accepted_decls=["theorem acceptedMarker : True := by trivial"],
    current_body=BODY_OPEN,
    informal_candidate=(
        "NEXT STEP:\nFinish.\n\nPROOF:\nTrivial.\n\nIS_FINAL_STEP: True"
    ),
)
easy_prompt = agent.build_prompt(**base, attempt_kind="easy_finalization")
check("Easy final prompt requests exact final schema",
      "FINAL THEOREM BODY:\n```lean4" in easy_prompt and
      "UPDATED THEOREM BODY:" not in easy_prompt)
check("Easy final prompt contains no placeholder protocol",
      "FILLED PLACEHOLDER" not in easy_prompt and "placeholder" not in easy_prompt.lower())
check("Easy final prompt contains no ANSWER field", "ANSWER" not in easy_prompt)
check("Easy final prompt requires sorry-free output",
      "must be `sorry`-free" in easy_prompt)

hard_exploration_prompt = agent.build_prompt(
    **base,
    attempt_kind="exploration",
    placeholder_header=PLACEHOLDER_HEADER,
    placeholder_initial_source=PLACEHOLDER_INITIAL,
)
check("Hard exploration current file contains placeholder",
      PLACEHOLDER_INITIAL in hard_exploration_prompt)
check("Hard exploration marks placeholder immutable",
      "unresolved placeholder shown in the current file is immutable"
      in hard_exploration_prompt)
check("Hard exploration requests no placeholder code section",
      "FILLED PLACEHOLDER:\n```lean4" not in hard_exploration_prompt)
check("Hard exploration allows open theorem body",
      "it may contain `sorry`" in hard_exploration_prompt)

hard_final_prompt = agent.build_prompt(
    **base,
    attempt_kind="hard_finalization",
    placeholder_header=PLACEHOLDER_HEADER,
    placeholder_initial_source=PLACEHOLDER_INITIAL,
    answer="the natural number 5",
)
check("Hard final prompt requests all three regions",
      all(section in hard_final_prompt for section in (
          "NEW DECLARATIONS:\n```lean4",
          "FILLED PLACEHOLDER:\n```lean4",
          "FINAL THEOREM BODY:\n```lean4",
      )))
check("Hard final prompt includes exact placeholder header",
      PLACEHOLDER_HEADER in hard_final_prompt)
check("Hard final prompt includes English answer",
      "<english_answer>\nthe natural number 5\n</english_answer>" in hard_final_prompt)
check("Hard final prompt states authoritative source order",
      "accepted declarations, NEW DECLARATIONS, FILLED PLACEHOLDER, "
      "FINAL THEOREM BODY" in hard_final_prompt)

repair_declarations = "theorem repairOnlyDeclaration : True := by exact missing"
repair_placeholder = "def answer : Nat := by\n  exact missingValue"
repair_body = "theorem main : True := by\n  exact missingProof"
previous_raw = transaction([
    ("NEW DECLARATIONS", repair_declarations),
    ("FILLED PLACEHOLDER", repair_placeholder),
    ("FINAL THEOREM BODY", repair_body),
])
repair = TranslationRepairContext(
    failed_check="body_check",
    raw_output=previous_raw,
    declarations=repair_declarations,
    body=repair_body,
    diagnostics="#### Error 1\nplaceholder diagnostic",
    attempt_kind="hard_finalization",
    placeholder=repair_placeholder,
)
repair_prompt = agent.build_prompt(
    **base,
    attempt_kind="hard_finalization",
    placeholder_header=PLACEHOLDER_HEADER,
    placeholder_initial_source=PLACEHOLDER_INITIAL,
    answer="5",
    repair_context=repair,
)
check("final repair includes previous raw transaction once",
      repair_prompt.count(previous_raw) == 1)
check("final repair does not duplicate parsed declarations",
      repair_prompt.count(repair_declarations) == 1)
check("final repair does not duplicate parsed placeholder",
      repair_prompt.count(repair_placeholder) == 1)
check("final repair does not duplicate parsed theorem body",
      repair_prompt.count(repair_body) == 1)
check("final repair includes all diagnostics",
      "#### Error 1\nplaceholder diagnostic" in repair_prompt)
check("final repair requires complete transaction",
      "Return the complete NEW DECLARATIONS, complete FILLED PLACEHOLDER, "
      "and complete FINAL THEOREM BODY again" in repair_prompt)

print(f"{'check':<62}{'result':<8}detail")
print("-" * 110)
all_ok = True
for label, ok, detail in rows:
    all_ok &= ok
    print(f"{label:<62}{'PASS' if ok else 'FAIL':<8}{detail}")
print("-" * 110)
print(f"\nTRANSLATION PROTOCOL: {'PASS' if all_ok else 'FAIL'}")
sys.exit(0 if all_ok else 1)
