"""Job scheduling: queue, concurrency, scope checks and the worktree lifecycle of each job."""

import asyncio
import contextlib
import logging
import time
from collections.abc import Iterable
from pathlib import Path

from vibe_mcp import gitops, runner
from vibe_mcp.config import Config
from vibe_mcp.gitops import GitError
from vibe_mcp.jobs import CLOSED, USABLE_BASE, Details, Job, load_all, new_id, run_dir_for
from vibe_mcp.scope import normalize, overlaps, violations

log = logging.getLogger(__name__)

_GUARD_WARNING = (
    "main checkout's branch or HEAD changed while the job ran "
    "(expected only if you committed or switched branches meanwhile)"
)


class ManagerError(Exception):
    """A request the manager cannot honour (unknown job, bad argument, wrong state)."""


async def find_repo(cwd: Path) -> Path:
    try:
        return await gitops.toplevel(cwd)
    except GitError as exc:
        raise ManagerError(f"not inside a git repository: {cwd}") from exc


class Manager:
    """Runs the jobs of one git repository. Not thread-safe: use from a single event loop."""

    def __init__(self, cfg: Config, repo: Path) -> None:
        self.cfg = cfg
        self.repo = repo
        self.jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._changed = asyncio.Event()
        self._closing = False
        self._git_lock = asyncio.Lock()  # serializes apply/discard on the main checkout
        for job in load_all(cfg.data_dir):
            if job.repo != repo:
                continue
            if job.active:
                self._end(job, "lost", f"server stopped while the job was {job.state}")
            self.jobs[job.id] = job

    # -- public API --------------------------------------------------------------------------

    def get(self, job_id: str) -> Job:
        try:
            return self.jobs[job_id]
        except KeyError:
            raise ManagerError(f"unknown job {job_id}") from None

    def submit(
        self,
        spec: str,
        title: str = "",
        scope: Iterable[str] | None = None,
        base_job: str | None = None,
        max_turns: int | None = None,
        time_limit: int | None = None,
        max_price: float | None = None,
    ) -> Job:
        """Queue a job and start it right away if a slot is free."""
        if not spec.strip():
            raise ManagerError("spec is empty")
        try:
            job_scope = normalize(scope)
        except ValueError as exc:
            raise ManagerError(str(exc)) from exc
        if base_job is not None:
            self.get(base_job)
        max_turns = self.cfg.max_turns if max_turns is None else max_turns
        time_limit = self.cfg.time_limit if time_limit is None else time_limit
        max_price = self.cfg.max_price if max_price is None else max_price
        if max_turns < 1:
            raise ManagerError(f"max_turns must be >= 1, got {max_turns}")
        if time_limit < 1:
            raise ManagerError(f"time_limit must be >= 1, got {time_limit}")
        if max_price < 0:
            raise ManagerError(f"max_price must be >= 0, got {max_price}")

        job_id = new_id(self.jobs)
        job = Job(
            id=job_id,
            run_dir=run_dir_for(self.cfg.data_dir, job_id),
            repo=self.repo,
            spec=spec,
            title=title.strip(),
            scope=job_scope,
            base_job=base_job,
            max_turns=max_turns,
            time_limit=time_limit,
            max_price=max_price,
        )
        job.save()
        self.jobs[job_id] = job
        self._schedule()
        self._notify()
        return job

    async def wait(
        self,
        job_ids: Iterable[str] | None = None,
        timeout: float = 120.0,  # noqa: ASYNC109
        all_: bool = False,
    ) -> list[Job]:
        """Wait until any (or, with all_, every) job ends, or until `timeout` seconds pass.

        Default targets: the jobs active right now. Returns the targets, whatever their state.
        """
        ids = list(job_ids or ())
        targets = [self.get(i) for i in ids] if ids else [j for j in self.jobs.values() if j.active]

        def done() -> bool:
            ended = [not j.active for j in targets]
            return all(ended) if all_ else any(ended)

        if targets and timeout > 0:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(timeout):
                    while not done():
                        await self._changed.wait()
        return targets

    async def details(self, job_id: str, with_diff: bool = False) -> Details:
        """Diffstat, changed and added files, scope violations (and the diff) of a job's commit."""
        job = self.get(job_id)
        if not (job.commit and job.base_ref):
            return Details()
        base, tip = job.base_ref, job.commit
        files = await gitops.changed_files(self.repo, base, tip)
        return Details(
            stat=await gitops.diff_stat(self.repo, base, tip),
            files=files,
            added=await gitops.added_files(self.repo, base, tip),
            violations=violations(job.scope, files),
            diff=await gitops.diff(self.repo, base, tip) if with_diff else None,
        )

    async def cancel(self, job_id: str) -> Job:
        """Cancel a queued job, or kill a running one (its worktree is kept)."""
        job = self.get(job_id)
        if job.state == "queued":
            self._end(job, "cancelled")
            self._schedule()
        elif job.state == "running":
            task = self._tasks[job.id]
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        else:
            raise ManagerError(f"job {job.id} is already {job.state}")
        return job

    async def apply(self, job_id: str) -> list[str]:
        """Stage the job's changes in the main checkout (no commit) and remove its worktree.

        Returns the conflicting paths instead if the changes do not merge; nothing is changed then.
        """
        job = self.get(job_id)
        self._check_closable(job)
        if job.state not in USABLE_BASE:
            raise ManagerError(
                f"job {job.id} is {job.state}; only finished or limit jobs can be applied "
                "(discard it instead)"
            )
        async with self._git_lock:
            if not await gitops.is_clean(self.repo):
                raise ManagerError(
                    "main checkout has uncommitted changes to tracked files; "
                    "commit or stash them first"
                )
            try:
                conflicts = await gitops.merge_squash(self.repo, job.branch)
            except GitError as exc:
                raise ManagerError(str(exc)) from exc
            if conflicts:
                return conflicts
            await self._close(job, "applied")
        return []

    async def discard(self, job_id: str) -> None:
        """Throw the job's worktree and branch away."""
        job = self.get(job_id)
        self._check_closable(job)
        async with self._git_lock:
            await self._close(job, "discarded")

    async def shutdown(self) -> None:
        """Kill every running job. Queued jobs stay queued on disk and are `lost` at next start."""
        self._closing = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # -- scheduling --------------------------------------------------------------------------

    def _worktree_path(self, job: Job) -> Path:
        return self.cfg.data_dir / "worktrees" / f"{self.repo.name}-{job.id}"

    def _check_closable(self, job: Job) -> None:
        """The refusals shared by apply and discard."""
        if job.state in CLOSED:
            raise ManagerError(f"job {job.id} was already {job.state}")
        if job.active:
            raise ManagerError(f"job {job.id} is {job.state}; wait for it or cancel it first")
        waiting = [j.id for j in self.jobs.values() if j.active and j.base_job == job.id]
        if waiting:
            raise ManagerError(
                f"job(s) {', '.join(waiting)} still waiting on base job {job.id}; cancel them first"
            )

    async def _close(self, job: Job, state: str) -> None:
        try:
            await gitops.remove_worktree(self.repo, self._worktree_path(job), job.branch)
        except GitError as exc:
            raise ManagerError(str(exc)) from exc
        job.state = state
        job.save()
        self._notify()

    def _end(self, job: Job, state: str, error: str | None = None) -> None:
        """Move a job that is not running (or never ran) to a final state."""
        job.state, job.ended_at = state, time.time()
        job.error = error or job.error
        job.save()
        self._notify()

    def _notify(self) -> None:
        """Wake everything blocked in wait()."""
        self._changed.set()
        self._changed = asyncio.Event()

    def _default_base(self) -> str:
        """HEAD, or in serial mode the branch of the latest usable job, so that work accumulates."""
        if self.cfg.isolation == "serial":
            usable = [j for j in self.jobs.values() if j.state in USABLE_BASE]
            if usable:
                return max(usable, key=lambda j: j.ended_at or 0).branch
        return "HEAD"

    def _base_of(self, job: Job) -> tuple[str, str]:
        """("ok", git ref) | ("wait", "") | ("fail", error message)."""
        if job.base_job is None:
            return "ok", self._default_base()
        base = self.get(job.base_job)
        if base.active:
            return "wait", ""
        if base.state in USABLE_BASE:
            return "ok", base.branch
        return "fail", f"base job {base.id} was {base.state}; resubmit against HEAD"

    def _schedule(self) -> None:
        """Start every queued job that may start now; fail those whose base is unusable.

        Jobs are considered in submission order. A job that has to wait (for a slot, a
        scope or its base) still reserves its scope, so later overlapping jobs cannot
        overtake and starve it.
        """
        if self._closing:
            return
        running = [j for j in self.jobs.values() if j.state == "running"]
        slots = self.cfg.concurrency - len(running)
        reserved = [j.scope for j in running]
        for job in list(self.jobs.values()):
            if job.state != "queued":
                continue
            verdict, detail = self._base_of(job)
            if verdict == "fail":
                self._end(job, "failed", detail)
                continue
            blocked = verdict == "wait" or slots <= 0
            blocked = blocked or any(overlaps(job.scope, other) for other in reserved)
            reserved.append(job.scope)
            if not blocked:
                slots -= 1
                self._start(job, detail)

    def _start(self, job: Job, base_ref: str) -> None:
        job.state = "running"
        job.started_at = time.time()
        job.base_ref = base_ref
        job.save()
        task = asyncio.create_task(self._execute(job), name=f"vibe-job-{job.id}")
        task.add_done_callback(lambda t: self._finished(job, t))
        self._tasks[job.id] = task
        self._notify()

    # -- one job -----------------------------------------------------------------------------

    async def _main_state(self) -> tuple[str, str]:
        return await gitops.current_branch(self.repo), await gitops.head(self.repo)

    async def _execute(self, job: Job) -> None:
        try:
            worktree = self._worktree_path(job)
            base_sha = await gitops.head(self.repo, job.base_ref)
            await gitops.add_worktree(self.repo, worktree, job.branch, base_sha)
            job.base_ref, job.worktree = base_sha, worktree
            before = await self._main_state()

            code = await runner.run(job, self.cfg, self._notify)

            runner.apply_export(job, runner.read_export(job.run_dir))
            job.state = runner.final_state(code)
            if job.state == "failed" and not job.error:
                job.error = runner.exit_error(job.run_dir, code)
            label = job.title or job.spec.strip().splitlines()[0][:72]
            job.commit = await gitops.commit_all(worktree, f"vibe {job.id}: {label}")
            if await self._main_state() != before:
                job.warning = _GUARD_WARNING
        except Exception as exc:
            log.exception("job %s failed", job.id)
            job.state = "failed"
            job.error = f"{type(exc).__name__}: {exc}"

    def _finished(self, job: Job, task: asyncio.Task[None]) -> None:
        """Done-callback of a job's task. It also runs for a task cancelled before it ever started,
        which a try/finally inside the coroutine would miss."""
        if task.cancelled():
            job.state = "cancelled"
            if self._closing:
                job.error = "server shut down"
        job.ended_at = time.time()
        job.pid = None
        job.save()
        self._tasks.pop(job.id, None)
        self._notify()
        self._schedule()
