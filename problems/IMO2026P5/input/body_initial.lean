theorem functional_inequality_hard :
    ∀ f : ℝ → ℝ,
      f ∈ functional_inequality_solution ↔
        (∀ x : ℝ, 0 < x → 0 < f x) ∧
        ∀ x y : ℝ,
          0 < x →
          0 < y →
          Real.sqrt ((x ^ 2 + (f y) ^ 2) / 2)
              ≥ (f x + y) / 2 ∧
          (f x + y) / 2
              ≥ Real.sqrt (x * f y) := by
  sorry
