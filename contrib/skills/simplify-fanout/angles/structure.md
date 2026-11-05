Find structure that makes the code harder to read than it needs to be.

- Deep nesting that a guard clause or an early return would flatten, and conditions that can be
  merged or inverted.
- Functions that do several unrelated things, or are long enough that the reader loses the thread.
- Names that hide intent.
- Abstractions with a single caller, layers of indirection that add nothing, and parameters that only
  exist to be passed along.
- Special cases bolted on top of general code, where one more general rule would absorb them.

For each one, describe the simpler shape in a sentence. Do not propose clever or dense rewrites:
explicit code beats short code.
