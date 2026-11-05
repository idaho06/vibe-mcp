"""The MCP server: five tools over a Manager.

Tool descriptions stay terse, because every session pays for them in context tokens.
"""

import functools
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from vibe_mcp import config as config_module
from vibe_mcp.config import Config
from vibe_mcp.format import apply_text, conflict_text, result_text, status_line
from vibe_mcp.manager import Manager, ManagerError, find_repo


def _tool_errors[**P](fn: Callable[P, Awaitable[str]]) -> Callable[P, Awaitable[str]]:
    """Report a ManagerError to the client as a tool error."""

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> str:
        try:
            return await fn(*args, **kwargs)
        except ManagerError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def build_server(cfg: Config | None = None, cwd: Path | None = None) -> MCPServer:
    """Build the server. The repo is the git toplevel of `cwd` (default: the process cwd)."""
    holder: dict[str, Manager] = {}

    @asynccontextmanager
    async def lifespan(_server: MCPServer):
        manager = Manager(cfg or config_module.load(), await find_repo(cwd or Path.cwd()))
        holder["manager"] = manager
        try:
            yield
        finally:
            await manager.shutdown()

    mcp = MCPServer("vibe", lifespan=lifespan)

    @mcp.tool(structured_output=False)
    @_tool_errors
    async def vibe_submit(
        spec: str,
        title: str = "",
        scope: list[str] | None = None,
        base_job: str | None = None,
        max_turns: int | None = None,
        time_limit: int | None = None,
        max_price: float | None = None,
        skill: str | None = None,
    ) -> str:
        """Queue a coding task for Mistral Vibe (a cheaper model) in an isolated git worktree.

        spec: complete and self-contained; Vibe sees neither this chat nor project docs.
        skill: name of a Vibe skill (.vibe/skills/<name>/SKILL.md in the repo or ~) to follow.
        scope: repo-relative paths the task may touch; jobs with overlapping scopes run one
        after another (omit = whole repo). base_job: build on another job's result.
        Returns at once. Then: vibe_wait, vibe_result, vibe_apply.
        """
        job = holder["manager"].submit(
            spec, title, scope, base_job, max_turns, time_limit, max_price, skill
        )
        return status_line(job)

    @mcp.tool(structured_output=False)
    @_tool_errors
    async def vibe_wait(
        job_ids: list[str] | None = None,
        timeout: float = 120,  # noqa: ASYNC109
        wait_all: bool = False,
    ) -> str:
        """Wait until any job (every job if wait_all) in job_ids ends, or timeout seconds pass.

        Default job_ids: all active jobs. timeout=0 just reports status. One line per job.
        """
        jobs = await holder["manager"].wait(job_ids, timeout, wait_all)
        return "\n".join(status_line(j) for j in jobs) or "no active jobs"

    @mcp.tool(structured_output=False)
    @_tool_errors
    async def vibe_result(job_id: str, diff: bool = False) -> str:
        """Outcome of a job: Vibe's own summary (unverified), diffstat, added files, scope
        violations, tokens and cost. diff=true appends the diff (capped)."""
        manager = holder["manager"]
        job = manager.get(job_id)
        details = await manager.details(job_id, with_diff=diff)
        return result_text(job, details, manager.cfg)

    @mcp.tool(structured_output=False)
    @_tool_errors
    async def vibe_cancel(job_id: str) -> str:
        """Cancel a queued job, or kill a running one. Its worktree is kept."""
        return status_line(await holder["manager"].cancel(job_id))

    @mcp.tool(structured_output=False)
    @_tool_errors
    async def vibe_apply(job_id: str, discard: bool = False) -> str:
        """Stage a finished job's changes in the main checkout (git merge --squash, no commit).

        Conflicts are reported and nothing changes. discard=true drops the job's worktree and
        branch instead.
        """
        manager = holder["manager"]
        job = manager.get(job_id)
        if discard:
            await manager.discard(job_id)
            return status_line(job)
        conflicts = await manager.apply(job_id)
        if conflicts:
            return conflict_text(job, conflicts)
        return apply_text(job, (await manager.details(job_id)).stat)  # commit outlives the branch

    return mcp
