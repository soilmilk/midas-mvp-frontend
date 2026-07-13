## Lean 4 core nuances
- **Truncated `Nat` subtraction.** For `a b : Nat`, `a - b = 0` when `a < b`. If a step relies on
  signed subtraction, restate with addition (`a = b + c`) or cast to `Int`.

## NEW DECLARATIONS can also have definitions

## Exception where NEW DECLARATIONS is empty
- There is an exception in which you can leave `NEW DECLARATIONS` empty: if the step only restructures the proof (induction setup, `rcases`/`by_cases`, `intro`/`obtain`, `by_contra`), change only the theorem body. Don't manufacture
  a lemma to wrap a tactic.

Example:

### Current Lean 4 file

```lean4
import Mathlib
import Aesop
set_option maxHeartbeats 0
open BigOperators Real Nat Topology Rat

-- Current theorem body
-- The sum of the first `n` odd natural numbers is `n²`.
theorem sum_first_n_odds (n : ℕ) :
    (∑ k in Finset.range n, (2 * k + 1)) = n ^ 2 := by
  sorry
```

### Informal step to translate

```text
STEP 1: We will use induction on n.

PROOF:

We proceed by induction on n.

The base case is n = 0.

For the induction step, assume the result holds for n and prove it for n + 1.
```

### Expected output

INTERMEDIATE REASONING:
This step only introduces the induction structure. It does not establish a
separate reusable mathematical fact, so no new declaration is needed. The
theorem body is updated with a base case, an induction hypothesis, and an
inductive case. The individual cases remain unfinished because later informal
steps will prove them.

NEW DECLARATIONS:
(none)

UPDATED THEOREM BODY:

```lean4
-- Current theorem body
theorem sum_first_n_odds (n : ℕ) :
    (∑ k in Finset.range n, (2 * k + 1)) = n ^ 2 := by
  induction n with
  | zero =>
      sorry
  | succ n ih =>
      sorry
```


