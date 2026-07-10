import Mathlib
set_option maxHeartbeats 400000

-- (none needed for this step)

-- (none needed for this step)

-- no new declarations needed for this step

-- no new declarations needed for this step

-- no new declarations needed for this step

-- no new declarations needed for this step

-- no new declarations needed for this step

-- no new declarations needed for this step

-- no new declarations needed for this step

-- no new declarations needed for this step

-- no new declarations needed for this step

-- no new declarations needed for this step

-- no new declarations needed for this step

theorem main (n : ℤ) : (30 : ℤ) ∣ n ^ 5 - n := by
  have h : n ^ 5 - n = n * (n - 1) * (n + 1) * (n ^ 2 + 1) := by ring
  suffices hdiv : (30 : ℤ) ∣ n * (n - 1) * (n + 1) * (n ^ 2 + 1) by
    simpa [h] using hdiv
  show (30 : ℤ) ∣ (n * (n - 1) * (n + 1)) * (n ^ 2 + 1)
  have hcase : ∃ k : ℤ, n = 2 * k ∨ n = 2 * k + 1 := ⟨n / 2, by omega⟩
  have h2 : (2 : ℤ) ∣ n * (n - 1) * (n + 1) := by
    obtain ⟨k, hk | hk⟩ := hcase
    · exact ⟨k * (n - 1) * (n + 1), by rw [hk]; ring⟩
    · exact ⟨n * k * (n + 1), by
        have hk1 : n - 1 = 2 * k := by omega
        rw [hk1]; ring⟩
  have h3 : (3 : ℤ) ∣ n * (n - 1) * (n + 1) := by
    have hcase3 : ∃ k : ℤ, n = 3 * k ∨ n = 3 * k + 1 ∨ n = 3 * k + 2 := ⟨n / 3, by omega⟩
    obtain ⟨k, hk0 | hk1 | hk2⟩ := hcase3
    · exact ⟨k * (n - 1) * (n + 1), by rw [hk0]; ring⟩
    · have hnm1 : n - 1 = 3 * k := by omega
      exact ⟨n * k * (n + 1), by rw [hnm1]; ring⟩
    · have hnp1 : n + 1 = 3 * (k + 1) := by omega
      exact ⟨n * (n - 1) * (k + 1), by rw [hnp1]; ring⟩
  have h5 : (5 : ℤ) ∣ n * (n - 1) * (n + 1) * (n ^ 2 + 1) := by
    have hcase5 :
        ∃ k : ℤ,
          n = 5 * k ∨ n = 5 * k + 1 ∨ n = 5 * k + 2 ∨ n = 5 * k + 3 ∨ n = 5 * k + 4 := ⟨n / 5, by omega⟩
    obtain ⟨k, hk0 | hk1 | hk2 | hk3 | hk4⟩ := hcase5
    · exact ⟨k * (n - 1) * (n + 1) * (n ^ 2 + 1), by rw [hk0]; ring⟩
    · have hnm1 : n - 1 = 5 * k := by omega
      exact ⟨n * k * (n + 1) * (n ^ 2 + 1), by rw [hnm1]; ring⟩
    · have hsq : n ^ 2 + 1 = 5 * (5 * k ^ 2 + 4 * k + 1) := by rw [hk2]; ring
      exact ⟨n * (n - 1) * (n + 1) * (5 * k ^ 2 + 4 * k + 1), by rw [hsq]; ring⟩
    · have hsq : n ^ 2 + 1 = 5 * (5 * k ^ 2 + 6 * k + 2) := by rw [hk3]; ring
      exact ⟨n * (n - 1) * (n + 1) * (5 * k ^ 2 + 6 * k + 2), by rw [hsq]; ring⟩
    · have hnp1 : n + 1 = 5 * (k + 1) := by omega
      exact ⟨n * (n - 1) * (k + 1) * (n ^ 2 + 1), by rw [hnp1]; ring⟩
  have h6 : ((2 : ℤ) * 3) ∣ n * (n - 1) * (n + 1) := by
    have hcase6 :
        ∃ k : ℤ,
          n = 6 * k ∨ n = 6 * k + 1 ∨ n = 6 * k + 2 ∨ n = 6 * k + 3 ∨ n = 6 * k + 4 ∨ n = 6 * k + 5 :=
      ⟨n / 6, by omega⟩
    obtain ⟨k, hk0 | hk1 | hk2 | hk3 | hk4 | hk5⟩ := hcase6
    · exact ⟨k * (n - 1) * (n + 1), by rw [hk0]; ring⟩
    · exact ⟨n * k * (n + 1), by rw [hk1]; ring⟩
    · exact ⟨(3 * k + 1) * (n - 1) * (2 * k + 1), by rw [hk2]; ring⟩
    · exact ⟨(2 * k + 1) * (3 * k + 1) * (n + 1), by rw [hk3]; ring⟩
    · exact ⟨(3 * k + 2) * (2 * k + 1) * (n + 1), by rw [hk4]; ring⟩
    · exact ⟨n * (n - 1) * (k + 1), by rw [hk5]; ring⟩
  have h6' : ((2 : ℤ) * 3) ∣ (n * (n - 1) * (n + 1)) * (n ^ 2 + 1) :=
    dvd_mul_of_dvd_left h6 (n ^ 2 + 1)
  have hcop : IsCoprime ((2 : ℤ) * 3) (5 : ℤ) := by
    refine ⟨1, -1, ?_⟩
    ring
  have h30' : ((2 : ℤ) * 3 * 5) ∣ (n * (n - 1) * (n + 1)) * (n ^ 2 + 1) :=
    hcop.mul_dvd h6' h5
  have h30eq : ((2 : ℤ) * 3 * 5 : ℤ) = 30 := by norm_num
  have h30 : (30 : ℤ) ∣ (n * (n - 1) * (n + 1)) * (n ^ 2 + 1) := by
    simpa [h30eq] using h30'
  exact h30
