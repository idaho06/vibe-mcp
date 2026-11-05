# vibe-mcp

An [MCP](https://modelcontextprotocol.io) server that lets Claude Code hand well-specified coding
work (implement, simplify, mechanical refactors) to the
[Mistral Vibe](https://github.com/mistralai/mistral-vibe) CLI, so the expensive model plans and
reviews while a cheaper one writes the code.

- **Asynchronous.** `vibe_submit` returns at once; Claude keeps working and calls `vibe_wait`.
- **Isolated.** Every job runs in its own git worktree and branch, inside a bubblewrap sandbox.
- **Compact.** Results are short plain text (summary, diffstat, cost). The full diff is on request.
- **Safe to merge.** `vibe_apply` stages a job's changes in your checkout and never commits.

> **Status:** unit-tested against a fake sandbox (no network, no cost), and smoke-tested against
> real Vibe 3.1.0 with the reference sandbox by a script driving the server over stdio: parallel
> jobs, apply, conflict report, cancel (no processes left behind), writes outside the worktree,
> and serial chaining. It has not been tried inside a live Claude Code session yet, so how Claude
> Code sets the server's working directory and how long it lets `vibe_wait` block are unverified.

## Requirements

| You need | Why | Check |
|---|---|---|
| **Linux** | the reference sandbox uses bubblewrap | |
| **git 2.22+**, and a repo with **at least one commit** | each job is a worktree branched from `HEAD` | `vibe-mcp doctor` |
| **[uv](https://docs.astral.sh/uv/)** | installs this tool; it downloads Python 3.14 itself if you lack it | |
| **bubblewrap** (`bwrap`) with unprivileged user namespaces allowed | the sandbox; some distros restrict namespaces and need extra setup | `vibe-mcp doctor` |
| **Mistral Vibe**, installed with `uv tool install mistral-vibe` | the reference sandbox binds `~/.local/bin`, `~/.local/share/uv` and `~/.vibe`, so a Vibe installed another way (pipx, pip) is invisible inside it | `vibe --version` |
| **A Mistral login**: the system keyring (`vibe --setup`) or `MISTRAL_API_KEY` | Vibe calls the Mistral API | `vibe-mcp doctor` |
| for the keyring login: **`xdg-dbus-proxy`** and a running **Secret Service** (gnome-keyring, KeePassXC, KWallet) | the sandbox reaches the keyring through a filtered D-Bus proxy; `DBUS_SESSION_BUS_ADDRESS` must reach the server (often missing over SSH or in tmux; use the API key there) | `vibe-mcp doctor` |
| **`python3`** on the PATH | the reference sandbox uses it to read Vibe's export | |

Only Vibe 3.1.x has been tested. On the common distros the packages are called `bubblewrap` and
`xdg-dbus-proxy`.

## Install

```sh
# 1. Vibe, then log in (skip the login if you will use MISTRAL_API_KEY instead)
uv tool install mistral-vibe        # see Mistral's docs if the package name changes
vibe --setup

# 2. this project, and the reference sandbox from the same clone
git clone https://github.com/idaho06/vibe-mcp && cd vibe-mcp
install -m755 contrib/vibe-sandbox ~/.local/bin/
uv tool install .

# 3. check the machine; every failing line says how to fix it
vibe-mcp doctor

# 4. register the server with Claude Code for all your projects
claude mcp add --scope user vibe -- vibe-mcp
claude mcp list                     # should show `vibe` as connected
```

To update later: `git pull && uv tool install --force .`

If you use `MISTRAL_API_KEY`, the server process must receive it. Pass it when registering (see
`claude mcp add --help` for the environment-variable option) instead of relying on your shell.

### Using it

Start Claude Code **inside the repository you want to work on** (it needs at least one commit). The
server works on the git repository of its working directory and prints `serving git repo <path>` on
stderr at startup; check that line. Do not wrap the command in something that changes directory (for
example `uv run --directory`; use `uv run --project` instead), or it will serve the wrong repository.

Claude Code asks before using MCP tools. Allow the five tools (named like `mcp__vibe__vibe_submit`)
in its permission settings if you do not want a prompt for each call.

### Using a different sandbox

`sandbox_cmd` in the config can point to any wrapper that behaves like this. The `doctor` checks that
are specific to the reference sandbox are skipped for other commands.

- It is called as `vibe-sandbox RUN_DIR [vibe arguments...]` with the current directory inside a git repo.
- `RUN_DIR` is named `vibe-run.*`, and the wrapper makes Vibe write `RUN_DIR/export/export.json`
  (Vibe's `--output-dir RUN_DIR/export`).
- It exits with Vibe's exit code (0 finished, 3 stopped at a limit, anything else failed).
- It should let Vibe write only the current directory (the job's worktree) and the git directory.
- Keep `data_dir` short: the wrapper may create a Unix socket in `RUN_DIR` (108-character limit).

Read `contrib/vibe-sandbox` before you rely on it: it decides what Vibe can see and write.

## Tools

| Tool | Purpose |
|---|---|
| `vibe_submit(spec, title?, scope?, base_job?, max_turns?, time_limit?, max_price?)` | Queue a task and return its job id at once. `spec` must be complete and self-contained: Vibe sees neither the chat nor your project docs. |
| `vibe_wait(job_ids?, timeout=120, wait_all=false)` | Block until any (or all) of the jobs end, or `timeout` seconds pass. `timeout=0` is a status check. Default: all active jobs. |
| `vibe_result(job_id, diff=false)` | Vibe's own summary (unverified), diffstat, added files, scope violations, tokens, cost. `diff=true` adds the capped diff. |
| `vibe_cancel(job_id)` | Cancel a queued job or kill a running one. Its worktree is kept. |
| `vibe_apply(job_id, discard=false)` | Stage the job's changes in your checkout (`git merge --squash`, **no commit**). Conflicts are reported and nothing changes. `discard=true` removes the job's worktree and branch instead. |

Typical flow: `vibe_submit` (one or several jobs) → `vibe_wait` → `vibe_result` → review the diff →
`vibe_apply` → you run the tests and `git commit`.

### Scopes and parallel jobs

`scope` lists the repo-relative paths a job may touch. Jobs whose scopes overlap run one after
another; jobs with disjoint scopes run in parallel (up to `max_concurrent`). A job with no scope
counts as the whole repo. A waiting job reserves its scope, so it cannot be starved by later jobs.
After the run, changed files outside the scope are reported as `SCOPE VIOLATIONS` (not blocked).

`base_job` starts a job from another job's result. It waits until that job ends, and fails with a
clear message if that job failed, was cancelled, applied or discarded.

### Isolation modes

- `isolation = "worktree"` (default): every job starts from the current `HEAD`.
- `isolation = "serial"`: one job at a time, and each job starts from the latest finished job's
  branch, so work accumulates like a single shared worktree.

## Configuration

Optional file `~/.config/vibe-mcp/config.toml` (or the path in `$VIBE_MCP_CONFIG`). Unknown keys and
wrong types are rejected at startup.

| Key | Default | Meaning |
|---|---|---|
| `isolation` | `"worktree"` | `"worktree"` or `"serial"` |
| `max_concurrent` | `3` | parallel jobs (forced to 1 in serial mode) |
| `model` | `"mistral-large-4"` | Vibe model alias |
| `sandbox_cmd` | `"vibe-sandbox"` | sandbox wrapper command (may include arguments) |
| `data_dir` | `"~/.local/share/vibe-mcp"` | run directories and worktrees |
| `max_turns` | `200` | default per-job turn budget (per-call override) |
| `time_limit` | `1200` | default per-job time limit in seconds (per-call override) |
| `max_price` | `0` | default per-job price cap in dollars; 0 = no cap |
| `max_message_chars` | `2000` | cap on Vibe's final message in results |
| `max_diff_bytes` | `20000` | cap on the diff in results |

## Things to know

- **Vibe's code is unreviewed.** The server never runs tests or the code Vibe wrote. Read the diff
  (`vibe_result diff=true`), then run your own checks before committing.
- **Trust the diff, not Vibe's report.** Vibe can say it wrote files outside its worktree, such as
  into your checkout or another job's worktree. With the reference sandbox those writes land in a
  throwaway in-memory filesystem and vanish, but Vibe still reports success. Only the committed
  diff counts.
- **Every job has a cost floor.** A one-line fix cost about 33K tokens (mostly Vibe's cached system
  prompt) and $0.013 in a test with Vibe 3.1.0. Delegate medium and large tasks, not one-liners.
- **Jobs live with the server.** Closing Claude Code kills running jobs; queued and running jobs
  left behind show up as `lost` on the next start. Their worktrees are kept until you discard them.
- **Main checkout guard.** The sandbox must share the git directory, so a job could in principle
  move your branch. If your checkout's branch or `HEAD` changed while a job ran, the result starts
  with a `WARNING` (it also fires when you committed meanwhile).
- **Vibe's streaming output is undocumented.** It is only used for live progress and the final
  message; the outcome, cost and tokens come from Vibe's `export.json`.

## Measuring the savings

`vibe-mcp stats` (add `--json` for scripts) sums up every saved job: counts by state, total and
average cost, tokens and duration. To compare with another delegation setup, run the same tasks
through both and compare wall time and Vibe cost from `stats`, plus the Claude tokens in the session
transcripts.

## Development

```sh
uv sync
uv run pytest        # no network, no cost: uses tests/fake_sandbox.py
uv run ruff check && uv run ruff format --check
```

Tests drive a fake sandbox (`tests/fake_sandbox.py`) that takes JSON directives, so the whole
scheduler, git handling and MCP surface are covered without running Vibe.

## License

MIT
