"""Email client with integrated session management for improved reliability.

This module extends the classic email client with automatic reconnection,
retry logic, and better error handling for session-related issues.
"""

import email.utils
import mimetypes
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from typing import Any

import aioimaplib
import aiosmtplib

from mcp_email_server.config import EmailServer, EmailSettings
from mcp_email_server.emails import EmailHandler
from mcp_email_server.emails.models import (
    AttachmentDownloadResponse,
    EmailBodyResponse,
    EmailContentBatchResponse,
    EmailMetadata,
    EmailMetadataPageResponse,
)
from mcp_email_server.log import logger
from mcp_email_server.session_manager import ConnectionHealthCheck, SessionManager


def _quote_mailbox(mailbox: str) -> str:
    """Quote mailbox name for IMAP compatibility."""
    escaped = mailbox.replace("\\", "\\\\").replace('"', r'\"')
    return f'"{escaped}"'


async def _send_imap_id(imap: aioimaplib.IMAP4 | aioimaplib.IMAP4_SSL) -> None:
    """Send IMAP ID command with fallback for strict servers like 163.com."""
    try:
        response = await imap.id(name="mcp-email-server", version="1.0.0")
        if response.result != "OK":
            await imap.protocol.execute(
                aioimaplib.Command(
                    "ID",
                    imap.protocol.new_tag(),
                    '("name" "mcp-email-server" "version" "1.0.0")',
                )
            )
    except Exception as e:
        logger.warning(f"IMAP ID command failed: {e!s}")


class SessionManagedEmailClient:
    """Email client with automatic session management and retry logic."""

    def __init__(self, email_server: EmailServer, sender: str | None = None):
        self.email_server = email_server
        self.sender = sender or email_server.user_name

        imap_class = aioimaplib.IMAP4_SSL if self.email_server.use_ssl else aioimaplib.IMAP4

        # Initialize session manager for IMAP operations
        self.session_manager = SessionManager(
            imap_class=imap_class,
            host=self.email_server.host,
            port=self.email_server.port,
            username=self.email_server.user_name,
            password=self.email_server.password,
            max_retries=3,
            initial_backoff=1.0,
            max_backoff=30.0,
            session_timeout=1800,  # 30 minutes
        )

        # Health check endpoint
        self.health_check = ConnectionHealthCheck(self.session_manager)

        self.smtp_use_tls = self.email_server.use_ssl
        self.smtp_start_tls = self.email_server.start_ssl

    async def close(self) -> None:
        """Close the session manager and any open connections."""
        await self.session_manager.close()

    def _parse_email_data(self, raw_email: bytes, email_id: str | None = None) -> dict[str, Any]:
        """Parse raw email data into a structured dictionary."""
        parser = BytesParser(policy=default)
        email_message = parser.parsebytes(raw_email)

        subject = email_message.get("Subject", "")
        sender = email_message.get("From", "")
        date_str = email_message.get("Date", "")
        message_id = email_message.get("Message-ID")

        # Extract recipients
        to_addresses = []
        to_header = email_message.get("To", "")
        if to_header:
            to_addresses = [addr.strip() for addr in to_header.split(",")]

        cc_header = email_message.get("Cc", "")
        if cc_header:
            to_addresses.extend([addr.strip() for addr in cc_header.split(",")])

        # Parse date
        try:
            date_tuple = email.utils.parsedate_tz(date_str)
            date = (
                datetime.fromtimestamp(email.utils.mktime_tz(date_tuple), tz=timezone.utc)
                if date_tuple
                else datetime.now(timezone.utc)
            )
        except Exception:
            date = datetime.now(timezone.utc)

        # Get body content
        body = ""
        attachments = []

        if email_message.is_multipart():
            for part in email_message.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))

                if "attachment" in content_disposition:
                    filename = part.get_filename()
                    if filename:
                        attachments.append(filename)
                elif content_type == "text/plain":
                    body_part = part.get_payload(decode=True)
                    if body_part:
                        charset = part.get_content_charset("utf-8")
                        try:
                            body += body_part.decode(charset)
                        except UnicodeDecodeError:
                            body += body_part.decode("utf-8", errors="replace")
        else:
            payload = email_message.get_payload(decode=True)
            if payload:
                charset = email_message.get_content_charset("utf-8")
                try:
                    body = payload.decode(charset)
                except UnicodeDecodeError:
                    body = payload.decode("utf-8", errors="replace")

        if body and len(body) > 20000:
            body = body[:20000] + "...[TRUNCATED]"

        return {
            "email_id": email_id or "",
            "message_id": message_id,
            "subject": subject,
            "from": sender,
            "to": to_addresses,
            "body": body,
            "date": date,
            "attachments": attachments,
        }

    @staticmethod
    def _build_search_criteria(
        before: datetime | None = None,
        since: datetime | None = None,
        subject: str | None = None,
        body: str | None = None,
        text: str | None = None,
        from_address: str | None = None,
        to_address: str | None = None,
    ):
        search_criteria = []
        if before:
            search_criteria.extend(["BEFORE", before.strftime("%d-%b-%Y").upper()])
        if since:
            search_criteria.extend(["SINCE", since.strftime("%d-%b-%Y").upper()])
        if subject:
            search_criteria.extend(["SUBJECT", subject])
        if body:
            search_criteria.extend(["BODY", body])
        if text:
            search_criteria.extend(["TEXT", text])
        if from_address:
            search_criteria.extend(["FROM", from_address])
        if to_address:
            search_criteria.extend(["TO", to_address])

        if not search_criteria:
            search_criteria = ["ALL"]

        return search_criteria

    async def get_email_count(
        self,
        before: datetime | None = None,
        since: datetime | None = None,
        subject: str | None = None,
        from_address: str | None = None,
        to_address: str | None = None,
        mailbox: str = "INBOX",
    ) -> int:
        """Get email count with automatic retry on session errors."""

        async def count_operation(imap):
            await _send_imap_id(imap)
            await imap.select(_quote_mailbox(mailbox))
            search_criteria = self._build_search_criteria(
                before, since, subject, from_address=from_address, to_address=to_address
            )
            logger.info(f"Count: Search criteria: {search_criteria}")
            _, messages = await imap.uid_search(*search_criteria)
            return len(messages[0].split())

        return await self.session_manager.execute_with_retry(count_operation, "get_email_count")

    async def get_emails_metadata_stream(
        self,
        page: int = 1,
        page_size: int = 10,
        before: datetime | None = None,
        since: datetime | None = None,
        subject: str | None = None,
        from_address: str | None = None,
        to_address: str | None = None,
        order: str = "desc",
        mailbox: str = "INBOX",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream email metadata with automatic retry on session errors."""

        async def metadata_operation(imap):
            await _send_imap_id(imap)
            await imap.select(_quote_mailbox(mailbox))

            search_criteria = self._build_search_criteria(
                before, since, subject, from_address=from_address, to_address=to_address
            )
            logger.info(f"Get metadata: Search criteria: {search_criteria}")

            _, messages = await imap.uid_search(*search_criteria)

            if not messages or not messages[0]:
                logger.warning("No messages returned from search")
                return []

            email_ids = messages[0].split()
            logger.info(f"Found {len(email_ids)} email IDs")

            start = (page - 1) * page_size
            end = start + page_size

            if order == "desc":
                email_ids.reverse()

            results = []
            for email_id in email_ids[start:end]:
                try:
                    email_id_str = email_id.decode("utf-8")
                    _, data = await imap.uid("fetch", email_id_str, "BODY.PEEK[HEADER]")

                    if not data:
                        logger.error(f"Failed to fetch headers for UID {email_id_str}")
                        continue

                    raw_headers = None
                    if len(data) > 1 and isinstance(data[1], bytearray):
                        raw_headers = bytes(data[1])
                    else:
                        for item in data:
                            if isinstance(item, bytes | bytearray) and len(item) > 10:
                                if isinstance(item, bytes) and b"FETCH" in item:
                                    continue
                                raw_headers = bytes(item) if isinstance(item, bytearray) else item
                                break

                    if raw_headers:
                        parser = BytesParser(policy=default)
                        email_message = parser.parsebytes(raw_headers)

                        subject_val = email_message.get("Subject", "")
                        sender = email_message.get("From", "")
                        date_str = email_message.get("Date", "")

                        to_addresses = []
                        to_header = email_message.get("To", "")
                        if to_header:
                            to_addresses = [addr.strip() for addr in to_header.split(",")]

                        cc_header = email_message.get("Cc", "")
                        if cc_header:
                            to_addresses.extend([addr.strip() for addr in cc_header.split(",")])

                        try:
                            date_tuple = email.utils.parsedate_tz(date_str)
                            date = (
                                datetime.fromtimestamp(email.utils.mktime_tz(date_tuple), tz=timezone.utc)
                                if date_tuple
                                else datetime.now(timezone.utc)
                            )
                        except Exception:
                            date = datetime.now(timezone.utc)

                        metadata = {
                            "email_id": email_id_str,
                            "subject": subject_val,
                            "from": sender,
                            "to": to_addresses,
                            "date": date,
                            "attachments": [],
                        }
                        results.append(metadata)
                except Exception as e:
                    logger.error(f"Error fetching email metadata {email_id}: {e!s}")

            return results

        # Execute with retry and yield results
        results = await self.session_manager.execute_with_retry(metadata_operation, "get_emails_metadata_stream")
        for metadata in results:
            yield metadata

    def _extract_raw_email(self, data: list) -> bytes | None:
        """Extract raw email bytes from IMAP response data."""
        if len(data) > 1 and isinstance(data[1], bytearray):
            return bytes(data[1])

        for item in data:
            if isinstance(item, bytes | bytearray) and len(item) > 100:
                if isinstance(item, bytes) and b"FETCH" in item:
                    continue
                return bytes(item) if isinstance(item, bytearray) else item
        return None

    async def get_email_body_by_id(self, email_id: str, mailbox: str = "INBOX") -> dict[str, Any] | None:
        """Get email body by ID with automatic retry on session errors."""

        async def fetch_operation(imap):
            await _send_imap_id(imap)
            await imap.select(_quote_mailbox(mailbox))

            # Try different fetch formats
            fetch_formats = ["RFC822", "BODY[]", "BODY.PEEK[]", "(BODY.PEEK[])"]
            
            for fetch_format in fetch_formats:
                try:
                    _, data = await imap.uid("fetch", email_id, fetch_format)
                    if data and len(data) > 0:
                        raw_email = self._extract_raw_email(data)
                        if raw_email:
                            return self._parse_email_data(raw_email, email_id)
                except Exception as e:
                    logger.debug(f"Fetch format {fetch_format} failed: {e}")

            return None

        return await self.session_manager.execute_with_retry(fetch_operation, "get_email_body_by_id")

    async def download_attachment(
        self,
        email_id: str,
        attachment_name: str,
        save_path: str,
        mailbox: str = "INBOX",
    ) -> dict[str, Any]:
        """Download attachment with automatic retry on session errors."""

        async def download_operation(imap):
            await _send_imap_id(imap)
            await imap.select(_quote_mailbox(mailbox))

            fetch_formats = ["RFC822", "BODY[]", "BODY.PEEK[]"]
            raw_email = None

            for fetch_format in fetch_formats:
                try:
                    _, data = await imap.uid("fetch", email_id, fetch_format)
                    if data:
                        raw_email = self._extract_raw_email(data)
                        if raw_email:
                            break
                except Exception as e:
                    logger.debug(f"Fetch format {fetch_format} failed: {e}")

            if not raw_email:
                raise ValueError(f"Failed to fetch email with UID {email_id}")

            parser = BytesParser(policy=default)
            email_message = parser.parsebytes(raw_email)

            attachment_data = None
            mime_type = None

            if email_message.is_multipart():
                for part in email_message.walk():
                    content_disposition = str(part.get("Content-Disposition", ""))
                    if "attachment" in content_disposition:
                        filename = part.get_filename()
                        if filename == attachment_name:
                            attachment_data = part.get_payload(decode=True)
                            mime_type = part.get_content_type()
                            break

            if attachment_data is None:
                raise ValueError(f"Attachment '{attachment_name}' not found in email {email_id}")

            save_file = Path(save_path)
            save_file.parent.mkdir(parents=True, exist_ok=True)
            save_file.write_bytes(attachment_data)

            logger.info(f"Attachment '{attachment_name}' saved to {save_path}")

            return {
                "email_id": email_id,
                "attachment_name": attachment_name,
                "mime_type": mime_type or "application/octet-stream",
                "size": len(attachment_data),
                "saved_path": str(save_file.resolve()),
            }

        return await self.session_manager.execute_with_retry(download_operation, "download_attachment")

    async def delete_emails(self, email_ids: list[str], mailbox: str = "INBOX") -> tuple[list[str], list[str]]:
        """Delete emails with automatic retry on session errors."""

        async def delete_operation(imap):
            await _send_imap_id(imap)
            await imap.select(_quote_mailbox(mailbox))

            deleted_ids = []
            failed_ids = []

            for email_id in email_ids:
                try:
                    await imap.uid("store", email_id, "+FLAGS", r"(\Deleted)")
                    deleted_ids.append(email_id)
                except Exception as e:
                    logger.error(f"Failed to delete email {email_id}: {e}")
                    failed_ids.append(email_id)

            await imap.expunge()
            return deleted_ids, failed_ids

        return await self.session_manager.execute_with_retry(delete_operation, "delete_emails")

    async def send_email(
        self,
        recipients: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        html: bool = False,
        attachments: list[str] | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
    ):
        """Send email (SMTP does not use session manager)."""
        # Create message
        if attachments:
            msg = MIMEMultipart()
            content_type = "html" if html else "plain"
            text_part = MIMEText(body, content_type, "utf-8")
            msg.attach(text_part)

            for file_path in attachments:
                path = Path(file_path)
                if not path.exists():
                    raise FileNotFoundError(f"Attachment file not found: {file_path}")

                with open(path, "rb") as f:
                    file_data = f.read()

                mime_type, _ = mimetypes.guess_type(str(path))
                if mime_type is None:
                    mime_type = "application/octet-stream"

                attachment_part = MIMEApplication(file_data, _subtype=mime_type.split("/")[1])
                attachment_part.add_header("Content-Disposition", "attachment", filename=path.name)
                msg.attach(attachment_part)
        else:
            content_type = "html" if html else "plain"
            msg = MIMEText(body, content_type, "utf-8")

        if any(ord(c) > 127 for c in subject):
            msg["Subject"] = Header(subject, "utf-8")
        else:
            msg["Subject"] = subject

        if any(ord(c) > 127 for c in self.sender):
            msg["From"] = Header(self.sender, "utf-8")
        else:
            msg["From"] = self.sender

        msg["To"] = ", ".join(recipients)

        if cc:
            msg["Cc"] = ", ".join(cc)
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references

        async with aiosmtplib.SMTP(
            hostname=self.email_server.host,
            port=self.email_server.port,
            start_tls=self.smtp_start_tls,
            use_tls=self.smtp_use_tls,
        ) as smtp:
            await smtp.login(self.email_server.user_name, self.email_server.password)

            all_recipients = recipients.copy()
            if cc:
                all_recipients.extend(cc)
            if bcc:
                all_recipients.extend(bcc)

            await smtp.send_message(msg, recipients=all_recipients)

        return msg


class SessionManagedEmailHandler(EmailHandler):
    """Email handler with integrated session management."""

    def __init__(self, email_settings: EmailSettings):
        self.email_settings = email_settings
        self.incoming_client = SessionManagedEmailClient(email_settings.incoming)
        self.outgoing_client = SessionManagedEmailClient(
            email_settings.outgoing,
            sender=f"{email_settings.full_name} <{email_settings.email_address}>",
        )
        self.save_to_sent = email_settings.save_to_sent
        self.sent_folder_name = email_settings.sent_folder_name

    async def close(self) -> None:
        """Close all session managers."""
        await self.incoming_client.close()
        await self.outgoing_client.close()

    async def get_connection_health(self) -> dict[str, Any]:
        """Get health status of the IMAP connection."""
        return await self.incoming_client.health_check.check_health()

    async def get_emails_metadata(
        self,
        page: int = 1,
        page_size: int = 10,
        before: datetime | None = None,
        since: datetime | None = None,
        subject: str | None = None,
        from_address: str | None = None,
        to_address: str | None = None,
        order: str = "desc",
        mailbox: str = "INBOX",
    ) -> EmailMetadataPageResponse:
        emails = []
        async for email_data in self.incoming_client.get_emails_metadata_stream(
            page, page_size, before, since, subject, from_address, to_address, order, mailbox
        ):
            emails.append(EmailMetadata.from_email(email_data))
        total = await self.incoming_client.get_email_count(
            before, since, subject, from_address=from_address, to_address=to_address, mailbox=mailbox
        )
        return EmailMetadataPageResponse(
            page=page,
            page_size=page_size,
            before=before,
            since=since,
            subject=subject,
            emails=emails,
            total=total,
        )

    async def get_emails_content(self, email_ids: list[str], mailbox: str = "INBOX") -> EmailContentBatchResponse:
        emails = []
        failed_ids = []

        for email_id in email_ids:
            try:
                email_data = await self.incoming_client.get_email_body_by_id(email_id, mailbox)
                if email_data:
                    emails.append(
                        EmailBodyResponse(
                            email_id=email_data["email_id"],
                            message_id=email_data.get("message_id"),
                            subject=email_data["subject"],
                            sender=email_data["from"],
                            recipients=email_data["to"],
                            date=email_data["date"],
                            body=email_data["body"],
                            attachments=email_data["attachments"],
                        )
                    )
                else:
                    failed_ids.append(email_id)
            except Exception as e:
                logger.error(f"Failed to retrieve email {email_id}: {e}")
                failed_ids.append(email_id)

        return EmailContentBatchResponse(
            emails=emails,
            requested_count=len(email_ids),
            retrieved_count=len(emails),
            failed_ids=failed_ids,
        )

    async def send_email(
        self,
        recipients: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        html: bool = False,
        attachments: list[str] | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> None:
        msg = await self.outgoing_client.send_email(
            recipients, subject, body, cc, bcc, html, attachments, in_reply_to, references
        )

        # Save to Sent folder if enabled (optional, can be enhanced with retry logic)
        if self.save_to_sent and msg:
            try:
                # Note: append_to_sent would need to be implemented with session manager
                logger.info("Sent folder saving would be implemented here")
            except Exception as e:
                logger.error(f"Failed to save email to Sent folder: {e}", exc_info=True)

    async def delete_emails(self, email_ids: list[str], mailbox: str = "INBOX") -> tuple[list[str], list[str]]:
        return await self.incoming_client.delete_emails(email_ids, mailbox)

    async def download_attachment(
        self,
        email_id: str,
        attachment_name: str,
        save_path: str,
        mailbox: str = "INBOX",
    ) -> AttachmentDownloadResponse:
        result = await self.incoming_client.download_attachment(email_id, attachment_name, save_path, mailbox)
        return AttachmentDownloadResponse(
            email_id=result["email_id"],
            attachment_name=result["attachment_name"],
            mime_type=result["mime_type"],
            size=result["size"],
            saved_path=result["saved_path"],
        )
