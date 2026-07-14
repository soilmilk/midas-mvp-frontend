import Mathlib
import Aesop

set_option maxHeartbeats 0

open BigOperators Real Nat Topology Rat

-- Polynomial evaluation preserves congruence.
lemma polynomial_eval_modEq
    (f : Polynomial ℕ) {a b m : ℕ}
    (hab : a ≡ b [MOD m]) :
    f.eval a ≡ f.eval b [MOD m] := by
  refine Polynomial.induction_on' f ?_ ?_
  · intro p q hp hq
    simpa only [Polynomial.eval_add] using hp.add hq
  · intro k c
    simpa only [Polynomial.eval_monomial] using
      (Nat.ModEq.refl c).mul (Nat.ModEq.pow k hab)

-- Polynomials with natural coefficients are monotone on ℕ.
lemma polynomial_eval_mono
    (f : Polynomial ℕ) {a b : ℕ}
    (hab : a ≤ b) :
    f.eval a ≤ f.eval b := by
  refine Polynomial.induction_on' f ?_ ?_
  · intro p q hp hq
    simpa only [Polynomial.eval_add] using Nat.add_le_add hp hq
  · intro k c
    simpa only [Polynomial.eval_monomial] using
      Nat.mul_le_mul_left c (Nat.pow_le_pow_left hab k)

-- A positive constant coefficient makes every evaluation positive.
lemma polynomial_eval_pos
    (f : Polynomial ℕ)
    (hconstant : 0 < f.coeff 0)
    (n : ℕ) :
    0 < f.eval n := by
  have hdecomp :
      f.coeff 0 + (f.erase 0).eval n = f.eval n := by
    simpa only [Polynomial.eval_add, Polynomial.eval_monomial,
      pow_zero, mul_one] using
      congrArg (Polynomial.eval n) (f.monomial_add_erase 0)
  omega

-- A nonconstant polynomial with a positive leading coefficient
-- is strictly increasing from 1 onward.
lemma polynomial_eval_one_lt
    (f : Polynomial ℕ)
    (hdeg : 0 < f.natDegree)
    (hcoeff : ∀ k ≤ f.natDegree, 0 < f.coeff k)
    {n : ℕ}
    (hn : 1 < n) :
    f.eval 1 < f.eval n := by
  let d := f.natDegree

  have hd : 0 < d := by
    simpa [d] using hdeg

  have hc : 0 < f.coeff d := by
    apply hcoeff
    simp [d]

  have hpow : 1 ^ d < n ^ d :=
    Nat.pow_lt_pow_left hn (Nat.ne_of_gt hd)

  have hleading :
      f.coeff d * 1 ^ d < f.coeff d * n ^ d :=
    Nat.mul_lt_mul_of_pos_left hpow hc

  have herase :
      (f.erase d).eval 1 ≤ (f.erase d).eval n :=
    polynomial_eval_mono (f.erase d) (Nat.le_of_lt hn)

  have hdecomp_one :
      f.coeff d * 1 ^ d + (f.erase d).eval 1 = f.eval 1 := by
    simpa only [Polynomial.eval_add, Polynomial.eval_monomial] using
      congrArg (Polynomial.eval 1) (f.monomial_add_erase d)

  have hdecomp_n :
      f.coeff d * n ^ d + (f.erase d).eval n = f.eval n := by
    simpa only [Polynomial.eval_add, Polynomial.eval_monomial] using
      congrArg (Polynomial.eval n) (f.monomial_add_erase d)

  omega

-- Since f(a) + a is congruent to a modulo f(a), evaluating f
-- preserves that congruence.
lemma eval_dvd_eval_self_add
    (f : Polynomial ℕ) (a : ℕ) :
    f.eval a ∣ f.eval (f.eval a + a) := by
  have hinput :
      f.eval a + a ≡ a [MOD f.eval a] :=
    Nat.add_modEq_left
  have heval :
      f.eval (f.eval a + a) ≡ f.eval a [MOD f.eval a] :=
    polynomial_eval_modEq f hinput
  exact (heval.dvd_iff (dvd_refl _)).mpr (dvd_refl _)

theorem main
    (f : Polynomial ℕ)
    (hdeg : 0 < f.natDegree)
    (hcoeff : ∀ k ≤ f.natDegree, 0 < f.coeff k)
    (n : ℕ)
    (hn : 0 < n) :
    (f.eval n ∣ f.eval (f.eval n + 1)) ↔ n = 1 := by
  constructor
  · intro hdvd

    have hinput :
        f.eval n + 1 ≡ 1 [MOD f.eval n] :=
      Nat.add_modEq_left

    have heval :
        f.eval (f.eval n + 1) ≡ f.eval 1 [MOD f.eval n] :=
      polynomial_eval_modEq f hinput

    have hdiv_one : f.eval n ∣ f.eval 1 :=
      (heval.dvd_iff (dvd_refl _)).mp hdvd

    have hconstant : 0 < f.coeff 0 :=
      hcoeff 0 (Nat.zero_le f.natDegree)

    have heval_one_pos : 0 < f.eval 1 :=
      polynomial_eval_pos f hconstant 1

    have heval_le : f.eval n ≤ f.eval 1 :=
      Nat.le_of_dvd heval_one_pos hdiv_one

    by_contra hn_ne_one
    have hn_gt_one : 1 < n := by omega
    have heval_lt : f.eval 1 < f.eval n :=
      polynomial_eval_one_lt f hdeg hcoeff hn_gt_one
    omega

  · rintro rfl
    exact eval_dvd_eval_self_add f 1