-- Reference solution (not used by the loop; documents provability).
-- Separate lemmas about A (recursive, needs induction) and B (closed form), combined at the end.
def dblA : Nat → Nat
  | 0 => 0
  | n + 1 => dblA n + 2
def dblB (n : Nat) : Nat := n + n
def f (n : Nat) : Nat := n + 1
theorem A_eq : ∀ n, dblA n = n + n := by
  intro n; induction n with
  | zero => rfl
  | succ n ih => simp only [dblA, ih]; omega
theorem B_eq : ∀ n, dblB n = n + n := by intro n; rfl
theorem main (n : Nat) : f (dblA n) = f (dblB n) := by
  simp only [f, A_eq, B_eq]
