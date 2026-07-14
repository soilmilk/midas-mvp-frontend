theorem imo2015_shortlist_a1
    (a : ℕ → ℝ)
    (hpos : ∀ k : ℕ, 1 ≤ k → 0 < a k)
    (hrec : ∀ k : ℕ, 1 ≤ k →
      a (k + 1) ≥ ((k : ℝ) * a k) / ((a k)^2 + (k : ℝ) - 1))
    (n : ℕ)
    (hn : 2 ≤ n) :
    (∑ k ∈ Finset.Icc 1 n, a k) ≥ (n : ℝ) := by
  sorry