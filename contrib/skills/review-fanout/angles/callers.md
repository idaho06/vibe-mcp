Check the contracts between the changed code and everything that uses it.

For every changed function, signature, return value, exported name or file format, find its callers
and users in the rest of the project (`grep -n`), and check that each still works:

- changed parameter types, order or meaning, new required parameters the callers do not pass;
- a renamed or removed name that is still used elsewhere;
- a changed return shape, or an error that used to be returned and is now raised (or the reverse);
- behavior that other code, tests or documentation rely on and the change removed.

Report the caller (file and line), and what it does that now breaks.
