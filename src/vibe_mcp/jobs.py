"""The Job record and its persistence (one job.json per run directory)."""

import json
import logging
import os
import secrets
import time
from collections.abc import Collection
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from vibe_mcp.scope import Scope

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")
# Finished with for good: the branch and worktree are gone.
CLOSED = ("applied", "discarded")
# Jobs whose branch can be the base of a later job.
USABLE_BASE = ("finished", "limit")


def new_id(taken: Collection[str] = ()) -> str:
    """A random 6-hex-char id, different from every id in `taken`."""
    while (job_id := secrets.token_hex(3)) in taken:
        pass
    return job_id


def run_dir_for(data_dir: Path, job_id: str) -> Path:
    """Keep this short: vibe-sandbox puts a Unix socket (108-char limit) inside it."""
    return data_dir / "runs" / f"vibe-run.{job_id}"


@dataclass
class Job:
    id: str
    run_dir: Path
    spec: str
    repo: Path | None = None  # the git repo the job belongs to (data_dir is shared by all repos)
    title: str = ""
    skill: str | None = None  # name of the skill whose text is in `preamble`
    preamble: str = ""  # goes in front of `spec` in the prompt
    appendix: str = ""  # goes after `spec`; filled in when a fan-out merge job starts
    # fan-out: reviewers run read-only and in parallel, then one merge job collects their reports
    readonly: bool = False  # never commits, and does not queue behind jobs with overlapping scope
    hidden: bool = False  # a reviewer inside a fan-out: left out of vibe_wait's default listing
    depends_on: tuple[str, ...] = ()  # starts only once all of these jobs have ended
    parent: str | None = None  # the merge job a reviewer reports to
    angle: str | None = None  # the reviewer's angle
    reviewers_cost: float = 0.0  # total cost of the reviewers, recorded on the merge job
    scope: Scope = ()
    base_job: str | None = None
    base_ref: str | None = None  # resolved when the job starts
    max_turns: int = 200
    time_limit: int = 1200
    max_price: float = 0
    # queued running | finished limit failed cancelled lost | applied discarded
    state: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    ended_at: float | None = None
    pid: int | None = None
    worktree: Path | None = None
    commit: str | None = None  # tip of the job branch once its changes are committed
    # live progress, from the streaming output
    step: int = 0
    last_action: str = ""
    # outcome, from export.json
    exit_code: int | None = None
    stop_reason: str | None = None
    cost_usd: float = 0.0
    tokens: int = 0
    final_message: str = ""
    error: str | None = None
    warning: str | None = None

    @property
    def branch(self) -> str:
        return f"vibe/{self.id}"

    @property
    def prompt(self) -> str:
        """What Vibe is given: the skill (if any), the task, then any reviewers' findings."""
        return self.preamble + self.spec + self.appendix

    @property
    def active(self) -> bool:
        return self.state in ACTIVE

    def save(self) -> None:
        """Write job.json atomically."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.run_dir / "job.json.tmp"
        tmp.write_text(json.dumps(asdict(self), indent=1, default=str))  # Paths -> str
        os.replace(tmp, self.run_dir / "job.json")


def load(run_dir: Path) -> Job:
    data = json.loads((run_dir / "job.json").read_text())
    known = {f.name for f in fields(Job)}
    data = {k: v for k, v in data.items() if k in known}
    data["scope"] = tuple(data.get("scope") or ())
    data["depends_on"] = tuple(data.get("depends_on") or ())
    data["run_dir"] = Path(data["run_dir"])
    for key in ("repo", "worktree"):
        if data.get(key):
            data[key] = Path(data[key])
    return Job(**data)


def load_all(data_dir: Path) -> list[Job]:
    """All saved jobs, oldest first. Unreadable entries are skipped with a warning."""
    jobs: list[Job] = []
    for path in sorted((data_dir / "runs").glob("vibe-run.*/job.json")):
        try:
            jobs.append(load(path.parent))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("skipping unreadable job file %s: %s", path, exc)
    return sorted(jobs, key=lambda j: j.created_at)


@dataclass
class Details:
    """What a job changed, relative to the commit it started from."""

    stat: str = ""
    files: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)  # changed files outside the job's scope
    diff: str | None = None
