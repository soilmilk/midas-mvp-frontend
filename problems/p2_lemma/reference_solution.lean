-- Reference solution (not used by the loop; documents provability).
-- The key is the GENERALIZED accumulator lemma — direct induction on `main` is too weak.
def sumTo : Nat → Nat
  | 0 => 0
  | n + 1 => (n + 1) + sumTo n
def sumAcc : Nat → Nat → Nat
  | 0, a => a
  | n + 1, a => sumAcc n (a + (n + 1))
theorem sumAcc_gen : ∀ n a, sumAcc n a = sumTo n + a := by
  intro n
  induction n with
  | zero => intro a; simp only [sumAcc, sumTo]; omega
  | succ n ih => intro a; simp only [sumAcc, sumTo, ih]; omega
theorem main (n : Nat) : sumAcc n 0 = sumTo n := by
  rw [sumAcc_gen]; omega
