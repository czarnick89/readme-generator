#!/usr/bin/env python3
"""Simple CLI loop to invoke the RepoScannerTool Lambda directly."""

import json
import boto3

FUNCTION_NAME = "RepoScannerTool-nick"

client = boto3.client("lambda", region_name="us-east-1")


def invoke(repo_url: str) -> dict:
    """Build the Bedrock-agent-shaped event and invoke the Lambda."""
    event = {
        "actionGroup": "ScanRepoAction",
        "apiPath": "/scan-repo",
        "httpMethod": "POST",
        "requestBody": {
            "content": {
                "application/json": {
                    "properties": [
                        {"name": "repo_url", "value": repo_url}
                    ]
                }
            }
        },
    }
    response = client.invoke(
        FunctionName=FUNCTION_NAME,
        Payload=json.dumps(event).encode(),
    )
    payload = json.loads(response["Payload"].read())
    # Pull the inner response body out of the Bedrock wrapper
    try:
        body = payload["response"]["responseBody"]["application/json"]["body"]
        return json.loads(body)
    except (KeyError, TypeError):
        return payload


def main():
    print(f"Repo Scanner CLI  —  invoking Lambda: {FUNCTION_NAME}")
    print("Type a GitHub URL to scan, or 'quit' to exit.\n")
    while True:
        try:
            url = input("repo_url> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not url:
            continue
        if url.lower() in ("quit", "exit", "q"):
            print("Bye!")
            break

        try:
            result = invoke(url)
            files = result.get("files", [])
            print(f"\nFound {len(files)} file(s):")
            for f in files:
                print(f"  {f}")
            print()
        except Exception as e:
            print(f"Error: {e}\n")


if __name__ == "__main__":
    main()
