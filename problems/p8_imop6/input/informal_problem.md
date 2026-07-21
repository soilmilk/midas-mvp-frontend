Let a:N→N be an infinite sequence, indexed starting from 0. Assume that:

Every term is greater than 1:

a(n) > 1 for every natural number n.

Each next term is greater than the current term:

a(n+1) > a(n).

Each next term has greatest common divisor greater than 1 with every term already chosen:

gcd(a(n+1),a(i)) > 1 whenever i less or equals to n.

The next term is the smallest natural number satisfying these conditions. More precisely, if m > a(n) and

gcd(m,a(i)) > 1for every i less or equals to n,

then

a(n+1) is less or equals to m.

Prove that there exist positive natural numbers T and L such that

a(n+T) = a(n) + L

for every natural number n.