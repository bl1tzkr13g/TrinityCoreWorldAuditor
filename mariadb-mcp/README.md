# Official MariaDB MCP — Portainer deployment

This stack builds the **official [MariaDB/mcp](https://github.com/MariaDB/mcp)** project at Docker build time. It does not run another MariaDB database. Its SQL connection targets your existing MariaDB host.

## Portainer Git stack

- Repository: https://github.com/bl1tzkr13g/TrinityCoreWorldAuditor
- Reference: refs/heads/main
- Compose path: mariadb-mcp/compose.yaml
- Environment variables: DB_HOST=10.0.0.115, DB_PORT=3306, DB_USER=world_auditor, DB_PASSWORD=(your secret), DB_NAME=world

**Important:** This Compose stack uses a repository-local build context. Some Portainer versions cannot build a Git stack directly. If the stack reports a build-context error, use the terminal to clone the repository and run `sudo docker compose -f mariadb-mcp/compose.yaml up -d --build`; the resulting container is still visible in Portainer.

Port 9001 is bound to **127.0.0.1 only** until authentication and HTTPS are configured. Do not forward this unauthenticated service to the internet. The official server does not provide default HTTP authentication. For remote ChatGPT-compatible MCP access, use an authenticated HTTPS proxy or supported Secure MCP Tunnel, subject to account capabilities.

Local smoke test on the Docker VM: `curl -i http://127.0.0.1:9001/mcp` (an MCP protocol error or 405 may indicate the server is reachable; inspect `sudo docker compose -f mariadb-mcp/compose.yaml logs` for details). Use an MCP client for a full tools/list test.

This stack is read-only by both configuration and the database user's SELECT-only privileges. Editing live tables requires a separate approved write-capable user and appropriate MCP tools. 
