theorem primes_sq_add_two_hard :
    ∀ p : ℕ,
      p ∈ primes_sq_add_two_solution ↔
        Nat.Prime p ∧ Nat.Prime (p ^ 2 + 2) := by
  sorry