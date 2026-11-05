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
> and serial chaining. It has also been used from a live Claude Code session on Linux: the server
> served the repo Claude Code was started in, the keyring login reached the jobs, and a cleanup, a
> review, `vibe_apply` and a four-minute job all worked. Only Vibe 3.1.x and `mistral-large-4` have
> been tried.

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
| `vibe_submit(spec, title?, scope?, base_job?, max_turns?, time_limit?, max_price?, skill?)` | Queue a task and return its job id at once. `spec` must be complete and self-contained: Vibe sees neither the chat nor your project docs. `skill` names a skill whose instructions go in front of the task (see [Skills](#skills-and-project-context)). |
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

## Skills and project context

A job runs in a fresh worktree, which holds only the files git tracks. Anything kept out of git
(a gitignored `.vibe/` folder, an untracked `AGENTS.md`) is missing there, and Vibe cannot see your
main checkout. Two features bring that material in. Both read from your **main checkout** when the job
is submitted, so they work even for files git ignores.

### Skills

A skill is a `SKILL.md` file of instructions. `vibe_submit(skill="simplify", spec="...")` puts the
skill's text (without its frontmatter) in front of your task. The server looks for it in:

1. `<repo>/.vibe/skills/<name>/SKILL.md` (per project; wins), then
2. `~/.vibe/skills/<name>/SKILL.md` (global).

These are Vibe's own skill folders, so the same files also work in interactive Vibe. A wrong name is
rejected at once with the list of available skills, and nothing is queued.

**Shipped skills.** [`contrib/skills`](contrib/skills) has two lightweight skills written for
unattended jobs. Install them once:

```sh
mkdir -p ~/.vibe/skills && cp -r contrib/skills/* ~/.vibe/skills/
```

- `simplify` cleans up the code your task names (reuse, dead code, nesting, wasted work) without
  changing behavior, and edits the files in place.
- `code-review` is read-only and reports real defects, each with the input that triggers it.

Both never ask questions, stay inside the scope the task names, run only the commands the task gives
them, and end with a short fixed-format report. They are single-agent: one session does the whole job.
For several reviewers working in parallel, see [Fan-out skills](#fan-out-skills-parallel-reviewers)
below. Write your own for anything else, or override these per project.

A third, `code-simplifier`, is in [`contrib/skills-apache`](contrib/skills-apache): an adaptation of
Anthropic's Apache-2.0 [code-simplifier](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/code-simplifier)
agent, kept under that license with a notice of what changed. It is also single-agent.

**Writing a skill.** Create `<name>/SKILL.md` with frontmatter holding a `name` (lowercase letters,
digits and hyphens) and a `description`, then the instructions. The frontmatter is YAML: **quote the
description if it contains `: `** (a colon followed by a space), or Vibe rejects the file. Remember the
job is headless: the skill should say not to ask questions, how to find its target, and how short the
final report must be, because Claude pays for every line of the result.

### Fan-out skills (parallel reviewers)

> **Experimental.** Tested with a fake sandbox; not yet measured against the real model for quality.

A skill whose folder has an `angles/` subfolder is run by the server as a team: one **read-only
reviewer per angle**, all in parallel, then **one merge job** that reads their reports. The model is
not asked to spawn anything itself. Claude sees a single job:

```
vibe_submit(skill="review-fanout", spec="Review the changes in HEAD~3..HEAD ...")   ->  job id
vibe_wait / vibe_result                                                              ->  one report
```

```
review-fanout/
  SKILL.md          instructions for the merge job (verify, merge, report)
  fanout.toml       optional: `readonly = true` if the merge job only reports
  angles/
    logic.md        what one reviewer looks for: one file per reviewer
    security.md
```

Angle names are the file names (letters, digits, hyphens and underscores). The server adds a standard
wrapper to every reviewer: read-only, never ask questions, do not carry out the task text (it only
names the target), and report findings in one fixed line format.

Two packs are shipped in [`contrib/skills`](contrib/skills) and installed by the `cp` above:

- `simplify-fanout`: four reviewers (`duplication`, `dead-weight`, `structure`, `wasted-work`). The
  merge job checks their findings against the code and applies the safe ones. `vibe_apply` works on
  it as on any other job.
- `review-fanout`: five reviewers (`logic`, `callers`, `state-and-timing`, `edges`, `security`). The
  merge job verifies each candidate, drops the ones the code refutes, and reports up to 12 findings.
  It is read-only, so there is nothing to apply.

How it behaves:

- **One commit for everyone.** The base is pinned to the commit that is `HEAD` when you submit, so the
  reviewers and the merge job see the same code even if you commit meanwhile. For the same reason
  `base_job` cannot be combined with a fan-out skill.
- **Reviewers are hidden.** `vibe_wait` without ids lists only the merge job, which shows progress
  such as `queued (reviewers 2/5 done)`. Asking for a reviewer by its id still works.
- **Failures are tolerated.** If some reviewers fail, the merge job still runs and its prompt names
  the missing angles. If all of them fail, the merge job fails with a message saying so. Cancelling the
  merge job cancels its reviewers.
- **Reviewers are read-only by instruction, not by the sandbox.** A reviewer runs in a throwaway
  detached worktree with no branch. If it changes files anyway, the changes are discarded and the job
  carries a warning. Its worktree is removed as soon as it ends.
- **Cost.** A fan-out is one session per reviewer plus the merge job, so the cost scales with the
  number of angles, and `vibe_result` shows `reviewers: 5 jobs, $… (merge $…, total $…)`. The limits
  `max_turns`, `time_limit` and `max_price` apply to each job, and `context_files` are sent to every
  one of them. `max_concurrent` (default 3) decides how many run at once, and `isolation = "serial"` runs
  them one after another. For a small change, a single-agent skill is much cheaper.

### Project context

`context_files` in the config lists repo files to put in front of every task, for example your coding
standards when `AGENTS.md` is not tracked in git:

```toml
context_files = ["AGENTS.md"]
```

Missing or empty files are skipped, and each file is cut at 50,000 characters. List only files Vibe
cannot see by itself: a tracked file is already in the worktree, and `AGENTS.md` is one Vibe reads on
its own. The default is empty, so specs stay self-contained unless you opt in.

### Naming the target of a cleanup or review

The worktree starts at `HEAD`, so uncommitted changes in your checkout are **not** in the job. Commit
first, then say exactly what to work on in the spec: "simplify the changes in `HEAD~3..HEAD`" or "review
`src/parser.py`". The shipped skills do nothing and say so when the task names no target.

### Cost

Every job pays Vibe's fixed per-session overhead, and a skill adds its own text (about 400 to 600
tokens for the shipped ones). In tests with Vibe 3.1.0 and `mistral-large-4` on a 45-line change, using
larger skills than the shipped ones, a review cost about $0.02 to $0.03 and a cleanup $0.04 to $0.06.
Vibe's cost report may not include subagents, so treat figures as lower bounds, and check
`vibe-mcp stats` on your own work.

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
| `context_files` | `[]` | repo files whose text is put in front of every task (see [Skills](#skills-and-project-context)) |

## Things to know

- **Long waits are fine in Claude Code.** It moves a tool call that runs longer than about 120 seconds
  to the background and tells Claude when it finishes. A `vibe_wait` on a four-minute job returned its
  result that way, as a notification, without an error. (Seen in one session; other clients may differ.)
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

MIT, except [`contrib/skills-apache/`](contrib/skills-apache), which is Apache-2.0 (see its README).
