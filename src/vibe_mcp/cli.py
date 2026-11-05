"""Command line: `vibe-mcp [serve]` runs the MCP server on stdio; `vibe-mcp stats` sums up jobs."""

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from vibe_mcp import config
from vibe_mcp.config import ConfigError
from vibe_mcp.format import duration
from vibe_mcp.jobs import Job, load_all
from vibe_mcp.manager import ManagerError, find_repo
from vibe_mcp.server import build_server


def stats_data(jobs: list[Job]) -> dict:
    """Totals over saved jobs. Averages count only jobs that actually ran."""
    ran = [j for j in jobs if j.started_at is not None]
    ended = [j for j in ran if j.ended_at is not None]
    cost = sum(j.cost_usd for j in jobs)
    seconds = sum(j.ended_at - j.started_at for j in ended)
    return {
        "jobs": len(jobs),
        "states": dict(Counter(j.state for j in jobs)),
        "cost_usd": cost,
        "avg_cost_usd": cost / len(ran) if ran else 0,
        "tokens": sum(j.tokens for j in jobs),
        "seconds": seconds,
        "avg_seconds": seconds / len(ended) if ended else 0,
    }


def stats_text(data: dict) -> str:
    if not data["jobs"]:
        return "no jobs yet"
    states = ", ".join(
        f"{state} {n}" for state, n in sorted(data["states"].items(), key=lambda kv: -kv[1])
    )
    return "\n".join(
        [
            f"jobs: {data['jobs']} ({states})",
            f"cost: ${data['cost_usd']:.4f} total, ${data['avg_cost_usd']:.4f} avg per job run",
            f"tokens: {data['tokens']:,} total",
            f"time: {duration(data['seconds'])} total, {duration(data['avg_seconds'])} avg",
        ]
    )


def _stats(as_json: bool) -> int:
    data = stats_data(load_all(config.load().data_dir))
    print(json.dumps(data) if as_json else stats_text(data))
    return 0


def _serve() -> int:
    cfg = config.load()
    repo = asyncio.run(find_repo(Path.cwd()))  # fail early, with a plain message
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)  # stdout is the MCP channel
    print(
        f"vibe-mcp: serving git repo {repo} (data in {cfg.data_dir})", file=sys.stderr, flush=True
    )
    build_server(cfg, repo).run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vibe-mcp", description=__doc__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="run the MCP server on stdio (the default)")
    stats = commands.add_parser("stats", help="summarize all saved jobs: count, cost, tokens, time")
    stats.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    try:
        return _stats(args.json) if args.command == "stats" else _serve()
    except (ConfigError, ManagerError) as exc:
        print(f"vibe-mcp: {exc}", file=sys.stderr)
        return 2
