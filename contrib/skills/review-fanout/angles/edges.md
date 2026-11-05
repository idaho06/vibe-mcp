Look for inputs and conditions the code does not handle.

For each changed function, list the unusual cases and check whether each one is handled:

- empty, very large, negative, zero, missing or `None` input; duplicates; unexpected types;
- text encodings, time zones, locale, path separators, platform differences;
- error paths: is the error reported, retried, or silently lost;
- resource exhaustion: big files, many items, long strings, deep recursion.

Report only the cases the code gets wrong, with the exact input that breaks it.
