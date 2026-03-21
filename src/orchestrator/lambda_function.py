import json
import boto3
import os
import uuid
import urllib.parse
import time

# Initialize AWS clients
s3_client = boto3.client('s3')
lambda_client = boto3.client('lambda')
bedrock_agent_runtime_client = boto3.client('bedrock-agent-runtime')

# Get agent details and bucket name from environment variables
REPO_SCANNER_LAMBDA_NAME = os.environ.get("REPO_SCANNER_LAMBDA_NAME", "RepoScannerTool-nick")
PROJECT_SUMMARIZER_AGENT_ID = os.environ.get("PROJECT_SUMMARIZER_AGENT_ID")
PROJECT_SUMMARIZER_AGENT_ALIAS_ID = os.environ.get("PROJECT_SUMMARIZER_AGENT_ALIAS_ID")
INSTALLATION_GUIDE_AGENT_ID = os.environ.get("INSTALLATION_GUIDE_AGENT_ID")
INSTALLATION_GUIDE_AGENT_ALIAS_ID = os.environ.get("INSTALLATION_GUIDE_AGENT_ALIAS_ID")
USAGE_EXAMPLES_AGENT_ID = os.environ.get("USAGE_EXAMPLES_AGENT_ID")
USAGE_EXAMPLES_AGENT_ALIAS_ID = os.environ.get("USAGE_EXAMPLES_AGENT_ALIAS_ID")
FINAL_COMPILER_AGENT_ID = os.environ.get("FINAL_COMPILER_AGENT_ID")
FINAL_COMPILER_AGENT_ALIAS_ID = os.environ.get("FINAL_COMPILER_AGENT_ALIAS_ID")
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET")

print(f"[DEBUG] OUTPUT_BUCKET = {OUTPUT_BUCKET}")


def scan_repo_direct(repo_url):
    """Invoke the RepoScanner Lambda directly — no Bedrock agent middleman."""
    print(f"[DEBUG] Directly invoking RepoScanner Lambda for: {repo_url}")
    event = {
        "actionGroup": "ScanRepoAction",
        "apiPath": "/scan_repo",
        "httpMethod": "POST",
        "requestBody": {
            "content": {
                "application/json": {
                    "properties": [{"name": "repo_url", "value": repo_url}]
                }
            }
        }
    }
    response = lambda_client.invoke(
        FunctionName=REPO_SCANNER_LAMBDA_NAME,
        Payload=json.dumps(event).encode(),
    )
    payload = json.loads(response["Payload"].read())
    try:
        body = payload["response"]["responseBody"]["application/json"]["body"]
        files = json.loads(body).get("files", [])
        print(f"[DEBUG] RepoScanner returned {len(files)} files")
        return files
    except (KeyError, TypeError) as e:
        print(f"[DEBUG] Failed to parse RepoScanner response: {e}. Raw: {payload}")
        return []


def invoke_agent_helper(agent_id, alias_id, input_text):
    """Invoke a Bedrock agent with a fresh session ID and return the response."""
    session_id = str(uuid.uuid4())
    print(f"Invoking agent {agent_id} (session {session_id}) with input: {input_text[:200]}")
    for attempt in range(3):
        try:
            response = bedrock_agent_runtime_client.invoke_agent(
                agentId=agent_id,
                agentAliasId=alias_id,
                sessionId=session_id,
                inputText=input_text
            )
            completion = ""
            for event in response.get("completion"):
                chunk = event["chunk"]
                completion += chunk["bytes"].decode()
            print(f"Agent {agent_id} returned: {completion[:200]}")
            return completion
        except Exception as e:
            if attempt < 2 and "throttling" in str(e).lower():
                wait = 10 * (attempt + 1)
                print(f"Throttled on agent {agent_id}, retrying in {wait}s (attempt {attempt + 1}/3)...")
                time.sleep(wait)
                session_id = str(uuid.uuid4())
            else:
                print(f"Error invoking agent {agent_id}: {e}")
                return f"Error processing this section: {e}"


def handler(event, context):
    """The main Lambda handler function."""
    print(f"Orchestrator started with event: {json.dumps(event)}")

    # 1. Get the repo URL from the S3 event trigger
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = urllib.parse.unquote_plus(event['Records'][0]['s3']['object']['key'])

    # Decode the filename back to a URL.
    # Encoding convention: https:// → https---, each / → -SLASH-
    # e.g. https---github.com-SLASH-municipal-ai → https://github.com/municipal-ai
    filename = key.replace('inputs/', '')
    repo_url = filename.replace('---', '://', 1).replace('-SLASH-', '/')

    print(f"[DEBUG] Bucket: {bucket}")
    print(f"[DEBUG] Key: {key}")
    print(f"[DEBUG] Repo URL: {repo_url}")
    print(f"[DEBUG] Output Bucket: {OUTPUT_BUCKET}")

    # 2. Extract and sanitize the repo name
    sanitized_repo_name = repo_url.split('/')[-1].replace('.git', '')
    output_key = f"outputs/{sanitized_repo_name}/README.md"

    print(f"[DEBUG] Sanitized repo name: {sanitized_repo_name}")
    print(f"[DEBUG] Output key: {output_key}")

    # Skip the HeadObject check - proceed directly with generation
    print("[DEBUG] Skipping existence check, proceeding with generation...")

    # --- AGENT INVOCATION CHAIN ---

    # 3. Scan the repository directly via Lambda (bypass Bedrock agent)
    print("[DEBUG] Starting agent invocation chain...")
    files = scan_repo_direct(repo_url)
    file_list_json = json.dumps({"files": files})
    print(f"[DEBUG] file_list_json (first 300 chars): {file_list_json[:300]}")

    # 4. Call the three analytical agents in parallel (each gets its own session)
    project_summary = invoke_agent_helper(
        PROJECT_SUMMARIZER_AGENT_ID, PROJECT_SUMMARIZER_AGENT_ALIAS_ID,
        f"Here is the file list for the GitHub repository {repo_url}:\n\n{file_list_json}\n\nPlease write a project summary."
    )
    installation_guide = invoke_agent_helper(
        INSTALLATION_GUIDE_AGENT_ID, INSTALLATION_GUIDE_AGENT_ALIAS_ID,
        f"Here is the file list for the GitHub repository {repo_url}:\n\n{file_list_json}\n\nPlease write an installation guide."
    )
    usage_examples = invoke_agent_helper(
        USAGE_EXAMPLES_AGENT_ID, USAGE_EXAMPLES_AGENT_ALIAS_ID,
        f"Here is the file list for the GitHub repository {repo_url}:\n\n{file_list_json}\n\nPlease write usage examples."
    )

    # 5. Assemble inputs for the Final_Compiler_Agent
    compiler_input = {
        "repository_name": sanitized_repo_name,
        "project_summary": project_summary,
        "installation_guide": installation_guide,
        "usage_examples": usage_examples
    }
    compiler_input_json = json.dumps(compiler_input)

    # 6. Call the Final_Compiler_Agent to get the final Markdown
    readme_content = invoke_agent_helper(
        FINAL_COMPILER_AGENT_ID, FINAL_COMPILER_AGENT_ALIAS_ID, compiler_input_json
    )

    # 7. Upload the final README.md to the output S3 bucket
    try:
        print(f"[DEBUG] Attempting PutObject to {OUTPUT_BUCKET}/{output_key}")
        s3_client.put_object(
            Bucket=OUTPUT_BUCKET,
            Key=output_key,
            Body=readme_content,
            ContentType='text/markdown'
        )
        print(
            f"Successfully uploaded README.md to s3://{OUTPUT_BUCKET}/{output_key}")
    except Exception as e:
        print(f"Error uploading README.md to S3: {e}")
        raise e

    return {
        'statusCode': 200,
        'body': json.dumps('README.md generated successfully!')
    }