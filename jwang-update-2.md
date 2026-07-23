# Translator PLAN block — prompt update report

## Summary

The translator prompt now requires an explicit `PLAN` block before it emits Lean code. The plan asks
the translator to state:

- which lemmas or definitions it will propose;
- how it intends to prove them; and
- how it will change the target theorem body to use them.

The goal is to make the translator commit to a coherent Lean-level implementation strategy before
generating declarations and the updated theorem body. This is a prompt-only behavior change; proof
search, parsing, verification, and checkpoint acceptance are unchanged.

## New translator output shape

The requested response structure is now:

~~~~text
INTERMEDIATE REASONING:
<assess the current Lean file and the English step>

PLAN:
<state which lemmas or definitions will be proposed, how they will be proved,
and how the theorem body will be changed>

NEW DECLARATIONS:
```lean4
...
```

UPDATED THEOREM BODY:
```lean4
...
```
~~~~

If a new declaration is not appropriate, the translator is instructed to say so in the plan and
explain the intended theorem-body change.

## Why this change was made

Previously, the translator moved directly from intermediate reasoning to generated Lean artifacts.
That allowed it to begin coding without clearly identifying the declarations it needed, their proof
strategy, or how the target theorem would use them.

The `PLAN` block adds a short planning checkpoint intended to improve focus and consistency,
especially for lemma-first steps. It does not itself enforce lemma creation; the runtime still
permits an empty declaration delta where allowed by the existing prompt and parser contract.

## Parser compatibility

No parser change was required. `PLAN` is placed before `NEW DECLARATIONS`, while the parser continues
to locate and extract only the existing Lean artifact sections:

- `NEW DECLARATIONS`; and
- `UPDATED THEOREM BODY`.

The strict contract between those two headings remains unchanged: `NEW DECLARATIONS` must contain
exactly one Lean 4 or unlabeled code fence before `UPDATED THEOREM BODY`. Keeping `PLAN` before that
region prevents it from interfering with parsing.

## Files changed

- `midas/agents.py`
  - requires the translator to produce a plan before Lean code;
  - defines the three questions that the plan must answer; and
  - adds `PLAN` to the example output structure before `NEW DECLARATIONS`.

- `tests/test_offline.py`
  - verifies that generated translator prompts contain the `PLAN` heading;
  - verifies that the prompt asks which lemmas or definitions will be proposed; and
  - verifies that the prompt asks how the theorem body will be changed.

## What did not change

The update does not change:

- either considerations file;
- the informal reasoning-agent prompt;
- translator retry counts or compiler-repair feedback;
- the translator output parser;
- structural validation;
- Lean declaration or body checks;
- checkpoint acceptance criteria;
- accumulated declarations or theorem reconstruction; or
- final independent compilation.

The `PLAN` block is explanatory prose and is not stored as a separate parsed artifact.

## Verification performed

The following checks passed after the update:

- `python3 tests/test_offline.py`;
- `python3 tests/test_loop_offline.py`; and
- `git diff --check` for the files changed by this update.

No live OpenRouter call was made, so verification used no model API credits.
