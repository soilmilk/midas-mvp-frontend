/--
A nondegenerate triangle, represented only by its three angles.

The physical game depends only on the angles of the current triangle,
so side lengths and coordinates are omitted.
-/
structure TriangleAngles where
  a : ℝ
  b : ℝ
  c : ℝ
  a_pos : 0 < a
  b_pos : 0 < b
  c_pos : 0 < c
  angle_sum : a + b + c = Real.pi

/--
`LegalSplit T T₁ T₂` means that Mulan can cut the triangle `T`
so that the two possible remaining triangles are `T₁` and `T₂`.

The three disjuncts correspond to cutting from the vertices whose
angles are `T.a`, `T.b`, and `T.c`, respectively.
-/
def LegalSplit
    (T T₁ T₂ : TriangleAngles) : Prop :=
  (
    ∃ x : ℝ,
      0 < x ∧ x < T.a ∧
        T₁.a = x ∧
        T₁.b = T.b ∧
        T₁.c = T.c + (T.a - x) ∧
        T₂.a = T.a - x ∧
        T₂.b = T.c ∧
        T₂.c = T.b + x
  ) ∨
  (
    ∃ x : ℝ,
      0 < x ∧ x < T.b ∧
        T₁.a = x ∧
        T₁.b = T.c ∧
        T₁.c = T.a + (T.b - x) ∧
        T₂.a = T.b - x ∧
        T₂.b = T.a ∧
        T₂.c = T.c + x
  ) ∨
  (
    ∃ x : ℝ,
      0 < x ∧ x < T.c ∧
        T₁.a = x ∧
        T₁.b = T.a ∧
        T₁.c = T.b + (T.c - x) ∧
        T₂.a = T.c - x ∧
        T₂.b = T.b ∧
        T₂.c = T.a + x
  )

/-- The current triangle already contains an angle equal to `θ`. -/
def HasTargetAngle (θ : ℝ) (T : TriangleAngles) : Prop :=
  T.a = θ ∨ T.b = θ ∨ T.c = θ

/--
`MulanWins θ T` means that Mulan has a strategy which guarantees
reaching an angle equal to `θ` after finitely many cuts.

A proof of this predicate is a finite strategy tree:

* `terminal` represents a position in which Mulan has already won;
* `cut` represents one cut chosen by Mulan, together with winning
  strategies for both triangles that Shan-Yu might retain.
-/
inductive MulanWins (θ : ℝ) : TriangleAngles → Prop
  | terminal {T : TriangleAngles} :
      HasTargetAngle θ T →
      MulanWins θ T
  | cut {T T₁ T₂ : TriangleAngles} :
      LegalSplit T T₁ T₂ →
      MulanWins θ T₁ →
      MulanWins θ T₂ →
      MulanWins θ T

/--
Mulan can guarantee victory for the angle `θ`, regardless of
Shan-Yu's initial triangle.
-/
def MulanCanForceWin (θ : ℝ) : Prop :=
  ∀ T : TriangleAngles, MulanWins θ T
