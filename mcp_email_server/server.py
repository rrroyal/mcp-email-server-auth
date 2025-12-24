import contextlib
from os import getenv

from mcp.server.fastmcp.server import FastMCP
from starlette.applications import Starlette
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
    SimpleUser,
)
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Mount


class BasicAuthBackend(AuthenticationBackend):
    async def authenticate(self, conn):
        configured_token = getenv("MCP_AUTH_TOKEN")
        if not configured_token:
            # No authorization configured, don't require authentication
            return

        if "Authorization" not in conn.headers:
            raise AuthenticationError("No authorization header")

        auth = conn.headers["Authorization"]
        scheme, credentials = auth.split()
        if scheme.lower() != "bearer":
            raise AuthenticationError("Invalid authorization header")

        if credentials != configured_token:
            raise AuthenticationError("Invalid token")

        return AuthCredentials(["authenticated"]), SimpleUser("user")

def create_starlette(mcp: FastMCP, path: str, mcp_app: Starlette) -> Starlette:
    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with contextlib.AsyncExitStack() as stack:
            await stack.enter_async_context(mcp.session_manager.run())
            yield

    return Starlette(
        routes=[
            Mount(path, mcp_app),
        ],
        middleware=[
            Middleware(AuthenticationMiddleware, backend=BasicAuthBackend())
        ],
        lifespan=lifespan
    )
