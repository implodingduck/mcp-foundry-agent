# mcp-foundry-agent

MCP Server that exposes a Microsoft Foundry Agent as a tool.

## Prerequisites

- Python 3.9+
- An Azure AI Foundry project with a deployed agent
- Azure credentials configured (e.g., `az login` or service principal)

## Setup

```bash
cd server
pip install -r requirements.txt
```

## Configuration

Set the following environment variables:

| Variable | Description |
|---|---|
| `PROJECT_ENDPOINT` | The Azure AI Foundry project endpoint (from the project Overview page) |
| `AGENT_ID` | The ID of the Foundry agent to invoke |

## Running

```bash
cd server
fastmcp run server.py
```

## Tool: `invoke_agent`

Sends a message to the configured Foundry Agent and returns its text response.

**Parameters:**
- `message` (string, required): The message to send to the agent.

