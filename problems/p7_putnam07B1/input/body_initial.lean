theorem main
    (f : Polynomial ℕ)
    (hdeg : 0 < f.natDegree)
    (hcoeff : ∀ k ≤ f.natDegree, 0 < f.coeff k)
    (n : ℕ)
    (hn : 0 < n) :
    (f.eval n ∣ f.eval (f.eval n + 1)) ↔ n = 1 := by
  sorry