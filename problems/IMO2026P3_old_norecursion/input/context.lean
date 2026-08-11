/-- `pieces` is a partition of a piece of length `x`. -/
def SplitsInto (x : ℝ) (pieces : List ℝ) : Prop :=
  (∀ y ∈ pieces, 0 < y) ∧
  pieces.sum = x

/-- Liu cuts the unit stick into at most `n + 1` pieces. -/
def LiuMove (n : ℕ) (pieces : List ℝ) : Prop :=
  SplitsInto 1 pieces ∧
  pieces.length ≤ n + 1

/--
Xiang refines each of Liu's pieces, using at most `n` further cuts.
-/
def XiangMove
    (n : ℕ)
    (liuPieces : List ℝ)
    (blocks : List (List ℝ)) : Prop :=
  List.Forall₂ SplitsInto liuPieces blocks ∧
  blocks.flatten.length ≤ liuPieces.length + n

inductive DraftTurn where
  | liu
  | xiang

/--
`DraftGuarantee turn pieces c` means that, from this position,
Liu can guarantee a total future length of at least `c`.

On Liu's turn he may choose any remaining piece.
On Xiang's turn the guarantee must survive every possible choice.
-/
inductive DraftGuarantee : DraftTurn → List ℝ → ℝ → Prop where
  | done (turn : DraftTurn) {c : ℝ} (hc : c ≤ 0) :
      DraftGuarantee turn [] c

  | liu (left right : List ℝ) (x c : ℝ)
      (hnext :
        DraftGuarantee .xiang (left ++ right) (c - x)) :
      DraftGuarantee .liu (left ++ (x :: right)) c

  | xiang {pieces : List ℝ} {c : ℝ}
      (hne : pieces ≠ [])
      (hnext :
        ∀ (left right : List ℝ) (x : ℝ),
          pieces = left ++ (x :: right) →
          DraftGuarantee .liu (left ++ right) c) :
      DraftGuarantee .xiang pieces c

/-- Liu has an initial marking guaranteeing at least `c`. -/
def CanGuarantee (n : ℕ) (c : ℝ) : Prop :=
  ∃ liuPieces : List ℝ,
    LiuMove n liuPieces ∧
    ∀ blocks : List (List ℝ),
      XiangMove n liuPieces blocks →
      DraftGuarantee .liu blocks.flatten c
