theorem main
    (a : ℕ → ℕ)

    -- Every term of the sequence is greater than 1.
    (ha : ∀ n, 1 < a n)

    -- The next term is greater than the current term.
    (hnext_gt : ∀ n, a n < a (n + 1))

    -- The next term has gcd greater than 1 with every previous term.
    (hnext_gcd :
      ∀ n i, i ≤ n →
        1 < Nat.gcd (a (n + 1)) (a i))

    -- The next term is the smallest integer satisfying those conditions.
    (hnext_min :
      ∀ n m,
        a n < m →
        (∀ i, i ≤ n → 1 < Nat.gcd m (a i)) →
        a (n + 1) ≤ m) :
    ∃ T L : ℕ,
      0 < T ∧
      0 < L ∧
      ∀ n, a (n + T) = a n + L := by
  sorry