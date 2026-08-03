theorem main
    (s : Multiset ℕ)

    -- Initially, there are exactly 2026 places on the board.
    (hcard : s.card = 2026)

    -- Every initial entry is greater than 1.
    (hlarge : ∀ x, x ∈ s → 1 < x) :
    ∃ M : ℕ,
      1 < M ∧

      -- Every sequence of legal moves is finite.
      TerminatesFrom s ∧

      -- At least one terminal board is reachable.
      (∃ t,
        Reachable s t ∧
        Terminal t) ∧

      -- Every reachable terminal board has exactly the same
      -- unique entry M greater than 1.
      ∀ t,
        Reachable s t →
        Terminal t →
        t.filter (fun x => 1 < x) = {M} := by
  sorry