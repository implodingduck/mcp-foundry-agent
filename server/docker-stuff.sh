docker build -t mcp-foundry-agent .

docker stop mcp-foundry-agent
docker rm mcp-foundry-agent

docker run -d -p 8000:8000 --env-file .env -v ~/.azure:/root/.azure -v ~/.local/share/msal_token_cache:/root/.local/share/msal_token_cache:ro --name mcp-foundry-agent mcp-foundry-agent
docker logs -f mcp-foundry-agent