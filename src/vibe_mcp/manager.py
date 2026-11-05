"""Job scheduling: queue, concurrency, scope checks and the worktree lifecycle of each job."""

import asyncio
import contextlib
import logging
import time
from collections.abc import Iterable
from pathlib import Path

from vibe_mcp import context, fanout, gitops, runner, skills
from vibe_mcp.config import Config
from vibe_mcp.gitops import GitError
from vibe_mcp.jobs import CLOSED, USABLE_BASE, Details, Job, load_all, new_id, run_dir_for
from vibe_mcp.scope import normalize, overlaps, violations

log = logging.getLogger(__name__)

_GUARD_WARNING = (
    "main checkout's branch or HEAD changed while the job ran "
    "(expected only if you committed or switched branches meanwhile)"
)


_READONLY_WARNING = "read-only job changed files; the changes were discarded"


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
        skill: str | None = None,
    ) -> Job:
        """Queue a job and start it right away if a slot is free."""
        if not spec.strip():
            raise ManagerError("spec is empty")
        try:
            project_context = context.blocks(self.repo, self.cfg.context_files)
            fan = skills.fanout(skill, self.repo) if skill else None
            preamble = project_context + (skills.block(skill, self.repo) if skill else "")
        except skills.SkillError as exc:
            raise ManagerError(str(exc)) from exc
        if preamble:
            preamble += "## Task\n\n"
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

        if fan:
            if base_job is not None:
                raise ManagerError("base_job cannot be combined with a fan-out skill")
            return self._enqueue_fanout(
                fan,
                skill=skill or "",
                project_context=project_context,
                preamble=preamble,
                spec=spec,
                title=title.strip(),
                scope=job_scope,
                limits=(max_turns, time_limit, max_price),
            )

        job_id = new_id(self.jobs)
        job = Job(
            id=job_id,
            run_dir=run_dir_for(self.cfg.data_dir, job_id),
            repo=self.repo,
            spec=spec,
            title=title.strip(),
            skill=skill or None,
            preamble=preamble,
            scope=job_scope,
            base_job=base_job,
            max_turns=max_turns,
            time_limit=time_limit,
            max_price=max_price,
        )
        return self.enqueue(job)

    def _enqueue_fanout(
        self,
        fan: skills.Fanout,
        *,
        skill: str,
        project_context: str,
        preamble: str,
        spec: str,
        title: str,
        scope: tuple[str, ...],
        limits: tuple[int, int, float],
    ) -> Job:
        """Queue one read-only reviewer per angle plus the merge job; return the merge job."""
        try:
            base = fanout.pin(self.repo, self._default_base())  # one commit for the whole group
        except ValueError as exc:
            raise ManagerError(str(exc)) from exc
        max_turns, time_limit, max_price = limits
        *reviewers, merge = fanout.build(
            cfg=self.cfg,
            repo=self.repo,
            taken=self.jobs,
            skill=skill,
            angles=fan.angles,
            context=project_context,
            merge_preamble=preamble,
            spec=spec,
            title=title,
            scope=scope,
            max_turns=max_turns,
            time_limit=time_limit,
            max_price=max_price,
            base_ref=base,
            readonly=fan.readonly,
        )
        for reviewer in reviewers:
            self.enqueue(reviewer)
        return self.enqueue(merge)

    def enqueue(self, job: Job) -> Job:
        """Register a prepared job and start it as soon as the scheduler allows."""
        for dependency in job.depends_on:
            self.get(dependency)  # raises for an unknown id, before anything is registered
        job.save()
        self.jobs[job.id] = job
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
        targets = (
            [self.get(i) for i in ids]
            if ids
            else [j for j in self.jobs.values() if j.active and not j.hidden]
        )

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
        # the parent first: cancelled reviewers must not let it start and then fail
        for child in [j for j in self.jobs.values() if j.parent == job.id and j.active]:
            await self.cancel(child.id)
        return job

    async def apply(self, job_id: str) -> list[str]:
        """Stage the job's changes in the main checkout (no commit) and remove its worktree.

        Returns the conflicting paths instead if the changes do not merge; nothing is changed then.
        """
        job = self.get(job_id)
        if job.readonly:
            raise ManagerError(f"job {job.id} is read-only: there is nothing to apply")
        async with self._git_lock:  # checked inside: a racing apply/discard may have closed it
            self._check_closable(job)
            if job.state not in USABLE_BASE:
                raise ManagerError(
                    f"job {job.id} is {job.state}; only finished or limit jobs can be applied "
                    "(discard it instead)"
                )
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
        async with self._git_lock:
            self._check_closable(job)
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
        waiting = [
            j.id
            for j in self.jobs.values()
            if j.active and (j.base_job == job.id or job.id in j.depends_on)
        ]
        if waiting:
            raise ManagerError(
                f"job(s) {', '.join(waiting)} still waiting on job {job.id}; cancel them first"
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
        dependencies = [self.get(d) for d in job.depends_on]
        if any(d.active for d in dependencies):
            return "wait", ""
        if dependencies and not any(d.state in USABLE_BASE for d in dependencies):
            states = ", ".join(f"{d.id} {d.state}" for d in dependencies)
            return "fail", f"none of the jobs it depends on finished ({states})"
        if job.base_ref is not None:  # pinned when queued, e.g. all members of a fan-out
            return "ok", job.base_ref
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
        reserved = [j.scope for j in running if not j.readonly]
        for job in list(self.jobs.values()):
            if job.state != "queued":
                continue
            verdict, detail = self._base_of(job)
            if verdict == "fail":
                self._end(job, "failed", detail)
                continue
            blocked = verdict == "wait" or slots <= 0
            if not job.readonly:
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
            await gitops.add_worktree(
                self.repo, worktree, None if job.readonly else job.branch, base_sha
            )
            job.base_ref, job.worktree = base_sha, worktree
            if job.depends_on and not job.appendix:
                reviewers = [self.jobs[d] for d in job.depends_on]
                job.appendix = fanout.appendix(reviewers)
                job.reviewers_cost = sum(r.cost_usd for r in reviewers)
            before = await self._main_state()

            code = await runner.run(job, self.cfg, self._notify)

            runner.apply_export(job, runner.read_export(job.run_dir))
            job.state = runner.final_state(code)
            if job.state == "failed" and not job.error:
                job.error = runner.exit_error(job.run_dir, code)
            warnings = []
            if job.readonly:
                if not await gitops.is_clean(worktree, untracked=True):
                    warnings.append(_READONLY_WARNING)
                await gitops.remove_worktree(self.repo, worktree, None)
                job.worktree = None
            else:
                label = job.title or job.spec.strip().splitlines()[0][:72]
                job.commit = await gitops.commit_all(worktree, f"vibe {job.id}: {label}")
            if await self._main_state() != before:
                warnings.append(_GUARD_WARNING)
            job.warning = "; ".join(warnings) or None
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
        parent = self.jobs.get(job.parent) if job.parent else None
        if parent is not None and parent.state == "queued":
            parent.last_action = fanout.progress([self.jobs[d] for d in parent.depends_on])
        self._notify()
        self._schedule()
