noncomputable section


/-- A legal interior point of the unit stick. -/
abbrev CutPoint :=
  {x : ℝ // x ∈ Set.Ioo (0 : ℝ) 1}

/--
A marking using at most `n` interior points.

A `Finset` records distinct points, while its cardinality condition expresses
"at most `n`".
-/
structure Marking (n : ℕ) where
  points : Finset CutPoint
  card_le : points.card ≤ n

/-- Liu's and Xiang's marked points must be distinct from one another. -/
def Compatible {n : ℕ} (liu xiang : Marking n) : Prop :=
  Disjoint liu.points xiang.points

/-- All cut points after both players have marked the stick. -/
def combinedCuts {n : ℕ} (liu xiang : Marking n) : Finset CutPoint :=
  liu.points ∪ xiang.points

/--
The cut coordinates, arranged from left to right.

The coordinates are converted from the subtype `CutPoint` back to real
numbers after sorting.
-/
def sortedCutCoordinates (cuts : Finset CutPoint) : List ℝ :=
  (cuts.sort (fun x y => x ≤ y)).map (fun x => (x : ℝ))

/-- The ordered list consisting of `0`, all cuts, and `1`. -/
def partitionEndpoints (cuts : Finset CutPoint) : List ℝ :=
  (0 : ℝ) :: (sortedCutCoordinates cuts ++ [1])

/--
Given a left endpoint and the subsequent endpoints, form the lengths of the
successive intervals.
-/
def successiveDifferences (left : ℝ) : List ℝ → List ℝ
  | [] => []
  | right :: rest =>
      (right - left) :: successiveDifferences right rest

/-- The lengths of all pieces produced by the given cuts. -/
def pieceLengths (cuts : Finset CutPoint) : List ℝ :=
  match partitionEndpoints cuts with
  | [] => []
  | left :: rest => successiveDifferences left rest

/--
Pieces are indexed by their positions in the piece-length list.

Using indices rather than a `Finset ℝ` is essential: two physically distinct
pieces may have equal lengths.
-/
abbrev PieceIndex (cuts : Finset CutPoint) :=
  Fin (pieceLengths cuts).length

/-- The length of a particular indexed piece. -/
def pieceWeight (cuts : Finset CutPoint) (i : PieceIndex cuts) : ℝ :=
  (pieceLengths cuts).get i

inductive DraftPlayer
  | liu
  | xiang
  deriving DecidableEq

/--
`DraftCanGuarantee weight remaining turn target` means that, from the current
claiming-game position, Liu can ensure that the total length he receives from
now onward is at least `target`.

At Liu's turn, there must exist a move that preserves the guarantee.

At Xiang's turn, the guarantee must survive every move Xiang might choose.

When Liu claims a piece, its weight is subtracted from the amount he still
needs to secure.
-/
inductive DraftCanGuarantee
    {ι : Type*}
    [DecidableEq ι]
    (weight : ι → ℝ) :
    Finset ι → DraftPlayer → ℝ → Prop

  | finished
      (turn : DraftPlayer)
      {target : ℝ}
      (h_target : target ≤ 0) :
      DraftCanGuarantee weight ∅ turn target

  /--
  Liu chooses a particular remaining piece.

  The arguments `i` and `hi` directly carry the existential witness,
  rather than storing the recursive proof underneath `Exists`.
  -/
  | liuMove
      {remaining : Finset ι}
      {target : ℝ}
      (i : ι)
      (hi : i ∈ remaining)
      (h_next :
        DraftCanGuarantee
          weight
          (remaining.erase i)
          DraftPlayer.xiang
          (target - weight i)) :
      DraftCanGuarantee
        weight
        remaining
        DraftPlayer.liu
        target

  /--
  Xiang may choose any remaining piece, so Liu's guarantee must
  survive every legal choice.
  -/
  | xiangMoves
      {remaining : Finset ι}
      {target : ℝ}
      (h_nonempty : remaining.Nonempty)
      (h_next :
        ∀ i : ι,
          i ∈ remaining →
            DraftCanGuarantee
              weight
              (remaining.erase i)
              DraftPlayer.liu
              target) :
      DraftCanGuarantee
        weight
        remaining
        DraftPlayer.xiang
        target
/--
Liu can guarantee `target` in the claiming phase associated with these cuts,
starting with every piece unclaimed and Liu moving first.
-/
def claimingCanGuarantee
    (cuts : Finset CutPoint)
    (target : ℝ) :
    Prop :=
  DraftCanGuarantee
    (pieceWeight cuts)
    (Finset.univ : Finset (PieceIndex cuts))
    .liu
    target



/--
Liu can guarantee `target` in the complete game with marking limit `n`.

The quantifier order encodes the sequential marking stage:

* Liu first chooses his marking.
* Xiang then chooses any compatible marking after seeing Liu's choice.
* Liu may use a claiming strategy adapted to the resulting partition.
* Xiang may oppose that strategy at every one of his claiming turns.
-/
def LiuCanGuarantee (n : ℕ) (target : ℝ) : Prop :=
  ∃ liu : Marking n,
    ∀ xiang : Marking n,
      Compatible liu xiang →
        claimingCanGuarantee (combinedCuts liu xiang) target

end
