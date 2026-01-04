from mcp_email_server.app import mcp
from mcp_email_server.server import create_starlette

# mcp.settings.host = "0.0.0.0"
# mcp.settings.port = 8000
mcp.settings.streamable_http_path = "/"
mcp.settings.stateless_http = True

mcp_app = mcp.streamable_http_app()

app = create_starlette(mcp, "/mcp", mcp_app)
