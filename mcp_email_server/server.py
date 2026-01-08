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

from mcp_email_server.config import get_settings
from mcp_email_server.emails.classic_with_session_mgmt import SessionManagedEmailHandler
from mcp_email_server.log import logger

# Global registry to track active handlers for cleanup
_active_handlers: list[SessionManagedEmailHandler] = []


def register_handler(handler: SessionManagedEmailHandler) -> None:
    """Register a handler for lifecycle management."""
    if handler not in _active_handlers:
        _active_handlers.append(handler)
        logger.debug(f"Registered handler for cleanup: {handler}")


async def cleanup_handlers() -> None:
    """Clean up all registered session-managed handlers."""
    logger.info(f"Cleaning up {len(_active_handlers)} registered handlers")
    for handler in _active_handlers:
        try:
            await handler.close()
            logger.debug(f"Closed handler: {handler}")
        except Exception as e:
            logger.error(f"Error closing handler: {e}")
    _active_handlers.clear()


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
            # Start MCP session manager
            await stack.enter_async_context(mcp.session_manager.run())
            
            # Register cleanup for session-managed handlers
            settings = get_settings()
            if settings.use_session_management:
                logger.info("Session management is enabled, handlers will be registered for cleanup")
            
            try:
                yield
            finally:
                # Clean up all registered session-managed handlers on shutdown
                await cleanup_handlers()

    return Starlette(
        routes=[
            Mount(path, mcp_app),
        ],
        middleware=[
            Middleware(AuthenticationMiddleware, backend=BasicAuthBackend())
        ],
        lifespan=lifespan
    )
