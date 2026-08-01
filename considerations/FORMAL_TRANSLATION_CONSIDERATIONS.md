## Translate the step (and proof) as new lemmas
- The provided step and proof should be translated as a lemma, whose proof is based on the provided English proof.
- It is forbidden to trivially add a have statement with `sorry` or `admit` in the theorem body.
- The inherited final theorem hole may remain during exploration, but it must be the only `sorry`
  and the final standalone tactic. Never move an unproved requested fact into a local proof hole.

## Reject an unsuitable informal candidate explicitly
- Use the exact TRANSLATION REJECTED protocol ONLY when the step is mathematically incorrect, lacks a
  needed assumption, or conflicts with established context.
- Formalization difficulty is not a rejection reason. Unknown library names, tedious algebra, or
  missing infrastructure should be approached step by step: define helper objects, prove smaller
  internal lemmas, unfold definitions, or use algebraic automation so compiler feedback can guide
  the next attempt.
- A non-empty INTERMEDIATE REASONING section must precede the rejection and identify the exact
  mathematical or contextual defect. Do not decide first and rationalize afterward.
- Do not include any Lean transaction sections in a rejection.

## Reason before choosing a translation
- INTERMEDIATE REASONING and PLAN are mandatory and non-empty for every Lean transaction.
- First state the exact Lean proposition to establish and inventory relevant hypotheses and
  definitions. Consider both a Mathlib-lemma route and an unfold/vector/algebra fallback, then
  commit to one concrete implementation plan.

## No "sorry" statements allowed in NEW DECLARATIONS section
- All the lemmas in NEW DECLARATIONS have to be proved completely - no "sorry" is allowed.
- In addition to lemmas, you can define new objects as needed, to be used in any other part of the proof.

## Update theorem body as much as possible
- Use the new lemmas in the theorem body to advance the proof.
- Don't leave the theorem body empty.
- Definitions alone do not prove an English claim. If the theorem body remains unchanged, NEW
  DECLARATIONS must include a fully proved theorem or lemma formalizing the requested step. An
  unused definition plus the inherited final `sorry` is rejected as no formal progress.

## Follow the selected transaction schema exactly
- Return every requested section once, in the requested order, with one complete Lean code fence.
- Do not add sections from a different attempt kind.
- An exploration theorem-body update may remain open. Every region requested for a final
  transaction must be `sorry`-free. An exploration update may not add holes or use a hole for a
  local fact.
- Preserve the target theorem header byte-for-byte and return the complete theorem body, not a diff.

## Don't make the intermediate reasoning block too long.
- Use at most 1000 words in the "INTERMEDIATE REASONING" section.

## Prefer using the new lemmas in the theorem body instead of leaving them unused.

## NEW DECLARATIONS can also have definitions:
- In addition to lemmas, you can define new objects as needed, to be used in any other part of the proof.
- Specially useful in geometry: sometimes, when it seems that the step is too diffcult to translate due to
  lack of infrastructure, you can build your own infrastructure by defining the necessary objects.


## When NEW DECLARATIONS is empty
- Leave `NEW DECLARATIONS` as an empty Lean code fence when no independent declaration is
  appropriate. Common examples are setting up induction, separating into cases, setting up
  contradiction, or making a final theorem-body-only change.

## Lean 4 core nuances
- **Truncated `Nat` subtraction.** For `a b : Nat`, `a - b = 0` when `a < b`. If a step relies on
  signed subtraction, restate with addition (`a = b + c`) or cast to `Int`.
