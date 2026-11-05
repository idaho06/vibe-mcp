Find code that carries no weight.

- Unused imports, variables, parameters, fields, functions, and branches that can never run. Search
  for uses across the whole project before you call something unused.
- State that can be computed from other state, or that is only ever set to the same value.
- Defensive checks for situations that cannot happen given the callers you can see.
- Comments that only restate the code, and commented-out code.
- Leftovers: debug output, and notes about work that has been done.

For each one, say exactly what can be deleted and what you checked to be sure it is unused.
