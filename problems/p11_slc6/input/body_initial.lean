theorem main
    (tour : Fin 2025 ≃ GridCell45)
    (htour : IsEchidnaTour tour) :
    ∃ numbering : GridCell45 ≃ Fin 2025,
      (∀ c d : GridCell45,
        HorizontallyAdjacent c d →
        numbering c < numbering d →
        VisitedBefore tour d c) ∧
      (∀ c d : GridCell45,
        VerticallyAdjacent c d →
        numbering c < numbering d →
        VisitedBefore tour c d) := by
  sorry