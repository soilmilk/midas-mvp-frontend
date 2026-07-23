Let a(n) :N→N be an infinite sequence (n greater or equals to 0). Assume that:

Every term is greater than 1:

a(n) > 1 for every natural number n.

Each next term is greater than the current term:

a(n+1) > a(n).

Each next term has greatest common divisor greater than 1 with every term already chosen:

gcd(a(n+1),a(i)) > 1 whenever i less or equals to n for every n.

The next term is the smallest natural number satisfying these conditions. More precisely, if m > a(n) and

gcd(m,a(i)) > 1 for every i less or equals to n,

then

a(n+1) is less or equals to m.

In other words, a(n+1) is the smallest natural number greater than a(n)	​that has a nontrivial common divisor with every preceding term a(0),…,a(n).

Prove that there exists positive natural numbers T and L such that

a(n+T) = a(n) + L

for every natural number n.

Equivalently, the sequence of consecutive differences is purely periodic. 