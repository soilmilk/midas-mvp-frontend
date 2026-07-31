## Translate the step (and proof) as new lemmas
- The provided step and proof should be translated as a lemma, whose proof is based on the provided English proof.
- It is forbidden to trivially add a have statement with sorry in the theorem body.

## No "sorry" statements allowed in NEW DECLARATIONS section
- All the lemmas in NEW DECLARATIONS have to be proved completely - no "sorry" is allowed.
- In addition to lemmas, you can define new objects as needed, to be used in any other part of the proof.

## Update theorem body as much as possible
- Use the new lemmas in the theorem body to advance the proof.
- Don't leave the theorem body empty.

## Follow the selected transaction schema exactly
- Return every requested section once, in the requested order, with one complete Lean code fence.
- Do not add sections from a different attempt kind.
- An exploration theorem-body update may remain open. Every region requested for a final
  transaction must be `sorry`-free.
- Preserve the target theorem header byte-for-byte and return the complete theorem body, not a diff.

## Don't make the intermediate reasoning block too long.
- Use at most 1000 words in the "INTERMEDIATE REASONING" section.

## Prefer using the new lemmas in the theorem body instead of leaving them unused.

## NEW DECLARATIONS can also have definitions:
- In addition to lemmas, you can define new objects as needed, to be used in any other part of the proof.

## When NEW DECLARATIONS is empty
- Leave `NEW DECLARATIONS` as an empty Lean code fence when no independent declaration is
  appropriate. Common examples are setting up induction, separating into cases, setting up
  contradiction, or making a final theorem-body-only change.

## Lean 4 core nuances
- **Truncated `Nat` subtraction.** For `a b : Nat`, `a - b = 0` when `a < b`. If a step relies on
  signed subtraction, restate with addition (`a = b + c`) or cast to `Int`.
