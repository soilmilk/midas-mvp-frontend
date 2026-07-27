-- A cell is represented by its row and column.
abbrev GridCell45 := Fin 45 × Fin 45

-- Two horizontally adjacent cells.
def HorizontallyAdjacent
    (c d : GridCell45) : Prop :=
  c.1 = d.1 ∧
    ((c.2 : ℕ) + 1 = (d.2 : ℕ) ∨
     (d.2 : ℕ) + 1 = (c.2 : ℕ))

-- Two vertically adjacent cells.
def VerticallyAdjacent
    (c d : GridCell45) : Prop :=
  c.2 = d.2 ∧
    ((c.1 : ℕ) + 1 = (d.1 : ℕ) ∨
     (d.1 : ℕ) + 1 = (c.1 : ℕ))

-- Two cells share a side.
def ShareSide
    (c d : GridCell45) : Prop :=
  HorizontallyAdjacent c d ∨
  VerticallyAdjacent c d

-- A tour visits every cell exactly once, with consecutive
-- visited cells sharing a side.
def IsEchidnaTour
    (tour : Fin 2025 ≃ GridCell45) : Prop :=
  ∀ k : Fin 2024,
    ShareSide
      (tour (Fin.castSucc k))
      (tour (Fin.succ k))

-- Cell c is visited before cell d.
def VisitedBefore
    (tour : Fin 2025 ≃ GridCell45)
    (c d : GridCell45) : Prop :=
  tour.symm c < tour.symm d