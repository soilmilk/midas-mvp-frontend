Two structurally different definitions of "double", and a function f:

  dblA n  — recursive: dblA 0 = 0, dblA (n+1) = dblA n + 2
  dblB n  — closed form: dblB n = n + n
  f n     — f n = n + 1

Prove that f agrees on the two doublings for every n:

  f (dblA n) = f (dblB n)   for all n.

(This needs a fact about the recursive dblA and a fact about the closed-form dblB,
combined at the end.)
