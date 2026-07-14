import Mathlib
import Aesop
set_option maxHeartbeats 0
open BigOperators Real Nat Topology Rat

-- Step 1: every shifted sequence term is positive.
-- This will be useful whenever a later step needs positivity of `a (k + 1)`,
-- especially when taking reciprocals.
lemma imo2015_shortlist_a1_pos_succ
    (a : ℕ → ℝ)
    (hpos : ∀ k : ℕ, 1 ≤ k → 0 < a k) :
    ∀ k : ℕ, 0 < a (k + 1) := by
  intro k
  exact hpos (k + 1) (Nat.succ_le_succ (Nat.zero_le k))

-- Step 1: the denominator in the recurrence is positive.
-- The denominator is `(a k)^2 + k - 1`; positivity follows from
-- `(a k)^2 > 0` and `k ≥ 1`.
lemma imo2015_shortlist_a1_den_pos
    (a : ℕ → ℝ)
    (hpos : ∀ k : ℕ, 1 ≤ k → 0 < a k) :
    ∀ k : ℕ, 1 ≤ k → 0 < (a k)^2 + (k : ℝ) - 1 := by
  intro k hk
  have hak_pos : 0 < a k := hpos k hk
  have hak_sq_pos : 0 < (a k)^2 := by
    exact sq_pos_of_ne_zero (ne_of_gt hak_pos)
  have hk_real : (1 : ℝ) ≤ (k : ℝ) := by
    exact_mod_cast hk
  nlinarith

-- Step 1: the right-hand side of the recurrence is positive.
-- This packages positivity of the numerator and denominator of the recurrence fraction.
lemma imo2015_shortlist_a1_rec_rhs_pos
    (a : ℕ → ℝ)
    (hpos : ∀ k : ℕ, 1 ≤ k → 0 < a k) :
    ∀ k : ℕ, 1 ≤ k →
      0 < ((k : ℝ) * a k) / ((a k)^2 + (k : ℝ) - 1) := by
  intro k hk
  have hk_nat_pos : 0 < k := lt_of_lt_of_le Nat.zero_lt_one hk
  have hk_real_pos : 0 < (k : ℝ) := by
    exact_mod_cast hk_nat_pos
  have hak_pos : 0 < a k := hpos k hk
  have hnum_pos : 0 < (k : ℝ) * a k := mul_pos hk_real_pos hak_pos
  exact div_pos hnum_pos (imo2015_shortlist_a1_den_pos a hpos k hk)


-- Step 2: the recurrence implies the local lower bound that will telescope later.
-- Starting from
--   a (k + 1) ≥ k * a k / ((a k)^2 + k - 1),
-- positivity lets us compare reciprocals/algebraically rearrange to get
--   a k ≥ k / a (k + 1) - (k - 1) / a k.
lemma imo2015_shortlist_a1_local_bound
    (a : ℕ → ℝ)
    (hpos : ∀ k : ℕ, 1 ≤ k → 0 < a k)
    (hrec : ∀ k : ℕ, 1 ≤ k →
      a (k + 1) ≥ ((k : ℝ) * a k) / ((a k)^2 + (k : ℝ) - 1)) :
    ∀ k : ℕ, 1 ≤ k →
      a k ≥ (k : ℝ) / a (k + 1) - ((k : ℝ) - 1) / a k := by
  intro k hk

  have hak_pos : 0 < a k := hpos k hk
  have hak_ne : a k ≠ 0 := ne_of_gt hak_pos

  have hsucc_pos : 0 < a (k + 1) :=
    imo2015_shortlist_a1_pos_succ a hpos k

  have hden_pos : 0 < (a k)^2 + (k : ℝ) - 1 :=
    imo2015_shortlist_a1_den_pos a hpos k hk

  -- Clear the positive denominator in the recurrence.
  have hmul :
      (k : ℝ) * a k ≤
        a (k + 1) * ((a k)^2 + (k : ℝ) - 1) := by
    have hrec_k :
        ((k : ℝ) * a k) / ((a k)^2 + (k : ℝ) - 1)
          ≤ a (k + 1) := by
      exact hrec k hk
    exact (div_le_iff₀ hden_pos).mp hrec_k

  -- Divide the cleared inequality by the positive quantities `a (k + 1)` and `a k`.
  have hdiv :
      (k : ℝ) / a (k + 1) ≤
        ((a k)^2 + (k : ℝ) - 1) / a k := by
    rw [div_le_div_iff₀ hsucc_pos hak_pos]
    nlinarith [hmul]

  -- Simplify the right-hand side:
  --   ((a k)^2 + k - 1) / a k = a k + (k - 1) / a k.
  have hsplit :
      ((a k)^2 + (k : ℝ) - 1) / a k =
        a k + ((k : ℝ) - 1) / a k := by
    field_simp [hak_ne]
    ring

  have hmain :
      (k : ℝ) / a (k + 1) ≤
        a k + ((k : ℝ) - 1) / a k := by
    simpa [hsplit] using hdiv
  
  linarith



-- Step 3: summing the local lower bound over k = 1, ..., m.
-- This does not telescope yet; it only converts the pointwise inequality
-- into a finite-sum inequality.
lemma imo2015_shortlist_a1_sum_local_bound
    (a : ℕ → ℝ)
    (hlocal_bound : ∀ k : ℕ, 1 ≤ k →
      a k ≥ (k : ℝ) / a (k + 1) - ((k : ℝ) - 1) / a k) :
    ∀ m : ℕ,
      (∑ k ∈ Finset.Icc 1 m, a k) ≥
        ∑ k ∈ Finset.Icc 1 m,
          ((k : ℝ) / a (k + 1) - ((k : ℝ) - 1) / a k) := by
  intro m
  refine Finset.sum_le_sum ?_
  intro k hk
  exact hlocal_bound k (Finset.mem_Icc.mp hk).1

-- Step 4: the finite sum on the right-hand side telescopes.
-- The identity is purely algebraic and does not need positivity assumptions.
lemma imo2015_shortlist_a1_telescoping_sum
    (a : ℕ → ℝ) :
    ∀ m : ℕ,
      (∑ k ∈ Finset.Icc 1 m,
          ((k : ℝ) / a (k + 1) - ((k : ℝ) - 1) / a k)) =
        (m : ℝ) / a (m + 1) := by
  intro m
  induction m with
  | zero =>
      simp
  | succ m ih =>
      rw [Finset.sum_Icc_succ_top (by omega : 1 ≤ m + 1)]
      rw [ih]
      have hcast : (((m + 1 : ℕ) : ℝ) - 1) = (m : ℝ) := by
        norm_num
      rw [hcast]
      ring

-- Step 4: combining the summed local lower bound with the telescoping identity.
lemma imo2015_shortlist_a1_sum_telescoped_bound
    (a : ℕ → ℝ)
    (hlocal_bound : ∀ k : ℕ, 1 ≤ k →
      a k ≥ (k : ℝ) / a (k + 1) - ((k : ℝ) - 1) / a k) :
    ∀ m : ℕ,
      (∑ k ∈ Finset.Icc 1 m, a k) ≥ (m : ℝ) / a (m + 1) := by
  intro m

  have hsum_local_bound :
      (∑ k ∈ Finset.Icc 1 m, a k) ≥
        ∑ k ∈ Finset.Icc 1 m,
          ((k : ℝ) / a (k + 1) - ((k : ℝ) - 1) / a k) :=
    imo2015_shortlist_a1_sum_local_bound a hlocal_bound m

  have htelescoping :
      (∑ k ∈ Finset.Icc 1 m,
          ((k : ℝ) / a (k + 1) - ((k : ℝ) - 1) / a k)) =
        (m : ℝ) / a (m + 1) :=
    imo2015_shortlist_a1_telescoping_sum a m

  simpa [htelescoping] using hsum_local_bound


-- Step 6 auxiliary real inequality: for every positive real x,
-- x + 1 / x is at least 2.
-- This is the AM-GM ingredient used in the base case and later again
-- in the small-final-term case.
lemma imo2015_shortlist_a1_pos_add_inv_ge_two
    {x : ℝ}
    (hx : 0 < x) :
    x + 1 / x ≥ 2 := by
  have hx_ne : x ≠ 0 := ne_of_gt hx

  have hnonneg : 0 ≤ (x - 1)^2 / x := by
    exact div_nonneg (sq_nonneg (x - 1)) (le_of_lt hx)

  have hidentity :
      (x - 1)^2 / x = x + 1 / x - 2 := by
    field_simp [hx_ne]
    ring

  have hmain : 0 ≤ x + 1 / x - 2 := by
    simpa [hidentity] using hnonneg

  linarith


-- Step 6: the base case n = 2.
-- From the recurrence at k = 1, we get a 2 ≥ 1 / a 1.
-- Since a 1 > 0, AM-GM gives a 1 + 1 / a 1 ≥ 2.
lemma imo2015_shortlist_a1_base_case
    (a : ℕ → ℝ)
    (hpos : ∀ k : ℕ, 1 ≤ k → 0 < a k)
    (hrec : ∀ k : ℕ, 1 ≤ k →
      a (k + 1) ≥ ((k : ℝ) * a k) / ((a k)^2 + (k : ℝ) - 1)) :
    (∑ k ∈ Finset.Icc 1 2, a k) ≥ (2 : ℝ) := by
  have ha1_pos : 0 < a 1 := hpos 1 (by norm_num)
  have ha1_ne : a 1 ≠ 0 := ne_of_gt ha1_pos

  have ha2_lower : a 2 ≥ 1 / a 1 := by
    have hrec_one :
        a 2 ≥
          ((1 : ℝ) * a 1) / ((a 1)^2 + (1 : ℝ) - 1) := by
      simpa using hrec 1 (by norm_num)

    have hrhs :
        ((1 : ℝ) * a 1) / ((a 1)^2 + (1 : ℝ) - 1) =
          1 / a 1 := by
      field_simp [ha1_ne]
      ring

    calc
      1 / a 1
          = ((1 : ℝ) * a 1) / ((a 1)^2 + (1 : ℝ) - 1) := hrhs.symm
      _ ≤ a 2 := hrec_one

  calc
    (∑ k ∈ Finset.Icc 1 2, a k)
        = a 1 + a 2 := by
            change (∑ k ∈ Finset.Icc 1 (1 + 1), a k) = a 1 + a 2
            rw [Finset.sum_Icc_succ_top]
            · simp
            · norm_num
    _ ≥ a 1 + 1 / a 1 := by
        linarith
    _ ≥ 2 :=
        imo2015_shortlist_a1_pos_add_inv_ge_two ha1_pos

-- Step 8: the induction step in the case where the new term is large.
-- If the previous partial sum is at least n and the new term a (n + 1)
-- is at least 1, then the partial sum up to n + 1 is at least n + 1.
lemma imo2015_shortlist_a1_large_final_term_case
    (a : ℕ → ℝ)
    (n : ℕ)
    (ih_n : (∑ k ∈ Finset.Icc 1 n, a k) ≥ (n : ℝ))
    (hlarge : a (n + 1) ≥ 1) :
    (∑ k ∈ Finset.Icc 1 (n + 1), a k) ≥ ((n + 1 : ℕ) : ℝ) := by
  calc
    (∑ k ∈ Finset.Icc 1 (n + 1), a k)
        = (∑ k ∈ Finset.Icc 1 n, a k) + a (n + 1) := by
            rw [Finset.sum_Icc_succ_top (by omega : 1 ≤ n + 1)]
    _ ≥ (n : ℝ) + 1 := by
        linarith
    _ = ((n + 1 : ℕ) : ℝ) := by
        simp


-- Step 9 auxiliary real inequality.
-- If 0 < x < 1 and n ≥ 2, then n / x + x ≥ n + 1.
-- This is the real-variable estimate used in the small-final-term case.
lemma imo2015_shortlist_a1_small_real_case
    (n : ℕ)
    {x : ℝ}
    (hn : 2 ≤ n)
    (hx_pos : 0 < x)
    (hx_small : x < 1) :
    (n : ℝ) / x + x ≥ ((n + 1 : ℕ) : ℝ) := by
  have hx_ne : x ≠ 0 := ne_of_gt hx_pos

  have hn_minus_nonneg : 0 ≤ (n : ℝ) - 1 := by
    have hn_one : (1 : ℕ) ≤ n := by
      omega
    have hn_one_real : (1 : ℝ) ≤ (n : ℝ) := by
      exact_mod_cast hn_one
    linarith

  have hamgm : x + 1 / x ≥ 2 :=
    imo2015_shortlist_a1_pos_add_inv_ge_two hx_pos

  have htail : ((n : ℝ) - 1) / x ≥ ((n : ℝ) - 1) := by
    rw [ge_iff_le]
    rw [le_div_iff₀ hx_pos]
    have hmul_le :
        ((n : ℝ) - 1) * x ≤ ((n : ℝ) - 1) * 1 := by
      exact mul_le_mul_of_nonneg_left (le_of_lt hx_small) hn_minus_nonneg
    nlinarith

  have hdecomp :
      (n : ℝ) / x + x =
        (x + 1 / x) + ((n : ℝ) - 1) / x := by
    field_simp [hx_ne]
    ring

  have htarget :
      (2 : ℝ) + ((n : ℝ) - 1) = ((n + 1 : ℕ) : ℝ) := by
    have hcast : ((n + 1 : ℕ) : ℝ) = (n : ℝ) + 1 := by
      norm_num
    rw [hcast]
    ring

  calc
    (n : ℝ) / x + x
        = (x + 1 / x) + ((n : ℝ) - 1) / x := hdecomp
    _ ≥ 2 + ((n : ℝ) - 1) := by
        linarith
    _ = ((n + 1 : ℕ) : ℝ) := htarget


-- Step 9: the induction step in the case where the new term is small.
-- If 0 < a (n + 1) < 1, then the telescoped lower bound gives
--   sum_{k=1}^n a k ≥ n / a (n + 1).
-- Adding a (n + 1) and applying the small real inequality gives
--   sum_{k=1}^{n+1} a k ≥ n + 1.
lemma imo2015_shortlist_a1_small_final_term_case
    (a : ℕ → ℝ)
    (n : ℕ)
    (hn : 2 ≤ n)
    (hsum_telescoped_bound : ∀ m : ℕ,
      (∑ k ∈ Finset.Icc 1 m, a k) ≥ (m : ℝ) / a (m + 1))
    (hsucc_pos : 0 < a (n + 1))
    (hsmall : a (n + 1) < 1) :
    (∑ k ∈ Finset.Icc 1 (n + 1), a k) ≥ ((n + 1 : ℕ) : ℝ) := by
  have hsum_n :
      (∑ k ∈ Finset.Icc 1 n, a k) ≥ (n : ℝ) / a (n + 1) :=
    hsum_telescoped_bound n

  have hreal :
      (n : ℝ) / a (n + 1) + a (n + 1) ≥ ((n + 1 : ℕ) : ℝ) :=
    imo2015_shortlist_a1_small_real_case n hn hsucc_pos hsmall

  calc
    (∑ k ∈ Finset.Icc 1 (n + 1), a k)
        = (∑ k ∈ Finset.Icc 1 n, a k) + a (n + 1) := by
            rw [Finset.sum_Icc_succ_top (by omega : 1 ≤ n + 1)]
    _ ≥ (n : ℝ) / a (n + 1) + a (n + 1) := by
        linarith
    _ ≥ ((n + 1 : ℕ) : ℝ) := hreal



-- IMO 2015 Shortlist A1.
-- A positive real sequence satisfies the recurrence lower bound
--   a (k + 1) ≥ k * a k / ((a k)^2 + k - 1)
-- for every k ≥ 1. The goal is to prove that every partial sum from
-- index 1 to n is at least n, for all n ≥ 2.

theorem imo2015_shortlist_a1
    (a : ℕ → ℝ)
    (hpos : ∀ k : ℕ, 1 ≤ k → 0 < a k)
    (hrec : ∀ k : ℕ, 1 ≤ k →
      a (k + 1) ≥ ((k : ℝ) * a k) / ((a k)^2 + (k : ℝ) - 1))
    (n : ℕ)
    (hn : 2 ≤ n) :
    (∑ k ∈ Finset.Icc 1 n, a k) ≥ (n : ℝ) := by

  have hlocal_bound : ∀ k : ℕ, 1 ≤ k →
      a k ≥ (k : ℝ) / a (k + 1) - ((k : ℝ) - 1) / a k :=
    imo2015_shortlist_a1_local_bound a hpos hrec

  have hsum_telescoped_bound : ∀ m : ℕ,
      (∑ k ∈ Finset.Icc 1 m, a k) ≥ (m : ℝ) / a (m + 1) :=
    imo2015_shortlist_a1_sum_telescoped_bound a hlocal_bound

  -- Step 5: set up the induction statement.
  -- The remaining proof is now split into a base case n = 2
  -- and an induction step from n to n + 1.
  have hind : ∀ n : ℕ, 2 ≤ n →
      (∑ k ∈ Finset.Icc 1 n, a k) ≥ (n : ℝ) := by
    intro n
    induction n with
    | zero =>
        intro hn
        omega
    | succ n ih =>
        intro hn
        by_cases hn_base : n = 1
        · subst n
          -- Step 6 will prove the base case.
          have hbase :
              (∑ k ∈ Finset.Icc 1 2, a k) ≥ (2 : ℝ) := by
            exact imo2015_shortlist_a1_base_case a hpos hrec
          simpa using hbase
        · have hn_ge_two : 2 ≤ n := by
            omega

          have ih_n :
              (∑ k ∈ Finset.Icc 1 n, a k) ≥ (n : ℝ) :=
            ih hn_ge_two

          -- Step 7: set up the induction step.
          -- We know the induction hypothesis up to `n`.
          -- Now split according to whether the new term `a (n + 1)` is at least 1.
          have hstep :
              (∑ k ∈ Finset.Icc 1 (n + 1), a k) ≥ ((n + 1 : ℕ) : ℝ) := by

            have hsucc_pos : 0 < a (n + 1) := by
              exact imo2015_shortlist_a1_pos_succ a hpos n

            by_cases hlarge : a (n + 1) ≥ 1
            · -- Step 8 will close the case `a (n + 1) ≥ 1`
              -- using the induction hypothesis and the decomposition of the sum.
              exact imo2015_shortlist_a1_large_final_term_case a n ih_n hlarge
            · -- Step 9 will close the case `a (n + 1) < 1`
              -- using the telescoped lower bound and the AM-GM inequality.
              have hsmall : a (n + 1) < 1 := by
                exact lt_of_not_ge hlarge
              exact imo2015_shortlist_a1_small_final_term_case
                a n hn_ge_two hsum_telescoped_bound hsucc_pos hsmall
          exact hstep

  exact hind n hn
  
