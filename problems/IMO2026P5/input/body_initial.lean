theorem imo_2026_p5 :
    ∀ f : PosReal → PosReal,
      f ∈ imo_2026_p5_solution ↔
        ∀ x y : PosReal,
          Real.sqrt (((x : ℝ) ^ 2 + (f y : ℝ) ^ 2) / 2)
              ≥ ((f x : ℝ) + (y : ℝ)) / 2 ∧
          ((f x : ℝ) + (y : ℝ)) / 2
              ≥ Real.sqrt ((x : ℝ) * (f y : ℝ)) := by
  sorry
