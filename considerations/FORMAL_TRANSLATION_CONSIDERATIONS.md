## No "sorry" statements allowed in NEW DECLARATIONS section
- All the lemmas in NEW DECLARATIONS have to be proved completely - no "sorry" is allowed.
- In addition to lemmas, you can define new objects as needed, to be used in any other part of the proof.

## Sorry statements are allowed in UPDATED THEOREM BODY
- The updated theorem body may contain sorry unless this is the final step.

## Prefer using the new lemmas in the theorem body instead of leaving them unused.

## NEW DECLARATIONS can also have definitions:
- In addition to lemmas, you can define new objects as needed, to be used in any other part of the proof.

## Exception where NEW DECLARATIONS is empty
- There is an exception in which you can leave `NEW DECLARATIONS` empty: ONLY if the English step is about setting up induction, separating into cases, or setting up contradiction. If that happens, change only the theorem body (induction setup, `rcases`/`by_cases`, `intro`/`obtain`, `by_contra`), and leave the NEW DECLARATIONS section as an empty lean4 code fence.

## Lean 4 core nuances
- **Truncated `Nat` subtraction.** For `a b : Nat`, `a - b = 0` when `a < b`. If a step relies on
  signed subtraction, restate with addition (`a = b + c`) or cast to `Int`.