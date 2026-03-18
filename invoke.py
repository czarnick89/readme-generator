#!/usr/bin/env python3
"""CLI loop to invoke the Repo Scanner Lambda and the three Bedrock analysis agents."""

import json
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

def invoke_bedrock_agent(agent_id: str, message: str) -> str:
    """Invoke a Bedrock agent with a text message and return the full response."""
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
            print(f"\nScanning {url} ...")
            files = scan_repo(url)
            last_files = files
            print(f"\nFound {len(files)} file(s):")
            for f in files:
                print(f"  {f}")
            print()

        # ── 2: Summarize ─────────────────────────────────────────────────────
        elif choice == "2":
            files = last_files or []
            if not files:
                url = _prompt("No cached files. Enter GitHub URL to scan first> ")
                files = scan_repo(url)
                last_files = files
            print("\nRunning Project Summarizer Agent...\n")
            result = invoke_bedrock_agent(agent_ids["summarizer"], _files_to_message(files))
            print(result, "\n")

        # ── 3: Installation guide ─────────────────────────────────────────────
        elif choice == "3":
            files = last_files or []
            if not files:
                url = _prompt("No cached files. Enter GitHub URL to scan first> ")
                files = scan_repo(url)
                last_files = files
            print("\nRunning Installation Guide Agent...\n")
            result = invoke_bedrock_agent(agent_ids["installer"], _files_to_message(files))
            print(result, "\n")

        # ── 4: Usage examples ─────────────────────────────────────────────────
        elif choice == "4":
            files = last_files or []
            if not files:
                url = _prompt("No cached files. Enter GitHub URL to scan first> ")
                files = scan_repo(url)
                last_files = files
            print("\nRunning Usage Examples Agent...\n")
            result = invoke_bedrock_agent(agent_ids["usage"], _files_to_message(files))
            print(result, "\n")

        # ── 5: All agents ─────────────────────────────────────────────────────
        elif choice == "5":
            url = _prompt("GitHub URL> ")
            if not url:
                continue
            print(f"\nScanning {url} ...")
            files = scan_repo(url)
            last_files = files
            print(f"Found {len(files)} file(s).\n")

            message = _files_to_message(files)
            for key, label in [
                ("summarizer", "Project Summary"),
                ("installer",  "Installation Guide"),
                ("usage",      "Usage Examples"),
            ]:
                print(f"── {label} ──────────────────────────────────────")
                print(invoke_bedrock_agent(agent_ids[key], message))
                print()

        else:
            print("Invalid choice.\n")


if __name__ == "__main__":
    main()
