#!/usr/bin/env python3
"""CLI loop to invoke the Repo Scanner Lambda and the three Bedrock analysis agents."""

import itertools
import json
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import boto3

REGION = "us-east-1"
LAMBDA_FUNCTION_NAME = "RepoScannerTool-nick"

# Bedrock agent names as deployed by Terraform
AGENT_NAMES = {
    "summarizer": "Project_Summarizer_Agent-Nick",
    "installer":  "Installation_Guide_Agent-Nick",
    "usage":      "Usage_Examples_Agent-Nick",
}

lambda_client  = boto3.client("lambda",               region_name=REGION)
bedrock_mgmt   = boto3.client("bedrock-agent",        region_name=REGION)
bedrock_rt     = boto3.client("bedrock-agent-runtime", region_name=REGION)


# ---------------------------------------------------------------------------
# Spinner
# ---------------------------------------------------------------------------

class Spinner:
    """Displays an animated ASCII dancing man while a task runs."""
    _NLINES = 3
    _FRAMES = [
        [r" \o/ ", r"  |  ", r" / \ "],
        [r"  o  ", r" /|\ ", r" | \ "],
        [r"  o  ", r" \|/ ", r" / \ "],
        [r"  o  ", r" /|\ ", r" / | "],
    ]

    def __init__(self, label: str = "Working"):
        self._label = label
        self._lines_printed = 0
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        for frame in itertools.cycle(self._FRAMES):
            if self._stop.is_set():
                break
            if self._lines_printed > 0:
                # Move cursor to start of first line of previous frame
                sys.stdout.write(f"\033[{self._NLINES}F")
            for i, line in enumerate(frame):
                suffix = f"  {self._label}..." if i == 1 else ""
                sys.stdout.write(f"{line}{suffix}\033[K\n")
            self._lines_printed = self._NLINES
            sys.stdout.flush()
            time.sleep(0.2)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()
        if self._lines_printed > 0:
            # Move up and erase each line, then return cursor to start position
            sys.stdout.write(f"\033[{self._NLINES}F")
            for _ in range(self._NLINES):
                sys.stdout.write("\033[2K\n")
            sys.stdout.write(f"\033[{self._NLINES}F")
            sys.stdout.flush()


# ---------------------------------------------------------------------------
# Agent ID lookup
# ---------------------------------------------------------------------------

def _get_agent_id(agent_name: str) -> str:
    """Return the agent_id for a given agent name, resolved at runtime."""
    paginator = bedrock_mgmt.get_paginator("list_agents")
    for page in paginator.paginate():
        for summary in page["agentSummaries"]:
            if summary["agentName"] == agent_name:
                return summary["agentId"]
    raise RuntimeError(f"Bedrock agent not found: {agent_name!r}")


def _load_agent_ids() -> dict:
    print("Resolving Bedrock agent IDs...", end=" ", flush=True)
    ids = {key: _get_agent_id(name) for key, name in AGENT_NAMES.items()}
    print("done.\n")
    return ids


# ---------------------------------------------------------------------------
# GitHub: lightweight commit SHA check
# ---------------------------------------------------------------------------

def _repo_slug(url: str) -> str | None:
    """Extract 'owner/repo' from a github.com URL, or None if not parseable."""
    parts = urllib.parse.urlparse(url).path.strip("/").split("/")
    return f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else None


def get_latest_sha(url: str) -> str | None:
    """Return the latest commit SHA for a public GitHub repo, or None on failure."""
    slug = _repo_slug(url)
    if not slug:
        return None
    api_url = f"https://api.github.com/repos/{slug}/commits?per_page=1"
    req = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data[0]["sha"][:7]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Session history  {url -> {"sha": str, "files": list[str]}}
# ---------------------------------------------------------------------------

history: dict[str, dict] = {}


def get_files_for_url(url: str) -> list[str]:
    """Return file list for a URL, using cache if the repo hasn't changed."""
    current_sha = get_latest_sha(url)
    cached = history.get(url)

    if cached:
        if current_sha and cached["sha"] == current_sha:
            print(f"  (cache hit — SHA {current_sha} unchanged, skipping scan)")
            return cached["files"]
        elif current_sha:
            print(f"  (repo changed: {cached['sha']} → {current_sha}, re-scanning...)")
        else:
            print("  (couldn't check SHA, re-scanning to be safe...)")

    files = scan_repo(url)
    history[url] = {"sha": current_sha or "unknown", "files": files}
    return files


# ---------------------------------------------------------------------------
# Lambda: scan repo
# ---------------------------------------------------------------------------

def scan_repo(repo_url: str) -> list[str]:
    """Invoke the RepoScanner Lambda and return the file list."""
    event = {
        "actionGroup": "ScanRepoAction",
        "apiPath": "/scan-repo",
        "httpMethod": "POST",
        "requestBody": {
            "content": {
                "application/json": {
                    "properties": [{"name": "repo_url", "value": repo_url}]
                }
            }
        },
    }
    with Spinner("Scanning repository"):
        response = lambda_client.invoke(
            FunctionName=LAMBDA_FUNCTION_NAME,
            Payload=json.dumps(event).encode(),
        )
        payload = json.loads(response["Payload"].read())
    try:
        body = payload["response"]["responseBody"]["application/json"]["body"]
        return json.loads(body).get("files", [])
    except (KeyError, TypeError):
        return []


# ---------------------------------------------------------------------------
# Bedrock Agent: invoke and stream response
# ---------------------------------------------------------------------------

def invoke_bedrock_agent(agent_id: str, message: str, label: str = "Thinking") -> str:
    """Invoke a Bedrock agent with a text message and return the full response."""
    with Spinner(label):
        response = bedrock_rt.invoke_agent(
            agentId=agent_id,
            agentAliasId="TSTALIASID",   # built-in alias for the DRAFT version
            sessionId=str(uuid.uuid4()),
            inputText=message,
        )
        chunks = []
        for event in response["completion"]:
            if "chunk" in event:
                chunks.append(event["chunk"]["bytes"].decode("utf-8"))
    return "".join(chunks)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _files_to_message(files: list[str]) -> str:
    return "Here is the list of files in the repository:\n" + "\n".join(files)


def _print_menu():
    print("┌─────────────────────────────────────────────┐")
    print("│  README Generator CLI                       │")
    print("├─────────────────────────────────────────────┤")
    print("│  1) Scan repo          (returns file list)  │")
    print("│  2) Summarize project  (Bedrock agent)      │")
    print("│  3) Installation guide (Bedrock agent)      │")
    print("│  4) Usage examples     (Bedrock agent)      │")
    print("│  5) Run ALL agents on a URL                 │")
    print("│  h) Session history                         │")
    print("│  q) Quit                                    │")
    print("└─────────────────────────────────────────────┘")


def _prompt(text: str) -> str:
    try:
        return input(text).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nBye!")
        raise SystemExit(0)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    agent_ids = _load_agent_ids()
    last_files: list[str] = []

    while True:
        _print_menu()
        if last_files:
            print(f"  (cached file list: {len(last_files)} files from last scan)\n")
        choice = _prompt("choice> ")

        if choice in ("q", "quit", "exit"):
            print("Bye!")
            break

        # ── 1: Scan ─────────────────────────────────────────────────────────
        if choice == "1":
            url = _prompt("GitHub URL> ")
            if not url:
                continue
            files = get_files_for_url(url)
            last_files = files
            print(f"\nFound {len(files)} file(s):")
            for f in files:
                print(f"  {f}")
            print()

        # ── 2: Summarize ─────────────────────────────────────────────────────
        elif choice == "2":
            if not last_files:
                url = _prompt("No cached files. Enter GitHub URL> ")
                last_files = get_files_for_url(url)
            result = invoke_bedrock_agent(agent_ids["summarizer"], _files_to_message(last_files), "Summarizing project")
            print(result, "\n")

        # ── 3: Installation guide ─────────────────────────────────────────────
        elif choice == "3":
            if not last_files:
                url = _prompt("No cached files. Enter GitHub URL> ")
                last_files = get_files_for_url(url)
            result = invoke_bedrock_agent(agent_ids["installer"], _files_to_message(last_files), "Writing installation guide")
            print(result, "\n")

        # ── 4: Usage examples ─────────────────────────────────────────────────
        elif choice == "4":
            if not last_files:
                url = _prompt("No cached files. Enter GitHub URL> ")
                last_files = get_files_for_url(url)
            result = invoke_bedrock_agent(agent_ids["usage"], _files_to_message(last_files), "Writing usage examples")
            print(result, "\n")

        # ── 5: All agents ─────────────────────────────────────────────────────
        elif choice == "5":
            url = _prompt("GitHub URL> ")
            if not url:
                continue
            files = get_files_for_url(url)
            last_files = files
            print(f"Found {len(files)} file(s).\n")

            message = _files_to_message(files)
            for key, label, spinner_label in [
                ("summarizer", "Project Summary",    "Summarizing project"),
                ("installer",  "Installation Guide", "Writing installation guide"),
                ("usage",      "Usage Examples",     "Writing usage examples"),
            ]:
                print(f"── {label} ──────────────────────────────────────")
                print(invoke_bedrock_agent(agent_ids[key], message, spinner_label))
                print()

        # ── h: History ────────────────────────────────────────────────────────
        elif choice == "h":
            if not history:
                print("  No history yet this session.\n")
                continue
            print()
            entries = list(history.items())
            for i, (url, data) in enumerate(entries, 1):
                print(f"  {i}) [{data['sha']}] {url}  ({len(data['files'])} files)")
            print()
            pick = _prompt("Load entry # (or Enter to cancel)> ")
            if not pick:
                continue
            try:
                url, data = entries[int(pick) - 1]
            except (ValueError, IndexError):
                print("  Invalid selection.\n")
                continue
            # SHA staleness check before loading from history
            current_sha = get_latest_sha(url)
            if current_sha and current_sha != data["sha"]:
                print(f"  Repo has new commits ({data['sha']} → {current_sha}).")
                rescan = _prompt("  Re-scan? [y/N]> ").lower()
                if rescan == "y":
                    files = scan_repo(url)
                    history[url] = {"sha": current_sha, "files": files}
                    last_files = files
                    print(f"  Re-scanned. Found {len(files)} file(s).\n")
                    continue
            last_files = data["files"]
            print(f"  Loaded {len(last_files)} files from {url}\n")

        else:
            print("Invalid choice.\n")


if __name__ == "__main__":
    main()
