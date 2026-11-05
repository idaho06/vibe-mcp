Look for defects that depend on when things happen or on what was left behind.

- Races: two tasks, threads or processes touching the same state, check-then-act sequences.
- Ordering: code that assumes one step finished before another began.
- Initialization: use of state before it is set, or set twice.
- Lifetimes: files, locks, connections and processes that are not released, especially on the error
  path and on cancellation.
- Partial failure and retries: what remains if the third of five steps fails; whether running the
  operation twice is safe.

Report the sequence of events that triggers the defect, step by step.
