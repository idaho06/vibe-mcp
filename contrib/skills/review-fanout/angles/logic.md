Read the target line by line, and read the whole function around every changed line: a defect in an
unchanged line of a touched function is in scope.

For every line ask what input or state makes it wrong. Look for:

- inverted or wrong conditions, off-by-one errors, operators with surprising precedence;
- the wrong variable (copy-paste), a result that is computed and then ignored;
- confusion between zero, empty, `None` and false;
- missing `await`, a function that should return a value but falls through;
- errors that are caught and swallowed where they should propagate.

Report only defects you can explain with a concrete input.
