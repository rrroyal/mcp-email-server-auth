from os import getenv

from mcp.server.transport_security import TransportSecuritySettings

from mcp_email_server.app import mcp
from mcp_email_server.server_utils import create_starlette

mcp.settings.streamable_http_path = "/"
mcp.settings.stateless_http = True

allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
allowed_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
if vercel_url := getenv("VERCEL_URL"):
    allowed_hosts.append(vercel_url)
    allowed_origins.append(f"https://{vercel_url}")
if vercel_branch_url := getenv("VERCEL_BRANCH_URL"):
    allowed_hosts.append(vercel_branch_url)
    allowed_origins.append(f"https://{vercel_branch_url}")
if vercel_project_production_url := getenv("VERCEL_PROJECT_PRODUCTION_URL"):
    allowed_hosts.append(vercel_project_production_url)
    allowed_origins.append(f"https://{vercel_project_production_url}")

mcp.settings.transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=allowed_hosts,
    allowed_origins=allowed_origins,
)

app = create_starlette(mcp, "/mcp", mcp.streamable_http_app())
