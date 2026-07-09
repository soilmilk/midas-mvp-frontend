-- reference
def twice (n : Nat) : Nat := 2 * n
theorem main : ∀ n, n ≤ twice n := by intro n; unfold twice; omega
