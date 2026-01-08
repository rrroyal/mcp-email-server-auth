# Migration Guide: Classic to Session-Managed Email Client

## Overview

This guide helps you migrate from the classic email client to the new session-managed client with automatic reconnection and retry logic.

## Why Migrate?

The session-managed client provides:

✅ **Automatic recovery** from "invalid session ID" errors  
✅ **Retry logic** for timeout and connection issues  
✅ **Better reliability** for iCloud and other providers  
✅ **Health monitoring** with connection status checks  
✅ **Production-ready** error handling  

## Quick Migration (5 Minutes)

### Step 1: Update Import

**Before:**
```python
from mcp_email_server.emails.classic import ClassicEmailHandler
```

**After:**
```python
from mcp_email_server.emails.classic_with_session_mgmt import SessionManagedEmailHandler
```

### Step 2: Update Handler Initialization

**Before:**
```python
handler = ClassicEmailHandler(email_settings)
```

**After:**
```python
handler = SessionManagedEmailHandler(email_settings)
```

### Step 3: Add Cleanup (Important!)

**Before:**
```python
handler = ClassicEmailHandler(email_settings)
# ... use handler ...
# No explicit cleanup needed
```

**After:**
```python
handler = SessionManagedEmailHandler(email_settings)
try:
    # ... use handler ...
finally:
    await handler.close()  # Clean up sessions
```

### That's It!

All existing method calls work exactly the same. The session management happens automatically in the background.

## Detailed Migration Examples

### Example 1: Simple Email Fetch

**Before:**
```python
from mcp_email_server.emails.classic import ClassicEmailHandler

async def fetch_emails():
    handler = ClassicEmailHandler(email_settings)
    
    try:
        emails = await handler.get_emails_metadata(
            page=1,
            page_size=10,
            mailbox="INBOX"
        )
        return emails
    except Exception as e:
        # Manual error handling
        logger.error(f"Failed to fetch emails: {e}")
        return None
```

**After:**
```python
from mcp_email_server.emails.classic_with_session_mgmt import SessionManagedEmailHandler

async def fetch_emails():
    handler = SessionManagedEmailHandler(email_settings)
    
    try:
        # Automatic retry on session errors!
        emails = await handler.get_emails_metadata(
            page=1,
            page_size=10,
            mailbox="INBOX"
        )
        return emails
    except Exception as e:
        # Only called if all retries exhausted
        logger.error(f"Failed after retries: {e}")
        return None
    finally:
        await handler.close()  # Important: cleanup
```

### Example 2: Batch Processing

**Before:**
```python
from mcp_email_server.emails.classic import ClassicEmailHandler

async def process_batch():
    handler = ClassicEmailHandler(email_settings)
    
    for page in range(1, 11):
        try:
            emails = await handler.get_emails_metadata(page=page, page_size=10)
            for email in emails:
                await process_email(email)
        except Exception as e:
            logger.error(f"Page {page} failed: {e}")
            # Manual reconnection needed
            handler = ClassicEmailHandler(email_settings)
```

**After:**
```python
from mcp_email_server.emails.classic_with_session_mgmt import SessionManagedEmailHandler

async def process_batch():
    handler = SessionManagedEmailHandler(email_settings)
    
    try:
        for page in range(1, 11):
            # Automatic recovery from session errors
            emails = await handler.get_emails_metadata(page=page, page_size=10)
            for email in emails.emails:  # Note: emails is EmailMetadataPageResponse
                await process_email(email)
    except Exception as e:
        logger.error(f"Fatal error after retries: {e}")
    finally:
        await handler.close()
```

### Example 3: Long-Running Service

**Before:**
```python
from mcp_email_server.emails.classic import ClassicEmailHandler

async def email_service():
    while True:
        handler = ClassicEmailHandler(email_settings)
        try:
            await check_new_emails(handler)
        except Exception as e:
            logger.error(f"Service error: {e}")
            # Reconnect on next iteration
        await asyncio.sleep(60)
```

**After:**
```python
from mcp_email_server.emails.classic_with_session_mgmt import SessionManagedEmailHandler

async def email_service():
    handler = SessionManagedEmailHandler(email_settings)
    
    try:
        while True:
            # Check connection health
            health = await handler.get_connection_health()
            if not health['healthy']:
                logger.warning(f"Connection degraded: {health['error']}")
            
            # Automatic error recovery
            await check_new_emails(handler)
            await asyncio.sleep(60)
    finally:
        await handler.close()
```

### Example 4: Email Download with Attachments

**Before:**
```python
async def download_email(email_id: str):
    handler = ClassicEmailHandler(email_settings)
    
    try:
        # Get email content
        content = await handler.get_emails_content([email_id])
        
        # Download attachments
        for attachment_name in content.emails[0].attachments:
            await handler.download_attachment(
                email_id=email_id,
                attachment_name=attachment_name,
                save_path=f"/tmp/{attachment_name}"
            )
    except Exception as e:
        logger.error(f"Download failed: {e}")
```

**After:**
```python
async def download_email(email_id: str):
    handler = SessionManagedEmailHandler(email_settings)
    
    try:
        # Automatic retry on session errors
        content = await handler.get_emails_content([email_id])
        
        # Download attachments with retry
        for attachment_name in content.emails[0].attachments:
            result = await handler.download_attachment(
                email_id=email_id,
                attachment_name=attachment_name,
                save_path=f"/tmp/{attachment_name}"
            )
            logger.info(f"Downloaded: {result.saved_path}")
    except Exception as e:
        logger.error(f"Download failed after retries: {e}")
    finally:
        await handler.close()
```

## Configuration Options

### Default Configuration

No configuration needed - works out of the box:

```python
handler = SessionManagedEmailHandler(email_settings)
# Uses default:
# - max_retries=3
# - initial_backoff=1.0
# - max_backoff=30.0
# - session_timeout=1800 (30 minutes)
```

### Custom Configuration

If you need to tune the session manager, you can create a custom one:

```python
from mcp_email_server.session_manager import SessionManager
from mcp_email_server.emails.classic_with_session_mgmt import SessionManagedEmailClient

# Create custom session manager
session_mgr = SessionManager(
    imap_class=aioimaplib.IMAP4_SSL,
    host=email_settings.incoming.host,
    port=email_settings.incoming.port,
    username=email_settings.incoming.user_name,
    password=email_settings.incoming.password,
    max_retries=5,          # More retries
    initial_backoff=2.0,    # Longer initial delay
    max_backoff=60.0,       # Allow longer waits
    session_timeout=900     # 15-minute timeout
)

# Use custom session manager
client = SessionManagedEmailClient(email_settings.incoming)
client.session_manager = session_mgr
```

## New Features Available

### 1. Health Check Endpoint

**New capability:**
```python
handler = SessionManagedEmailHandler(email_settings)

health = await handler.get_connection_health()

print(f"Healthy: {health['healthy']}")
print(f"Response Time: {health['response_time_ms']}ms")
print(f"Last Activity: {health['last_activity']}")

if health['error']:
    print(f"Error: {health['error']}")
```

### 2. Explicit Cleanup

**New requirement:**
```python
handler = SessionManagedEmailHandler(email_settings)
try:
    # ... operations ...
finally:
    await handler.close()  # Required to clean up sessions
```

### 3. Better Logging

**Automatic logging:**
```python
# Session management operations are automatically logged:
# INFO: Creating new IMAP connection to imap.mail.me.com:993
# INFO: IMAP connection established successfully
# INFO: Executing get_email_count (attempt 1/3)
# WARNING: get_email_count failed (attempt 1/3): invalid session ID
# INFO: Retrying get_email_count in 1.0s...
```

## Common Patterns

### Pattern 1: Context Manager (Recommended)

```python
class EmailHandlerContext:
    def __init__(self, email_settings):
        self.email_settings = email_settings
        self.handler = None
    
    async def __aenter__(self):
        self.handler = SessionManagedEmailHandler(self.email_settings)
        return self.handler
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.handler:
            await self.handler.close()

# Usage:
async with EmailHandlerContext(email_settings) as handler:
    emails = await handler.get_emails_metadata(page=1, page_size=10)
    # Automatic cleanup on exit
```

### Pattern 2: Singleton Handler

```python
class EmailService:
    def __init__(self, email_settings):
        self.handler = SessionManagedEmailHandler(email_settings)
    
    async def get_emails(self, page: int = 1):
        return await self.handler.get_emails_metadata(page=page, page_size=10)
    
    async def close(self):
        await self.handler.close()

# Usage:
service = EmailService(email_settings)
try:
    emails = await service.get_emails(page=1)
finally:
    await service.close()
```

### Pattern 3: Health-Aware Service

```python
class MonitoredEmailService:
    def __init__(self, email_settings):
        self.handler = SessionManagedEmailHandler(email_settings)
        self.health_check_interval = 300  # 5 minutes
        self.last_health_check = None
    
    async def check_health(self):
        now = datetime.now()
        if (self.last_health_check is None or 
            (now - self.last_health_check).seconds > self.health_check_interval):
            health = await self.handler.get_connection_health()
            self.last_health_check = now
            
            if not health['healthy']:
                logger.warning(f"Connection unhealthy: {health}")
            
            return health
        return {"cached": True}
    
    async def get_emails(self, page: int = 1):
        await self.check_health()
        return await self.handler.get_emails_metadata(page=page, page_size=10)
```

## Troubleshooting

### Issue: "RuntimeError: Session manager already closed"

**Cause:** Calling operations after `handler.close()`

**Solution:**
```python
# Don't do this:
handler = SessionManagedEmailHandler(email_settings)
await handler.close()
await handler.get_emails_metadata()  # Error!

# Do this instead:
handler = SessionManagedEmailHandler(email_settings)
try:
    await handler.get_emails_metadata()
finally:
    await handler.close()
```

### Issue: "Still getting connection errors"

**Cause:** Network issues or server problems

**Solution:**
```python
# Increase retries and backoff
from mcp_email_server.session_manager import SessionManager

handler = SessionManagedEmailHandler(email_settings)
handler.incoming_client.session_manager = SessionManager(
    # ... connection params ...
    max_retries=5,
    initial_backoff=2.0,
    max_backoff=60.0
)
```

### Issue: "Operations are too slow"

**Cause:** Unnecessary retries or long backoffs

**Solution:**
```python
# Reduce retry overhead for stable connections
handler.incoming_client.session_manager = SessionManager(
    # ... connection params ...
    max_retries=2,
    initial_backoff=0.5,
    max_backoff=10.0
)
```

## Testing Your Migration

### Unit Test Example

```python
import pytest
from mcp_email_server.emails.classic_with_session_mgmt import SessionManagedEmailHandler

@pytest.mark.asyncio
async def test_email_fetch():
    handler = SessionManagedEmailHandler(test_email_settings)
    
    try:
        # Test basic operations
        emails = await handler.get_emails_metadata(page=1, page_size=5)
        assert len(emails.emails) > 0
        
        # Test health check
        health = await handler.get_connection_health()
        assert health['healthy'] is True
        
    finally:
        await handler.close()
```

### Integration Test Example

```python
@pytest.mark.asyncio
async def test_session_recovery():
    handler = SessionManagedEmailHandler(email_settings)
    
    try:
        # Simulate session expiration
        await handler.incoming_client.session_manager._close_connection()
        
        # Should automatically reconnect
        emails = await handler.get_emails_metadata(page=1, page_size=5)
        assert len(emails.emails) >= 0  # Should not raise error
        
    finally:
        await handler.close()
```

## Rollback Plan

If you need to rollback:

```python
# Simply change the import back
from mcp_email_server.emails.classic import ClassicEmailHandler

# Remove the close() call
handler = ClassicEmailHandler(email_settings)
emails = await handler.get_emails_metadata(page=1, page_size=10)
# No cleanup needed
```

The classic implementation remains available and unchanged.

## Summary Checklist

Before deploying:

- [ ] Updated import to `SessionManagedEmailHandler`
- [ ] Added `handler.close()` in finally blocks
- [ ] Tested with your email provider
- [ ] Verified health check endpoint works
- [ ] Reviewed logs for retry behavior
- [ ] Updated error handling if needed
- [ ] Documented configuration changes
- [ ] Have rollback plan ready

## Support

Need help with migration?

1. Check `docs/SESSION_MANAGEMENT.md` for detailed documentation
2. Review the examples in this guide
3. Test the health check endpoint
4. Check logs for session-related messages
5. File an issue on GitHub with logs

## Next Steps

After migration:

1. Monitor health check status
2. Review retry logs
3. Tune configuration if needed
4. Remove old error handling code
5. Enjoy reliable email operations! 🎉

---

**Migration complete!** Your email client now has automatic session management and retry logic.
