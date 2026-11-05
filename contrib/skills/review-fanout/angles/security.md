Look for ways the changed code could be misused or could leak.

- Input that reaches a shell command, a SQL or query string, a file path or a template without being
  checked or escaped (injection, path traversal).
- Unsafe deserialization, and `eval`-like behavior on data from outside.
- Secrets, tokens or personal data written to code, logs, error messages or files.
- Missing checks that a caller is allowed to do what the code does.
- Input from outside the process trusted without validation.

Report the path the untrusted data takes, from where it enters to where it does harm.
