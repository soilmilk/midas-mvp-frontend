theorem main
    (a : ℕ → ℕ)

    -- Every term is greater than 1.
    (ha : ∀ n : ℕ, 1 < a n)

    -- Every next term is strictly greater than the current term.
    (hnext_gt : ∀ n : ℕ, a n < a (n + 1))

    -- Every next term has a nontrivial gcd with every preceding term.
    (hnext_gcd :
      ∀ n i : ℕ,
        i ≤ n →
        1 < Nat.gcd (a (n + 1)) (a i))

    -- The next term is the smallest natural number satisfying
    -- the preceding two requirements.
    (hnext_min :
      ∀ n m : ℕ,
        a n < m →
        (∀ i : ℕ, i ≤ n → 1 < Nat.gcd m (a i)) →
        a (n + 1) ≤ m) :
    ∃ T L : ℕ,
      0 < T ∧
      0 < L ∧
      ∀ n : ℕ,
        a (n + T) = a n + L := by
  sorry