theorem main
    (A B C M N K L O : Point)
    (hABC : NonCollinear3 A B C)
    (hM : M = midpoint ℝ A B)
    (hN : N = midpoint ℝ A C)
    (hK_BMC : StrictInsideTriangle K B M C)
    (hL_BNC : StrictInsideTriangle L B N C)
    (hK_ABL : StrictInsideTriangle K A B L)
    (hL_AKC : StrictInsideTriangle L A K C)
    (hangle₁ :
      EuclideanGeometry.angle K B A =
        EuclideanGeometry.angle A C L)
    (hangle₂ :
      EuclideanGeometry.angle L B K =
        EuclideanGeometry.angle L N C)
    (hangle₃ :
      EuclideanGeometry.angle L C K =
        EuclideanGeometry.angle B M K)
    (hO : IsCircumcenter3 O A K L) :
    dist O M = dist O N := by
  sorry
