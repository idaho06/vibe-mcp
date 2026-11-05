Find work that is done more often, or on more data, than necessary.

- The same value computed, read or fetched repeatedly where once would do: repeated file reads or
  queries, recomputation inside loops.
- Independent steps run one after another that could run together.
- Everything loaded or built when only part of it is needed.
- Collections, caches or logs that grow without a bound, and resources (files, connections,
  processes) that are never released.
- Slow work placed on a path that runs at startup or on every request.

Only report waste that matters for how the code is really used. For each one, say what it costs and
what the cheaper form is.
