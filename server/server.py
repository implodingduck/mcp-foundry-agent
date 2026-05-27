import os
import time
import logging
from dotenv import load_dotenv
from fastmcp import FastMCP, Context
from fastmcp.server.auth.providers.azure import AzureProvider
from fastmcp.server.dependencies import get_http_headers, get_access_token
from azure.ai.agents import AgentsClient
from azure.identity import DefaultAzureCredential
from azure.ai.agents.models import ListSortOrder


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-foundry-agent")

auth_provider = AzureProvider(
    client_id=os.environ.get("CLIENT_ID"),  # Your Azure App Client ID
    client_secret=os.environ.get("CLIENT_SECRET"),                 # Your Azure App Client Secret
    tenant_id=os.environ.get("TENANT_ID"), # Your Azure Tenant ID (REQUIRED)
    base_url=os.environ.get("BASE_URL"),                   # Must match your App registration
    required_scopes=["user_impersonation"],                 # At least one scope REQUIRED - name of scope from your App
)



mcp = FastMCP("FastMCP call Foundry Agent", auth=auth_provider)

PROJECT_ENDPOINT = os.environ.get("PROJECT_ENDPOINT", "")
AGENT_ID = os.environ.get("AGENT_ID", "")
CLIENT_ID = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
TENANT_ID = os.environ.get("TENANT_ID", "")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPE = os.environ.get("FOUNDRY_SCOPE", "https://cognitiveservices.azure.com/")


@mcp.tool()
def invoke_agent(message: str, ctx: Context) -> str:
    """Invoke a Microsoft Foundry Agent with the given message and return its response.

    Args:
        message: The user message to send to the Foundry Agent.

    Returns:
        The agent's text response.
    """
    if not PROJECT_ENDPOINT:
        return "Error: PROJECT_ENDPOINT environment variable is not set."
    if not AGENT_ID:
        return "Error: AGENT_ID environment variable is not set."

    
    credential = DefaultAzureCredential()
    

    logger.info("Connecting to Foundry project at %s", PROJECT_ENDPOINT)

    agents_client = AgentsClient(
        endpoint=PROJECT_ENDPOINT,
        credential=credential,
    )

    with agents_client:
        # Create a new thread for this invocation
        thread = agents_client.threads.create()
        ctx.info(f"Created thread {thread.id}")

        # Add the user message
        agents_client.messages.create(
            thread_id=thread.id,
            role="user",
            content=message,
        )

        # Run the agent and poll until completion
        run = agents_client.runs.create(thread_id=thread.id, agent_id=AGENT_ID)
        while run.status in ["queued", "in_progress", "requires_action"]:
            time.sleep(1)
            run = agents_client.runs.get(thread_id=thread.id, run_id=run.id)

        if run.status == "failed":
            return f"Agent run failed: {run.last_error}"

        # Collect assistant response messages
        messages = agents_client.messages.list(
            thread_id=thread.id, order=ListSortOrder.ASCENDING
        )
        response_parts = []
        for msg in messages:
            if msg.role == "assistant" and msg.text_messages:
                for text_msg in msg.text_messages:
                    response_parts.append(text_msg.text.value)

        if not response_parts:
            return "Agent returned no text response."

        return "\n".join(response_parts)

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)