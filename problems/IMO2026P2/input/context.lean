/-- The Euclidean plane. -/
abbrev Point := EuclideanSpace ℝ (Fin 2)

/-- The two-dimensional determinant of two vectors. -/
def cross2 (u v : Point) : ℝ :=
  u 0 * v 1 - u 1 * v 0

/-- Three points are noncollinear. -/
def NonCollinear3 (A B C : Point) : Prop :=
  cross2 (B - A) (C - A) ≠ 0

/--
`P` lies strictly inside triangle `ABC`.

For a nondegenerate triangle, this is expressed by strictly positive
barycentric coordinates whose sum is one.
-/
def StrictInsideTriangle (P A B C : Point) : Prop :=
  NonCollinear3 A B C ∧
  ∃ x y z : ℝ,
    0 < x ∧
    0 < y ∧
    0 < z ∧
    x + y + z = 1 ∧
    P = x • A + y • B + z • C

/--
`O` is the circumcentre of the nondegenerate triangle `ABC`.
-/
def IsCircumcenter3 (O A B C : Point) : Prop :=
  NonCollinear3 A B C ∧
  dist O A = dist O B ∧
  dist O A = dist O C
