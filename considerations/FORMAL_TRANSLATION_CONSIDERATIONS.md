
## Output structure (hard requirement)
- Output the two required sections, in this order, each as a fenced code block:
  `NEW DECLARATIONS:` then `UPDATED THEOREM BODY:`. Intermediate reasoning before them is fine.
- `NEW DECLARATIONS` is a **delta**: only new lemmas/defs, each with a descriptive comment. It
  may be **empty** if only the theorem body changes. It must contain **no `sorry`** and must not
  repeat previously accepted declarations.
- `UPDATED THEOREM BODY` is the **complete** theorem declaration, not a delta. Copy the original
  header **byte-for-byte**; change only the proof after `:= by`. It may contain `sorry` on
  intermediate steps; on the final step it must not.
- Neither section may contain `import` lines (the prelude supplies them; context and accepted
  declarations are already available).

## Structure-only steps
- If the step only restructures the proof (induction setup, `rcases`/`by_cases`, `intro`/`obtain`,
  `by_contra`), leave `NEW DECLARATIONS` empty and change only the theorem body. Don't manufacture
  a lemma to wrap a tactic.

## Copy the header exactly
- Do not change the theorem name, binders, hypotheses, or conclusion. A single byte difference
  (spacing, a renamed binder) fails the structure check before compilation. Only the proof moves.

## This MVP is Core/Std only — no Mathlib
- Do not use Mathlib lemmas or notation. Stay within Lean 4 core + Std. If a step seems to need
  Mathlib, prefer a more elementary proof (`decide`, `rfl`, `omega`, `simp`, `Nat`/`List` core lemmas).

## Lean 4 core nuances
- **Truncated `Nat` subtraction.** For `a b : Nat`, `a - b = 0` when `a < b`. If a step relies on
  signed subtraction, restate with addition (`a = b + c`) or cast to `Int`.
- **Decidable goals.** Concrete `Nat`/`Bool` (in)equalities close with `by decide`; concrete
  arithmetic with `by decide` or `rfl`; linear arithmetic over `Nat`/`Int` with `by omega`. Avoid
  `native_decide` — it adds non-standard axioms.
- **Term vs tactic mode.** Use `by ...` for anything with case splits, induction, or multiple
  steps; reserve one-line term proofs for direct applications.
- **Closing tactic choice.** `rfl`/`decide` for definitional/decidable goals, `omega` for linear
  arithmetic, `simp [lemma1, lemma2]` only with named lemmas (bare `simp` on an underspecified goal
  is a common "tactic failed"). Match the tactic to the goal shape.
- **Namespacing.** Declare lemmas in the same namespace as the theorem body, or they compile but
  are not found by name (shows up as an `unknownIdentifier` that looks like a missing lemma).

## Flag uncertainty
- If you invoke a lemma you are not confident exists in Core/Std, mark it with a comment
  `-- verify: core/std name` so an `unknownIdentifier` failure is easy to triage.

## Examples
*(placeholder — add 2–3 worked step→output pairs once real successful translations accumulate.)*