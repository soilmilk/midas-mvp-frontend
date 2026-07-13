import Mathlib
import Aesop
set_option maxHeartbeats 0
open BigOperators Real Nat Topology Rat

-- Factorization of n^5 - n
lemma n5_sub_n_factored (n : ℤ) : n ^ 5 - n = n * (n - 1) * (n + 1) * (n ^ 2 + 1) := by
  ring

-- 6 divides the product of three consecutive integers n-1, n, n+1
lemma six_dvd_n_pred_succ (n : ℤ) : (6 : ℤ) ∣ n * (n - 1) * (n + 1) := by
  have h2 : (2 : ℤ) ∣ n * (n - 1) * (n + 1) := by
    rcases Int.even_or_odd n with he | ho
    · obtain ⟨k, hk⟩ := he
      have hdvd : (2 : ℤ) ∣ n := ⟨k, by omega⟩
      exact (hdvd.mul_right (n - 1)).mul_right (n + 1)
    · obtain ⟨k, hk⟩ := ho
      have hdvd : (2 : ℤ) ∣ (n - 1) := ⟨k, by omega⟩
      exact (hdvd.mul_left n).mul_right (n + 1)
  have h3 : (3 : ℤ) ∣ n * (n - 1) * (n + 1) := by
    have hm : n % 3 = 0 ∨ n % 3 = 1 ∨ n % 3 = 2 := by omega
    rcases hm with h0 | h1 | h2
    · have hdvd : (3 : ℤ) ∣ n := Int.dvd_of_emod_eq_zero h0
      exact (hdvd.mul_right (n - 1)).mul_right (n + 1)
    · have hdvd : (3 : ℤ) ∣ (n - 1) := Int.dvd_of_emod_eq_zero (by omega)
      exact (hdvd.mul_left n).mul_right (n + 1)
    · have hdvd : (3 : ℤ) ∣ (n + 1) := Int.dvd_of_emod_eq_zero (by omega)
      exact hdvd.mul_left (n * (n - 1))
  have hcop : IsCoprime (2 : ℤ) (3 : ℤ) := ⟨-1, 1, by norm_num⟩
  have hfinal := hcop.mul_dvd h2 h3
  norm_num at hfinal
  exact hfinal

-- 5 divides the product n(n-1)(n+1)(n^2+1)
lemma five_dvd_prod (n : ℤ) : (5 : ℤ) ∣ n * (n - 1) * (n + 1) * (n ^ 2 + 1) := by
  have hm : n % 5 = 0 ∨ n % 5 = 1 ∨ n % 5 = 2 ∨ n % 5 = 3 ∨ n % 5 = 4 := by omega
  rcases hm with h0 | h1 | h2 | h3 | h4
  · obtain ⟨q, hq⟩ : ∃ q, n = 5 * q := ⟨n / 5, by omega⟩
    exact ⟨q * (5 * q - 1) * (5 * q + 1) * (25 * q ^ 2 + 1), by rw [hq]; ring⟩
  · obtain ⟨q, hq⟩ : ∃ q, n = 5 * q + 1 := ⟨n / 5, by omega⟩
    exact ⟨(5 * q + 1) * q * (5 * q + 2) * (25 * q ^ 2 + 10 * q + 2), by rw [hq]; ring⟩
  · obtain ⟨q, hq⟩ : ∃ q, n = 5 * q + 2 := ⟨n / 5, by omega⟩
    exact ⟨(5 * q + 2) * (5 * q + 1) * (5 * q + 3) * (5 * q ^ 2 + 4 * q + 1), by rw [hq]; ring⟩
  · obtain ⟨q, hq⟩ : ∃ q, n = 5 * q + 3 := ⟨n / 5, by omega⟩
    exact ⟨(5 * q + 3) * (5 * q + 2) * (5 * q + 4) * (5 * q ^ 2 + 6 * q + 2), by rw [hq]; ring⟩
  · obtain ⟨q, hq⟩ : ∃ q, n = 5 * q + 4 := ⟨n / 5, by omega⟩
    exact ⟨(5 * q + 4) * (5 * q + 3) * (q + 1) * (25 * q ^ 2 + 40 * q + 17), by rw [hq]; ring⟩

theorem main (n : ℤ) : (30 : ℤ) ∣ n ^ 5 - n := by
  have h : n ^ 5 - n = n * (n - 1) * (n + 1) * (n ^ 2 + 1) := n5_sub_n_factored n
  have h6 : (6 : ℤ) ∣ n * (n - 1) * (n + 1) := six_dvd_n_pred_succ n
  have h6' : (6 : ℤ) ∣ n * (n - 1) * (n + 1) * (n ^ 2 + 1) := h6.mul_right _
  have h5 : (5 : ℤ) ∣ n * (n - 1) * (n + 1) * (n ^ 2 + 1) := five_dvd_prod n
  have hcop : IsCoprime (6 : ℤ) (5 : ℤ) := ⟨1, -1, by norm_num⟩
  have hfinal := hcop.mul_dvd h6' h5
  norm_num at hfinal
  rw [h]
  exact hfinal
