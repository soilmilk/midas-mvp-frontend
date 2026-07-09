-- reference
def andSelf (b : Bool) : Bool := b && b
theorem main : ∀ b, andSelf b = b := by decide
