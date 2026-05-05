import os
import re
import subprocess
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


REPO_ROOT_ENV = "PLATFORM_MCP_REPO_ROOT"
DEFAULT_REPO_ROOT = "/workspace"

_ACTION_LINE = re.compile(r"^\s*#\s+(?P<address>.+?)\s+will be (?P<action>.+?)\s*$")
_PLAN_LINE = re.compile(
    r"^Plan:\s+(?P<add>\d+)\s+to add,\s+(?P<change>\d+)\s+to change,\s+(?P<destroy>\d+)\s+to destroy"
)


def _repo_root() -> Path:
    return Path(os.environ.get(REPO_ROOT_ENV, DEFAULT_REPO_ROOT))


def _clamp_limit(limit: int | None, *, default: int, maximum: int = 500) -> int:
    if limit is None:
        return default
    return max(1, min(int(limit), maximum))


def register_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    def tofu_plan_summary(directory: str = "tofu", resource_limit: int | None = 100, tail_lines: int = 40) -> dict[str, Any]:
        """Run OpenTofu/Terraform-compatible `tofu plan` and summarize infrastructure changes.

        Use for Terraform/OpenTofu IaC review before applying infrastructure
        changes. Runs in <repo>/<directory> and returns a structured summary.
        `resource_limit` caps changed resource addresses and `tail_lines` caps
        stdout/stderr tails.

        Returns: { exit_code, add, change, destroy, resources: [{address, action}], stderr_tail, stdout_tail }.
        Resource addresses come from the `# X will be Y` headers tofu emits before each diff block;
        counts come from the trailing `Plan: A to add, B to change, C to destroy.` line.
        """
        cwd = _repo_root() / directory
        try:
            result = subprocess.run(
                ["tofu", "plan", "-no-color", "-input=false"],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as e:
            return {"error": f"tofu binary not found: {e}", "exit_code": None}

        return _parse_plan(result.stdout, result.stderr, result.returncode, resource_limit=resource_limit, tail_lines=tail_lines)


def _parse_plan(stdout: str, stderr: str, exit_code: int, *, resource_limit: int | None = 100, tail_lines: int = 40) -> dict[str, Any]:
    resources: list[dict[str, str]] = []
    add = change = destroy = 0
    total_resources = 0
    cap = _clamp_limit(resource_limit, default=100)
    for line in stdout.splitlines():
        m = _ACTION_LINE.match(line)
        if m:
            total_resources += 1
            if len(resources) < cap:
                resources.append({"address": m["address"], "action": m["action"]})
            continue
        m = _PLAN_LINE.match(line)
        if m:
            add, change, destroy = int(m["add"]), int(m["change"]), int(m["destroy"])
    tail = max(0, min(int(tail_lines), 200))
    return {
        "exit_code": exit_code,
        "add": add,
        "change": change,
        "destroy": destroy,
        "resource_count": total_resources,
        "resources_truncated": total_resources > len(resources),
        "resources": resources,
        "stdout_tail": "\n".join(stdout.splitlines()[-tail:]) if tail else "",
        "stderr_tail": "\n".join(stderr.splitlines()[-tail:]) if stderr and tail else "",
    }
