# Session Management for MCP Email Server

## Overview

This document describes the session management improvements added to the MCP Email Server to address recurring "invalid session ID" errors and connection timeouts, particularly with iCloud and other email providers.

## Problem Statement

IMAP connections can fail for various reasons:
- **Session Expiration**: Email servers (especially iCloud) terminate idle sessions after a period of inactivity
- **Invalid Session ID**: Sessions become invalid due to network issues or server-side timeouts
- **Connection Timeouts**: Network interruptions or slow responses cause timeouts
- **Broken Pipes**: TCP connections drop unexpectedly

These issues required manual reconnection and disrupted workflow, particularly problematic for long-running applications.

## Solution: Automated Session Management

### Core Components

#### 1. SessionManager (`mcp_email_server/session_manager.py`)

The `SessionManager` class provides:

- **Automatic Reconnection**: Detects invalid sessions and reconnects automatically
- **Retry Logic with Exponential Backoff**: Retries failed operations with increasing delays
- **Session Health Monitoring**: Validates session state before operations
- **Connection Pooling**: Reuses valid connections to minimize overhead

**Key Features:**
```python
SessionManager(
    imap_class=aioimaplib.IMAP4_SSL,
    host="imap.mail.me.com",
    port=993,
    username="your_email@icloud.com",
    password="app_specific_password",
    max_retries=3,              # Retry up to 3 times
    initial_backoff=1.0,        # Start with 1 second delay
    max_backoff=30.0,           # Max delay of 30 seconds
    session_timeout=1800        # 30-minute session timeout
)
```

#### 2. Error Detection

The session manager automatically detects retryable errors:

- "invalid session ID"
- "session expired"
- "connection lost"
- "connection reset"
- "timeout" / "timed out"
- "broken pipe"
- "EOF occurred"
- "command bye"

#### 3. Exponential Backoff

Retry delays increase exponentially to avoid overwhelming the server:

- Attempt 1: 1 second delay
- Attempt 2: 2 seconds delay
- Attempt 3: 4 seconds delay
- Maximum: 30 seconds

#### 4. Connection Health Check

The `ConnectionHealthCheck` class provides:

```python
health_info = await health_check.check_health()
# Returns:
# {
#     "healthy": True/False,
#     "timestamp": "2026-01-08T12:09:00",
#     "host": "imap.mail.me.com",
#     "port": 993,
#     "response_time_ms": 234.56,
#     "last_activity": "2026-01-08T12:08:45",
#     "error": None
# }
```

## Implementation

### Using SessionManagedEmailClient

The new `SessionManagedEmailClient` wraps all IMAP operations with automatic retry:

```python
from mcp_email_server.emails.classic_with_session_mgmt import SessionManagedEmailClient

client = SessionManagedEmailClient(email_server)

try:
    # Operations automatically retry on session errors
    count = await client.get_email_count(mailbox="INBOX")
    emails = []
    async for email in client.get_emails_metadata_stream(page=1, page_size=10):
        emails.append(email)
        
    # Check connection health
    health = await client.health_check.check_health()
    print(f"Connection healthy: {health['healthy']}")
    
finally:
    await client.close()  # Clean up connections
```

### Migration from Classic Client

**Before (Classic):**
```python
from mcp_email_server.emails.classic import EmailClient

client = EmailClient(email_server)
try:
    count = await client.get_email_count()
except Exception as e:
    # Manual error handling required
    logger.error(f"Failed: {e}")
```

**After (Session-Managed):**
```python
from mcp_email_server.emails.classic_with_session_mgmt import SessionManagedEmailClient

client = SessionManagedEmailClient(email_server)
# Automatic retry on session errors
count = await client.get_email_count()
```

## Configuration

### Tuning Parameters

Adjust session management parameters based on your needs:

**For Stable Connections:**
```python
SessionManager(
    max_retries=2,          # Fewer retries
    initial_backoff=0.5,    # Shorter delays
    max_backoff=10.0,
    session_timeout=3600    # 1-hour timeout
)
```

**For Unreliable Connections:**
```python
SessionManager(
    max_retries=5,          # More retries
    initial_backoff=2.0,    # Longer initial delay
    max_backoff=60.0,       # Allow longer waits
    session_timeout=900     # 15-minute timeout (shorter for faster detection)
)
```

**For iCloud (Recommended):**
```python
SessionManager(
    max_retries=3,
    initial_backoff=1.0,
    max_backoff=30.0,
    session_timeout=1800    # 30 minutes
)
```

## Benefits

### 1. Reliability
- Automatically recovers from transient network issues
- Handles server-side session timeouts gracefully
- Reduces manual intervention required

### 2. Performance
- Reuses valid connections when possible
- Minimizes reconnection overhead
- Implements intelligent backoff to avoid overwhelming servers

### 3. User Experience
- Transparent error recovery
- No disruption to ongoing operations
- Clear health status reporting

### 4. Debugging
- Detailed logging of retry attempts
- Health check endpoint for monitoring
- Error categorization (retryable vs. non-retryable)

## Best Practices

### 1. Always Close Clients

```python
client = SessionManagedEmailClient(email_server)
try:
    # Your operations
    pass
finally:
    await client.close()
```

### 2. Monitor Health Status

```python
health = await client.health_check.check_health()
if not health['healthy']:
    logger.warning(f"Connection unhealthy: {health['error']}")
```

### 3. Handle Non-Retryable Errors

```python
try:
    result = await client.get_email_count()
except ValueError as e:
    # Non-retryable error (e.g., invalid mailbox)
    logger.error(f"Configuration error: {e}")
except Exception as e:
    # All retries exhausted
    logger.error(f"Operation failed after retries: {e}")
```

### 4. Use Appropriate Timeouts

For long-running batch operations, ensure session timeout is sufficient:

```python
# Processing 1000 emails, estimate 10 seconds each
# Set timeout to at least 3 hours
SessionManager(session_timeout=10800)
```

## Logging

The session manager provides detailed logging:

```
INFO: Creating new IMAP connection to imap.mail.me.com:993
INFO: IMAP connection established successfully
INFO: Executing get_email_count (attempt 1/3)
WARNING: get_email_count failed (attempt 1/3): invalid session ID
INFO: Retrying get_email_count in 1.0s...
INFO: Executing get_email_count (attempt 2/3)
INFO: Count: Search criteria: ['ALL']
```

## Testing

### Unit Tests

```python
import pytest
from mcp_email_server.session_manager import SessionManager

@pytest.mark.asyncio
async def test_session_recovery():
    session_mgr = SessionManager(...)
    
    # Simulate session expiration
    await session_mgr._close_connection()
    
    # Should automatically reconnect
    result = await session_mgr.execute_with_retry(
        lambda imap: imap.noop(),
        "test_operation"
    )
    assert result[0] == "OK"
```

### Integration Tests

```python
@pytest.mark.asyncio
async def test_icloud_compatibility():
    client = SessionManagedEmailClient(icloud_server)
    
    # Should handle iCloud-specific issues
    count = await client.get_email_count(mailbox="INBOX")
    assert count >= 0
    
    health = await client.health_check.check_health()
    assert health['healthy'] is True
```

## Troubleshooting

### Issue: Still Getting "Invalid Session ID"

**Solution:**
- Check if session_timeout is too long
- Verify credentials are correct
- Ensure network connectivity is stable
- Check server logs for specific errors

### Issue: Excessive Retries

**Solution:**
- Reduce max_retries
- Increase initial_backoff
- Check if errors are truly transient
- Verify server is not rate-limiting

### Issue: Slow Performance

**Solution:**
- Reduce session_timeout for faster detection
- Decrease max_backoff
- Check network latency
- Monitor health check response times

## Future Enhancements

Potential improvements for future releases:

1. **Connection Pool**: Support multiple concurrent connections
2. **Circuit Breaker**: Temporarily disable retries after repeated failures
3. **Metrics**: Collect statistics on retry rates and success rates
4. **Adaptive Backoff**: Adjust backoff based on server response patterns
5. **Provider Profiles**: Pre-configured settings for common providers

## References

- [IMAP RFC 3501](https://www.rfc-editor.org/rfc/rfc3501.html)
- [aioimaplib Documentation](https://aioimaplib.readthedocs.io/)
- [Exponential Backoff Algorithm](https://en.wikipedia.org/wiki/Exponential_backoff)

## Support

For issues or questions about session management:

1. Check logs for detailed error messages
2. Verify configuration parameters
3. Test connection health
4. Review this documentation
5. File an issue on GitHub with logs and configuration details
