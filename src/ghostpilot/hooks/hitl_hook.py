"""Standalone CLI script executed by Claude PreToolUse or shell wrapper for HITL checks."""

import json
import os
import sys
import uuid

import httpx

from ghostpilot.models.enums import ApprovalDecision, RiskLevel

SAFE_READONLY_COMMANDS = {
    "ls",
    "pwd",
    "cat",
    "head",
    "tail",
    "git status",
    "git diff",
    "git log",
    "git branch",
    "pytest",
    "ruff",
    "echo",
    "find",
    "grep",
    "which",
}

MUTATING_COMMAND_PREFIXES = [
    "rm",
    "mv",
    "cp",
    "git push",
    "npm install",
    "npm i",
    "pip install",
    "curl",
    "wget",
    "sudo",
    "dd",
    "mkfs",
]


def classify_risk(tool_name: str, tool_input: dict) -> tuple[RiskLevel, str]:
    """Classify tool execution risk and return (RiskLevel, summary)."""
    command = ""
    if tool_name.lower() in ("bash", "execute_command", "shell"):
        command = str(tool_input.get("command", "")).strip()

    # Read-only tools
    if tool_name.lower() in ("read", "view", "filereader", "grep", "search"):
        path = tool_input.get("file_path") or tool_input.get("path") or ""
        return RiskLevel.LOW, f"Read file {path}"

    if command:
        lower_cmd = command.lower()
        # Direct match or begins with safe command + space
        if any(lower_cmd == safe or lower_cmd.startswith(f"{safe} ") for safe in SAFE_READONLY_COMMANDS):
            return RiskLevel.LOW, f"Run read-only command: {command}"

        if any(lower_cmd.startswith(mut) for mut in MUTATING_COMMAND_PREFIXES):
            if "rm -rf /" in lower_cmd or "git push --force" in lower_cmd:
                return RiskLevel.CRITICAL, f"Potentially destructive command: {command}"
            return RiskLevel.HIGH, f"Execute mutating bash: {command}"

        return RiskLevel.MEDIUM, f"Execute bash command: {command}"

    # File edits/writes
    if tool_name.lower() in ("write", "edit", "multiedit", "filewriter"):
        path = tool_input.get("file_path") or tool_input.get("path") or ""
        return RiskLevel.MEDIUM, f"Modify file: {path}"

    return RiskLevel.MEDIUM, f"Invoke tool: {tool_name}"


def main():
    # Read hook input from stdin
    raw_input = sys.stdin.read().strip()
    if not raw_input:
        # Nothing to inspect; allow
        sys.exit(0)

    try:
        payload = json.loads(raw_input)
    except json.JSONDecodeError:
        # Fallback if raw text
        payload = {"tool_name": "Bash", "tool_input": {"command": raw_input}}

    tool_name = payload.get("tool_name", "unknown")
    tool_input = payload.get("tool_input", {})
    session_id = payload.get("session_id")

    risk_level, summary = classify_risk(tool_name, tool_input)

    # Check auto-approve readonly
    auto_approve_readonly = os.getenv("AUTO_APPROVE_READONLY", "true").lower() in ("true", "1", "yes")
    if auto_approve_readonly and risk_level == RiskLevel.LOW:
        sys.exit(0)

    # Post to local FastAPI Control Plane
    request_id = str(uuid.uuid4())[:8]
    control_host = os.getenv("GHOSTPILOT_HOST", "127.0.0.1")
    control_port = os.getenv("GHOSTPILOT_PORT", "8765")
    url = f"http://{control_host}:{control_port}/internal/hitl/confirm"

    req_body = {
        "request_id": request_id,
        "agent": "claude",
        "tool_name": tool_name,
        "summary": summary,
        "risk_level": risk_level.value,
        "tool_input": tool_input,
        "session_id": session_id,
    }

    try:
        with httpx.Client(timeout=300.0) as client:
            resp = client.post(url, json=req_body)
            if resp.status_code == 200:
                data = resp.json()
                decision = data.get("decision")
                if decision in (ApprovalDecision.APPROVE.value, ApprovalDecision.TRUST_SESSION.value):
                    sys.exit(0)
                else:
                    reason = data.get("reason", "Action denied by user.")
                    sys.stderr.write(f"GhostPilot HITL: {reason}\n")
                    sys.exit(2)
            else:
                sys.stderr.write(f"GhostPilot control plane returned status {resp.status_code}\n")
                sys.exit(2)
    except Exception as exc:
        sys.stderr.write(f"GhostPilot HITL Hook connection error: {exc}\n")
        # In case control plane is unreachable, block unsafe commands
        if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            sys.exit(2)
        sys.exit(0)


if __name__ == "__main__":
    main()
