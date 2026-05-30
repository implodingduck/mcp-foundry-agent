import os
import logging
from dotenv import load_dotenv
from msal import ConfidentialClientApplication
from fastmcp import FastMCP, Context
from fastmcp.server.auth.providers.azure import AzureProvider
from fastmcp.server.dependencies import get_http_headers, get_access_token
from azure.ai.projects import AIProjectClient
from azure.core.credentials import AccessToken, TokenCredential
from openai.types.responses.response_input_param import McpApprovalResponse, ResponseInputParam
import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken as MCPAccessToken

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-foundry-agent")


class DirectEntraTokenVerifier:
    """Validates raw Entra ID access tokens using Microsoft's JWKS endpoint."""

    def __init__(self, tenant_id: str, client_id: str):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self.audiences = [client_id, f"api://{client_id}"]
        jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        self._jwks_client = PyJWKClient(jwks_url, cache_keys=True)

    async def verify_token(self, token: str) -> MCPAccessToken | None:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            decoded = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.audiences,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "aud", "iss"]},
            )

            # Reject if tenant doesn't match
            if decoded.get("tid") != self.tenant_id:
                logger.debug("Direct Entra token rejected: tid mismatch")
                return None

            # Require delegated scopes (scp claim) to ensure this is an
            # access token, not an ID token
            scp = decoded.get("scp", "")
            scopes = scp.split() if scp else []
            if not scopes:
                logger.debug("Direct Entra token rejected: no scp claim (may be an ID token)")
                return None

            client_id = decoded.get("azp") or decoded.get("appid") or "unknown"
            logger.info("Direct Entra token validated (sub=%s, scopes=%s)", decoded.get("sub"), scopes)

            return MCPAccessToken(
                token=token,
                client_id=client_id,
                scopes=scopes,
                expires_at=decoded.get("exp"),
            )

        except jwt.ExpiredSignatureError:
            logger.debug("Direct Entra token rejected: expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.debug("Direct Entra token rejected: %s", e)
            return None
        except Exception as e:
            logger.debug("Direct Entra token verification error: %s", e)
            return None


class DualAuthProvider(AzureProvider):
    """Accepts both FastMCP OAuth flow tokens and direct Entra ID access tokens."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._entra_verifier = DirectEntraTokenVerifier(
            tenant_id=kwargs["tenant_id"],
            client_id=kwargs["client_id"],
        )

    async def verify_token(self, token: str) -> MCPAccessToken | None:
        # Try the native FastMCP OAuth flow first
        result = await super().verify_token(token)
        if result is not None:
            return result

        # Fall back to direct Entra ID token validation
        logger.debug("Native OAuth verification failed, trying direct Entra ID token")
        return await self._entra_verifier.verify_token(token)


auth_provider = DualAuthProvider(
    client_id=os.environ.get("CLIENT_ID"),
    client_secret=os.environ.get("CLIENT_SECRET"),
    tenant_id=os.environ.get("TENANT_ID"),
    base_url=os.environ.get("BASE_URL"),
    required_scopes=["user_impersonation"],
)


mcp = FastMCP("FastMCP call Foundry Agent", auth=auth_provider)

PROJECT_ENDPOINT = os.environ.get("PROJECT_ENDPOINT", "")
AGENT_ID = os.environ.get("AGENT_ID", "")
CLIENT_ID = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
TENANT_ID = os.environ.get("TENANT_ID", "")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPE = os.environ.get("FOUNDRY_SCOPE", "https://ai.azure.com/.default")


class OboTokenCredential(TokenCredential):
    """Wraps a static access token obtained via the OBO flow."""

    def __init__(self, access_token: str, expires_on: int):
        self._token = AccessToken(access_token, expires_on)

    def get_token(self, *scopes, **kwargs):
        return self._token


def _get_obo_credential(user_token: str) -> OboTokenCredential:
    """Exchange a user token for a Foundry-scoped token via OBO flow."""
    logger.info("Creating ConfidentialClientApplication (client_id=%s, authority=%s)", CLIENT_ID, AUTHORITY)
    app = ConfidentialClientApplication(
        client_id=CLIENT_ID,
        client_credential=CLIENT_SECRET,
        authority=AUTHORITY,
    )

    logger.info("Requesting OBO token (scope=%s)", SCOPE)
    result = app.acquire_token_on_behalf_of(
        user_assertion=user_token,
        scopes=[SCOPE],
    )

    if "access_token" not in result:
        error = result.get("error_description", result.get("error", "Unknown error"))
        logger.error("OBO token acquisition failed: %s (correlation_id=%s)", error, result.get("correlation_id"))
        raise RuntimeError(f"OBO token acquisition failed: {error}")

    logger.info(
        "OBO token acquired (expires_in=%s, token_type=%s, scope=%s)",
        result.get("expires_in"),
        result.get("token_type"),
        result.get("scope"),
    )
    return OboTokenCredential(result["access_token"], result.get("expires_in", 3600))


def handle_responses(agent_name: str, openai_client, response, conversation_id: str) -> str:
    print("Handling response...")
    input_list: ResponseInputParam = []
    for item in response.output:
        if item.type == "mcp_approval_request":
            input_list.append(
                McpApprovalResponse(
                    type="mcp_approval_response",
                    approve=True,
                    approval_request_id=item.id,
                )
            )

    print("Final input:")
    print(input_list)
    if len(input_list) > 0:
        response = openai_client.responses.create(
            input=input_list,
            conversation=conversation_id,
            extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
        )
        return handle_responses(agent_name, openai_client, response, conversation_id)
    else:
        print(f"Returning output text: {response.output_text}")
        return response.output_text

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

    access_token = get_access_token()
    if not access_token:
        logger.error("No access token found in request")
        return "Error: No access token found."

    user_token = access_token.token
    logger.info("User token received (length=%d)", len(user_token))

    ctx.info("Performing On-Behalf-Of token exchange")
    try:
        credential = _get_obo_credential(user_token)
    except RuntimeError as e:
        logger.error("OBO credential error: %s", e)
        return f"Error: {e}"

    logger.info("Connecting to Foundry project at %s", PROJECT_ENDPOINT)
    project_client = AIProjectClient(credential=credential, endpoint=PROJECT_ENDPOINT)
    with project_client.get_openai_client() as openai_client:

        conversation = openai_client.conversations.create()
        
        response = openai_client.responses.create(
            conversation=conversation.id,
            input=message,
            extra_body={"agent_reference": {"name": AGENT_ID, "type": "agent_reference"}},
        )
        
        output_text = handle_responses(AGENT_ID, openai_client, response, conversation.id)
        return output_text

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)