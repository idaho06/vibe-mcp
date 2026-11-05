Find code that says the same thing more than once.

- Copy-paste inside the target, even when names or constants differ slightly.
- New code that re-implements something the project already has. Search the rest of the project
  (`grep -n`, and the file names of utility or shared modules) before you decide something is new,
  and name the existing function to call instead.
- Hand-written versions of what the language's standard library or an installed dependency already
  does.
- The same constant, string or condition written out in several places.

For each one, say where every copy lives, which should survive, and what replaces the others.
