noncomputable section

/-- Sum of the entries at even, zero-based indices. -/
def firstPlayerScore (pieces : List ℝ) : ℝ :=
  (List.mapIdx
    (fun i x => if i % 2 = 0 then x else 0)
    pieces).sum

/-- Liu's score after the pieces are sorted in nonincreasing order. -/
def liuScore (pieces : List ℝ) : ℝ :=
  firstPlayerScore
    (pieces.mergeSort (fun x y => decide (x ≥ y)))

/-- Liu partitions the unit stick into at most `n + 1` positive pieces. -/
def LiuMove (n : ℕ) (pieces : List ℝ) : Prop :=
  (∀ x ∈ pieces, 0 < x) ∧
  pieces.sum = 1 ∧
  pieces.length ≤ n + 1

/-- `pieces` subdivides a piece of length `x`. -/
def SplitsInto (x : ℝ) (pieces : List ℝ) : Prop :=
  (∀ y ∈ pieces, 0 < y) ∧
  pieces.sum = x

/--
Xiang refines each of Liu's pieces and makes at most `n`
additional cuts.
-/
def XiangMove
    (n : ℕ)
    (liuPieces : List ℝ)
    (blocks : List (List ℝ)) : Prop :=
  List.Forall₂ SplitsInto liuPieces blocks ∧
  blocks.flatten.length ≤ liuPieces.length + n

/-- Liu can guarantee a score of at least `c`. -/
def CanGuarantee (n : ℕ) (c : ℝ) : Prop :=
  ∃ liuPieces : List ℝ,
    LiuMove n liuPieces ∧
    ∀ blocks : List (List ℝ),
      XiangMove n liuPieces blocks →
      c ≤ liuScore blocks.flatten

end
