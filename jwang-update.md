# Translator compiler-repair loop — implementation report

## Summary

The translator retry loop now gives attempts 2 and 3 enough information to repair the immediately
preceding Lean compilation failure. After a `lemma_failed` or `body_failed` attempt, the next
translator prompt contains:

- the complete previous translator output;
- every compiler error returned by the verifier;
- the file, line, column, severity, and diagnostic code when available;
- a classification of whether the error occurred in the rejected declarations, rejected theorem
  body, or previously verified/base code;
- numbered Lean source lines around each reported location, with a caret at the failing column; and
- explicit instructions to fix every error and return a complete corrected translation rather than
  a patch.

The broad reasoner → translator → verifier pipeline is unchanged. The feature only improves the
feedback supplied inside the existing three-attempt translator loop.

## Why this change was needed

Previously, a failed translation retry received only a short compiler-feedback string. The next
translator could see the error message, but it did not reliably receive the exact code that caused
the error or enough source context to identify the failing expression.

This was especially weak for errors such as:

```text
Function expected at `sq_pos_of_ne_zero ?m.216` but this term has type ...
```

Without the rejected output and nearby source lines, the translator had to guess which application
it needed to repair.

## Retry behavior

For each informal candidate, Midas still permits the configured number of translation attempts,
normally three.

```text
translation attempt 1
  └─ Lean compilation fails

translation attempt 2
  ├─ receives attempt 1 complete output
  ├─ receives all attempt 1 compiler errors
  └─ receives located source excerpts

translation attempt 3
  ├─ receives attempt 2 complete output
  ├─ receives all attempt 2 compiler errors
  └─ receives located source excerpts
```

Only the immediately preceding rejected compilation is included. This keeps the repair target clear
and avoids accumulating obsolete attempts in the prompt. When Midas moves to a new informal
candidate, the repair context resets.

If a retry instead produces a parser or structural-format failure, the compiler repair context is
cleared and the existing parse/format feedback path is used.

## Final translator prompt shape

The repair prompt intentionally includes the rejected code only once:

~~~~text
## Previous rejected translation — repair this exact output

The `declaration_check` failed.

### Complete previous model output

<previous_model_output>
INTERMEDIATE REASONING:
...

NEW DECLARATIONS:
```lean4
...
```

UPDATED THEOREM BODY:
```lean4
...
```
</previous_model_output>

### All compiler errors and their source locations

#### Error 1
- Diagnostic: `<req>:20:10` error
- Source region: rejected NEW DECLARATIONS
- Message: ...
- Nearby submitted Lean code:
  ...

### Required repair behavior
...
~~~~

An earlier implementation also printed separate `Parsed NEW DECLARATIONS` and
`Parsed UPDATED THEOREM BODY` sections. Those sections duplicated code already present in the
complete previous model output, so they were removed.

Parsed declarations and theorem bodies are still retained internally. Midas uses them to reconstruct
the exact verifier input, classify error locations, and generate nearby source excerpts; it simply
does not print them a second time in the translator prompt.

## Source-location mapping

Midas reconstructs the source layout submitted to the selected verifier:

```text
prelude
context
previously accepted declarations
rejected new declarations
rejected updated theorem body
```

For the warm backend, import lines are omitted from this reconstruction because Mathlib is already
resident and the warm verifier strips imports before elaboration. This keeps reported `<req>` line
numbers aligned with the source excerpts shown to the translator.

Each compiler error is mapped to one of three regions:

- `rejected NEW DECLARATIONS`;
- `rejected UPDATED THEOREM BODY`; or
- `prelude, context, or previously accepted code`.

The prompt shows up to three lines before and after the reported line and places a caret at the
reported column. For legacy warm diagnostics whose structured line is zero, Midas attempts to
recover an embedded `<req>:line:column` location from the message.

## Warm verifier diagnostic preservation

The warm verifier previously discarded information before Python could build the retry prompt:

- the Lean server reduced compiler output to approximately 90 characters; and
- the Python adapter reduced the synthetic error message to 300 characters.

The warm protocol now preserves complete multiline compiler output. Newlines are encoded safely on
the server's line-oriented response and decoded by the Python backend. Python then parses every Lean
diagnostic separately, preserving:

- file;
- line;
- column;
- severity;
- diagnostic code; and
- the complete multiline message.

This applies to declaration checks, body checks, and final reconstructed-file checks. Verifier
acceptance and rejection rules were not changed.

## Files changed

- `midas/agents.py`
  - defines the structured translation repair context;
  - renders the complete previous output, diagnostics, and repair instructions;
  - avoids duplicate parsed-artifact sections.

- `midas/loop.py`
  - captures the immediately preceding rejected translation;
  - reconstructs verifier source for location mapping;
  - classifies diagnostic regions and renders nearby code;
  - passes repair context only within the current translator-attempt loop.

- `midas/warm_backend.py`
  - decodes complete warm responses;
  - parses multiple structured diagnostics;
  - removes Python-side message truncation.

- `warm-server/Warm.lean`
  - stops truncating compiler output;
  - transports multiline diagnostics safely.

- `warm-server/WarmChain.lean`
  - applies the same diagnostic transport behavior to the chained verifier.

- `tests/test_offline.py`
  - verifies complete previous-output inclusion;
  - verifies declarations and body are not duplicated;
  - verifies multiple diagnostics, multiline messages, codes, and source-region mapping.

- `tests/test_loop_offline.py`
  - drives a real offline `lemma_failed` attempt through the loop;
  - confirms attempt 2 receives the rejected declaration, theorem body, and located compiler error.

## What did not change

The implementation does not change:

- the reasoning-agent workflow;
- the number of translator attempts;
- candidate abandonment rules;
- checkpoint acceptance criteria;
- declaration/body compilation order;
- accepted-declaration accumulation;
- proof-step or runtime limits;
- artifact logging; or
- the independent, sorry-free final reconstruction requirement.

## Verification performed

The following checks passed after implementation:

- `python tests/test_offline.py`;
- `python tests/test_loop_offline.py`;
- `python verifier/run_phase1.py`;
- Python syntax compilation for the changed modules and tests;
- `lake build warm`;
- `lake build warmchain`;
- an end-to-end warm Mathlib failure containing two distinct Lean diagnostics; and
- `git diff --check`.

No live OpenRouter model run was required, so the verification used no model API credits.
