# Session Management Integration Guide

This document explains how session management is integrated into the MCP email server architecture.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        MCP Email Server                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                    server.py                               │  │
│  │  - Starlette lifespan management                          │  │
│  │  - Handler registry & cleanup                             │  │
│  └───────────────┬───────────────────────────────────────────┘  │
│                  │                                                │
│                  ▼                                                │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                 dispatcher.py                              │  │
│  │  - dispatch_handler()                                      │  │
│  │  - Chooses handler based on config                        │  │
│  └───────────────┬───────────────────────────────────────────┘  │
│                  │                                                │
│      ┌───────────┴───────────┐                                   │
│      ▼                       ▼                                   │
│  ┌─────────────────┐  ┌─────────────────────────────────────┐  │
│  │ ClassicEmail    │  │ SessionManagedEmailHandler          │  │
│  │ Handler         │  │  - Uses configured settings         │  │
│  │ (fallback)      │  │  - Auto cleanup on close()          │  │
│  └─────────────────┘  └──────────┬──────────────────────────┘  │
│                                   │                               │
│                                   ▼                               │
│                   ┌────────────────────────────────────┐         │
│                   │ SessionManagedEmailClient          │         │
│                   │  - Accepts config parameters       │         │
│                   │  - Pulls from settings if not set  │         │
│                   └──────────┬─────────────────────────┘         │
│                              │                                    │
│                              ▼                                    │
│                   ┌────────────────────────────────────┐         │
│                   │      SessionManager                │         │
│                   │  - Auto reconnection               │         │
│                   │  - Retry with backoff              │         │
│                   │  - Session validation              │         │
│                   └────────────────────────────────────┘         │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

## Component Details

### 1. Configuration Layer (`config.py`)

**Purpose**: Centralized configuration management for session settings

**Features**:
- Global settings with defaults
- Environment variable support
- Runtime configuration

**Settings**:
```python
class Settings(BaseSettings):
    use_session_management: bool = True
    session_max_retries: int = 3
    session_initial_backoff: float = 1.0
    session_max_backoff: float = 30.0
    session_timeout: int = 1800
```

**Environment Variables**:
- `MCP_EMAIL_SERVER_USE_SESSION_MANAGEMENT`
- `MCP_EMAIL_SERVER_SESSION_MAX_RETRIES`
- `MCP_EMAIL_SERVER_SESSION_INITIAL_BACKOFF`
- `MCP_EMAIL_SERVER_SESSION_MAX_BACKOFF`
- `MCP_EMAIL_SERVER_SESSION_TIMEOUT`

### 2. Dispatcher Layer (`dispatcher.py`)

**Purpose**: Route requests to appropriate email handler based on configuration

**Flow**:
```python
def dispatch_handler(account_name: str) -> EmailHandler:
    settings = get_settings()
    account = settings.get_account(account_name)
    
    use_session_mgmt = getattr(settings, 'use_session_management', True)
    
    if use_session_mgmt:
        return SessionManagedEmailHandler(account)
    else:
        return ClassicEmailHandler(account)  # Fallback
```

**Key Features**:
- ✅ Zero-config by default (session management enabled)
- ✅ Respects global configuration
- ✅ Graceful fallback to classic handler

### 3. Session-Managed Client Layer (`classic_with_session_mgmt.py`)

**Purpose**: Email operations with automatic retry and reconnection

#### SessionManagedEmailClient

**Initialization**:
```python
def __init__(
    self,
    email_server: EmailServer,
    sender: str | None = None,
    max_retries: int | None = None,
    initial_backoff: float | None = None,
    max_backoff: float | None = None,
    session_timeout: int | None = None,
):
    # Get configuration from settings if not provided
    settings = get_settings()
    max_retries = max_retries if max_retries is not None else settings.session_max_retries
    # ...
```

**Configuration Priority**:
1. Explicit parameters (for testing/override)
2. Global settings from `config.py`
3. Default values

#### SessionManagedEmailHandler

**Responsibilities**:
- Manages incoming and outgoing email clients
- Applies global configuration to both clients
- Provides health check endpoint
- Ensures proper cleanup via `close()`

**Usage**:
```python
handler = SessionManagedEmailHandler(email_settings)
try:
    emails = await handler.get_emails_metadata(page=1, page_size=10)
    health = await handler.get_connection_health()
finally:
    await handler.close()  # Cleanup
```

### 4. Session Manager Layer (`session_manager.py`)

**Purpose**: Low-level connection management and retry logic

**Key Features**:
- Connection pooling (one connection per manager)
- Automatic reconnection on errors
- Exponential backoff retry
- Session validation via NOOP
- Timeout management

**Retry Flow**:
```
Operation → Execute → Success ✅
              │
              ▼ (Error)
           Is Retryable? → No → Raise ❌
              │
              ▼ Yes
           Close Connection
              │
              ▼
           Wait (Backoff)
              │
              ▼
           Retry (up to max_retries)
```

### 5. Lifecycle Management Layer (`server.py`)

**Purpose**: Ensure proper cleanup of handlers on server shutdown

**Features**:
- Handler registry
- Automatic cleanup on shutdown
- Integrated with Starlette lifespan

**Flow**:
```python
@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(mcp.session_manager.run())
        
        settings = get_settings()
        if settings.use_session_management:
            logger.info("Session management enabled")
        
        try:
            yield  # Server runs
        finally:
            await cleanup_handlers()  # Cleanup all handlers
```

## Data Flow

### Successful Email Fetch

```
User Request
    │
    ▼
dispatch_handler()
    │
    ▼
SessionManagedEmailHandler.get_emails_metadata()
    │
    ▼
SessionManagedEmailClient.get_emails_metadata_stream()
    │
    ▼
SessionManager.execute_with_retry()
    │
    ├─→ get_connection() → Validate session
    │       │
    │       ├─→ Valid? Use existing
    │       └─→ Invalid? Create new
    │
    └─→ Execute operation
            │
            ▼
         Success ✅
```

### Failed Operation with Retry

```
User Request
    │
    ▼
SessionManager.execute_with_retry()
    │
    ├─→ Attempt 1 → Error (invalid session ID)
    │       │
    │       └─→ Is retryable? Yes
    │               │
    │               ├─→ Close connection
    │               ├─→ Wait 1.0s
    │               └─→ Retry
    │
    ├─→ Attempt 2 → Error (timeout)
    │       │
    │       └─→ Is retryable? Yes
    │               │
    │               ├─→ Close connection
    │               ├─→ Wait 2.0s
    │               └─→ Retry
    │
    └─→ Attempt 3 → Success ✅
```

## Configuration Scenarios

### Scenario 1: Default (Zero Config)

```bash
# No configuration needed
# Session management enabled with sensible defaults
```

**Behavior**:
- Uses `SessionManagedEmailHandler`
- 3 retries, 1s initial backoff, 30s max backoff
- 30-minute session timeout

### Scenario 2: Aggressive Retry (Unreliable Networks)

```bash
export MCP_EMAIL_SERVER_SESSION_MAX_RETRIES=5
export MCP_EMAIL_SERVER_SESSION_INITIAL_BACKOFF=2.0
export MCP_EMAIL_SERVER_SESSION_MAX_BACKOFF=60.0
export MCP_EMAIL_SERVER_SESSION_TIMEOUT=900
```

**Behavior**:
- More aggressive retry strategy
- Longer backoff times
- Shorter session timeout (reconnect more often)

### Scenario 3: Minimal Retry (Stable Connections)

```bash
export MCP_EMAIL_SERVER_SESSION_MAX_RETRIES=2
export MCP_EMAIL_SERVER_SESSION_INITIAL_BACKOFF=0.5
export MCP_EMAIL_SERVER_SESSION_MAX_BACKOFF=10.0
export MCP_EMAIL_SERVER_SESSION_TIMEOUT=3600
```

**Behavior**:
- Fewer retries (fail faster)
- Shorter backoff times
- Longer session timeout (stable connections)

### Scenario 4: Disabled (Testing/Troubleshooting)

```bash
export MCP_EMAIL_SERVER_USE_SESSION_MANAGEMENT=false
```

**Behavior**:
- Falls back to `ClassicEmailHandler`
- No automatic retry
- Manual reconnection required

## Error Handling

### Retryable Errors

The following errors trigger automatic retry:
- "invalid session ID"
- "session expired"
- "connection lost" / "connection reset"
- "timeout" / "timed out"
- "broken pipe"
- "EOF occurred"
- "command bye"

### Non-Retryable Errors

These errors are raised immediately:
- Authentication failures
- Invalid credentials
- Mailbox does not exist
- Permission denied
- Quota exceeded

## Testing

### Unit Tests

```python
# Test configuration loading
def test_session_config_from_env():
    os.environ['MCP_EMAIL_SERVER_SESSION_MAX_RETRIES'] = '5'
    settings = get_settings(reload=True)
    assert settings.session_max_retries == 5

# Test dispatcher routing
def test_dispatcher_uses_session_handler():
    settings = get_settings()
    handler = dispatch_handler("test_account")
    assert isinstance(handler, SessionManagedEmailHandler)

# Test fallback
def test_dispatcher_fallback_to_classic():
    settings = get_settings()
    settings.use_session_management = False
    handler = dispatch_handler("test_account")
    assert isinstance(handler, ClassicEmailHandler)
```

### Integration Tests

```python
# Test automatic reconnection
async def test_auto_reconnection():
    handler = SessionManagedEmailHandler(email_settings)
    
    # First operation should succeed
    emails = await handler.get_emails_metadata(page=1, page_size=10)
    assert len(emails.emails) > 0
    
    # Simulate session expiry (would happen naturally over time)
    # Next operation should automatically reconnect
    more_emails = await handler.get_emails_metadata(page=2, page_size=10)
    assert len(more_emails.emails) >= 0
    
    await handler.close()

# Test cleanup
async def test_handler_cleanup():
    handler = SessionManagedEmailHandler(email_settings)
    register_handler(handler)
    
    # Do some operations
    await handler.get_emails_metadata(page=1, page_size=10)
    
    # Cleanup should close all handlers
    await cleanup_handlers()
    
    # Subsequent operations should fail (connection closed)
    with pytest.raises(Exception):
        await handler.get_emails_metadata(page=1, page_size=10)
```

## Best Practices

### 1. Use Default Configuration

For most use cases, the default configuration is optimal:
```python
# No configuration needed
handler = dispatch_handler("my_account")
```

### 2. Tune for Your Network

Adjust settings based on your network reliability:
```bash
# Unreliable network (e.g., mobile)
export MCP_EMAIL_SERVER_SESSION_MAX_RETRIES=5
export MCP_EMAIL_SERVER_SESSION_INITIAL_BACKOFF=2.0

# Stable network (e.g., datacenter)
export MCP_EMAIL_SERVER_SESSION_MAX_RETRIES=2
export MCP_EMAIL_SERVER_SESSION_INITIAL_BACKOFF=0.5
```

### 3. Monitor Health

Use the health check endpoint:
```python
health = await handler.get_connection_health()
if not health['healthy']:
    logger.warning(f"Connection issue: {health['error']}")
    # Maybe alert monitoring system
```

### 4. Always Clean Up

Ensure handlers are properly closed:
```python
handler = dispatch_handler("my_account")
try:
    # Do work
    await handler.get_emails_metadata(page=1, page_size=10)
finally:
    await handler.close()  # Always cleanup
```

### 5. Test Fallback Path

Periodically test with session management disabled:
```bash
export MCP_EMAIL_SERVER_USE_SESSION_MANAGEMENT=false
# Run your application
# Verify classic handler still works
```

## Troubleshooting

### Problem: Too Many Retries

**Symptom**: Operations take too long to fail

**Solution**:
```bash
export MCP_EMAIL_SERVER_SESSION_MAX_RETRIES=2
export MCP_EMAIL_SERVER_SESSION_MAX_BACKOFF=10.0
```

### Problem: Session Expiring Too Quickly

**Symptom**: Frequent reconnections in logs

**Solution**:
```bash
export MCP_EMAIL_SERVER_SESSION_TIMEOUT=3600  # 1 hour
```

### Problem: Operations Failing Despite Retries

**Symptom**: All retries exhausted, permanent failure

**Solution**:
1. Check credentials
2. Verify IMAP server is accessible
3. Check for rate limiting
4. Review error logs for non-retryable errors

### Problem: Memory Usage Growing

**Symptom**: Memory usage increases over time

**Solution**:
1. Ensure handlers are being closed: `await handler.close()`
2. Verify cleanup is called on shutdown
3. Check for handler leaks in your code

## Migration Guide

### From Classic Handler to Session-Managed Handler

**Before**:
```python
from mcp_email_server.emails.classic import ClassicEmailHandler

handler = ClassicEmailHandler(email_settings)
```

**After**:
```python
from mcp_email_server.emails.dispatcher import dispatch_handler

# Automatically uses SessionManagedEmailHandler
handler = dispatch_handler(account_name)
```

**Or explicit**:
```python
from mcp_email_server.emails.classic_with_session_mgmt import SessionManagedEmailHandler

handler = SessionManagedEmailHandler(email_settings)
```

### Rolling Back

If you need to roll back to classic handler:

```bash
# Option 1: Environment variable (temporary)
export MCP_EMAIL_SERVER_USE_SESSION_MANAGEMENT=false

# Option 2: Code change (permanent)
from mcp_email_server.emails.classic import ClassicEmailHandler
handler = ClassicEmailHandler(email_settings)
```

## Summary

Session management is now fully integrated into the MCP email server with:

✅ **Zero-config defaults** - Works out of the box with sensible settings  
✅ **Configurable** - Tune via environment variables or code  
✅ **Automatic cleanup** - Handlers properly closed on shutdown  
✅ **Backward compatible** - Can disable or use classic handler  
✅ **Production-ready** - Tested with iCloud and other IMAP providers  

The integration provides transparent retry and reconnection while maintaining the existing API surface.
