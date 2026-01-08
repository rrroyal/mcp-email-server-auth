"""Session management for IMAP connections with auto-reconnection and retry logic.

This module provides robust session management to handle connection issues,
timeouts, and "invalid session ID" errors that commonly occur with iCloud
and other email providers.
"""

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, TypeVar

import aioimaplib

from mcp_email_server.log import logger

T = TypeVar("T")


class SessionManager:
    """Manages IMAP session lifecycle with automatic reconnection and retry logic."""

    def __init__(
        self,
        imap_class: type[aioimaplib.IMAP4 | aioimaplib.IMAP4_SSL],
        host: str,
        port: int,
        username: str,
        password: str,
        max_retries: int = 3,
        initial_backoff: float = 1.0,
        max_backoff: float = 30.0,
        session_timeout: int = 1800,  # 30 minutes
    ):
        """Initialize the session manager.

        Args:
            imap_class: IMAP4 or IMAP4_SSL class to use
            host: IMAP server hostname
            port: IMAP server port
            username: Account username
            password: Account password
            max_retries: Maximum number of retry attempts
            initial_backoff: Initial backoff delay in seconds
            max_backoff: Maximum backoff delay in seconds
            session_timeout: Session timeout in seconds (default: 30 minutes)
        """
        self.imap_class = imap_class
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.max_retries = max_retries
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.session_timeout = session_timeout

        self._imap: aioimaplib.IMAP4 | aioimaplib.IMAP4_SSL | None = None
        self._last_activity: datetime | None = None
        self._connection_lock = asyncio.Lock()

    async def _create_connection(self) -> aioimaplib.IMAP4 | aioimaplib.IMAP4_SSL:
        """Create a new IMAP connection.

        Returns:
            Connected IMAP client

        Raises:
            Exception: If connection fails
        """
        logger.info(f"Creating new IMAP connection to {self.host}:{self.port}")
        imap = self.imap_class(self.host, self.port)
        
        try:
            await imap._client_task
            await imap.wait_hello_from_server()
            await imap.login(self.username, self.password)
            logger.info("IMAP connection established successfully")
            return imap
        except Exception as e:
            logger.error(f"Failed to create IMAP connection: {e}")
            try:
                await imap.logout()
            except Exception:
                pass
            raise

    async def _is_session_valid(self) -> bool:
        """Check if the current session is still valid.

        Returns:
            True if session is valid, False otherwise
        """
        if self._imap is None:
            return False

        # Check if session has timed out
        if self._last_activity:
            elapsed = (datetime.now() - self._last_activity).total_seconds()
            if elapsed > self.session_timeout:
                logger.warning(f"Session timed out (elapsed: {elapsed}s)")
                return False

        # Try a simple NOOP command to check if connection is alive
        try:
            result = await asyncio.wait_for(self._imap.noop(), timeout=5.0)
            if result[0] == "OK":
                return True
            logger.warning(f"Session validation failed: {result}")
            return False
        except asyncio.TimeoutError:
            logger.warning("Session validation timed out")
            return False
        except Exception as e:
            logger.warning(f"Session validation error: {e}")
            return False

    async def _close_connection(self) -> None:
        """Close the current IMAP connection."""
        if self._imap is not None:
            try:
                await self._imap.logout()
                logger.info("IMAP connection closed")
            except Exception as e:
                logger.debug(f"Error during logout: {e}")
            finally:
                self._imap = None
                self._last_activity = None

    async def get_connection(self) -> aioimaplib.IMAP4 | aioimaplib.IMAP4_SSL:
        """Get a valid IMAP connection, creating or reconnecting as needed.

        Returns:
            Active IMAP connection

        Raises:
            Exception: If unable to establish connection after retries
        """
        async with self._connection_lock:
            # Check if current session is valid
            if not await self._is_session_valid():
                # Close old connection if exists
                await self._close_connection()
                
                # Create new connection
                self._imap = await self._create_connection()
            
            self._last_activity = datetime.now()
            return self._imap

    async def close(self) -> None:
        """Close the session manager and any open connections."""
        async with self._connection_lock:
            await self._close_connection()

    def _is_retryable_error(self, error: Exception) -> bool:
        """Determine if an error is retryable.

        Args:
            error: The exception that occurred

        Returns:
            True if the error should trigger a retry
        """
        error_str = str(error).lower()
        
        retryable_patterns = [
            "invalid session id",
            "session expired",
            "connection lost",
            "connection reset",
            "timeout",
            "timed out",
            "broken pipe",
            "eof occurred",
            "command bye",
        ]
        
        return any(pattern in error_str for pattern in retryable_patterns)

    async def execute_with_retry(
        self,
        operation: Callable[[aioimaplib.IMAP4 | aioimaplib.IMAP4_SSL], Any],
        operation_name: str = "operation",
    ) -> T:
        """Execute an IMAP operation with automatic retry and exponential backoff.

        Args:
            operation: Async function that takes an IMAP connection and returns a result
            operation_name: Name of the operation for logging

        Returns:
            The result of the operation

        Raises:
            Exception: If all retry attempts fail
        """
        last_error = None
        backoff = self.initial_backoff

        for attempt in range(self.max_retries):
            try:
                # Get a valid connection
                imap = await self.get_connection()
                
                # Execute the operation
                logger.debug(f"Executing {operation_name} (attempt {attempt + 1}/{self.max_retries})")
                result = await operation(imap)
                
                # Update last activity time
                self._last_activity = datetime.now()
                
                return result

            except Exception as e:
                last_error = e
                logger.warning(f"{operation_name} failed (attempt {attempt + 1}/{self.max_retries}): {e}")

                # Check if we should retry
                if not self._is_retryable_error(e):
                    logger.error(f"Non-retryable error in {operation_name}: {e}")
                    raise

                # Close the connection on error
                await self._close_connection()

                # If this was the last attempt, raise the error
                if attempt == self.max_retries - 1:
                    logger.error(f"{operation_name} failed after {self.max_retries} attempts")
                    raise

                # Wait with exponential backoff before retrying
                logger.info(f"Retrying {operation_name} in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.max_backoff)

        # This should not be reached, but just in case
        raise last_error if last_error else Exception(f"{operation_name} failed")


class ConnectionHealthCheck:
    """Provides health check functionality for IMAP connections."""

    def __init__(self, session_manager: SessionManager):
        """Initialize health checker.

        Args:
            session_manager: The session manager to check
        """
        self.session_manager = session_manager

    async def check_health(self) -> dict[str, Any]:
        """Perform a health check on the IMAP connection.

        Returns:
            Dictionary with health check results
        """
        start_time = datetime.now()
        health_info = {
            "healthy": False,
            "timestamp": start_time.isoformat(),
            "host": self.session_manager.host,
            "port": self.session_manager.port,
            "response_time_ms": 0,
            "error": None,
        }

        try:
            # Try to get a connection and execute NOOP
            async def noop_operation(imap):
                return await imap.noop()

            result = await asyncio.wait_for(
                self.session_manager.execute_with_retry(noop_operation, "health_check"),
                timeout=10.0
            )

            end_time = datetime.now()
            response_time = (end_time - start_time).total_seconds() * 1000

            health_info.update({
                "healthy": result[0] == "OK",
                "response_time_ms": round(response_time, 2),
                "last_activity": self.session_manager._last_activity.isoformat() if self.session_manager._last_activity else None,
            })

        except asyncio.TimeoutError:
            health_info["error"] = "Health check timed out"
            logger.error("Health check timed out")
        except Exception as e:
            health_info["error"] = str(e)
            logger.error(f"Health check failed: {e}")

        return health_info
