from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from mcp_email_server.log import logger

DEFAILT_CONFIG_PATH = "~/.config/zerolib/mcp_email_server/config.toml"

CONFIG_PATH = Path(os.getenv("MCP_EMAIL_SERVER_CONFIG_PATH", DEFAILT_CONFIG_PATH)).expanduser().resolve()


class EmailServer(BaseModel):
    user_name: str
    password: str
    host: str
    port: int
    use_ssl: bool = True  # Usually port 465
    start_ssl: bool = False  # Usually port 587

    def masked(self) -> EmailServer:
        return self.model_copy(update={"password": "********"})


class AccountAttributes(BaseModel):
    model_config = ConfigDict(json_encoders={datetime.datetime: lambda v: v.isoformat()})
    account_name: str
    description: str = ""
    created_at: datetime.datetime = Field(default_factory=lambda: datetime.datetime.now(ZoneInfo("UTC")))
    updated_at: datetime.datetime = Field(default_factory=lambda: datetime.datetime.now(ZoneInfo("UTC")))

    @model_validator(mode="after")
    @classmethod
    def update_updated_at(cls, obj: AccountAttributes) -> AccountAttributes:
        """Update updated_at field."""
        # must disable validation to avoid infinite loop
        obj.model_config["validate_assignment"] = False

        # update updated_at field
        obj.updated_at = datetime.datetime.now(ZoneInfo("UTC"))

        # enable validation again
        obj.model_config["validate_assignment"] = True
        return obj

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AccountAttributes):
            return NotImplemented
        return self.model_dump(exclude={"created_at", "updated_at"}) == other.model_dump(
            exclude={"created_at", "updated_at"}
        )

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, v: datetime.datetime) -> str:
        return v.isoformat()

    def masked(self) -> AccountAttributes:
        return self.model_copy()


class EmailSettings(AccountAttributes):
    full_name: str
    email_address: str
    incoming: EmailServer
    outgoing: EmailServer
    save_to_sent: bool = True  # Save sent emails to IMAP Sent folder
    sent_folder_name: str | None = None  # Override Sent folder name (auto-detect if None)

    @classmethod
    def init(
        cls,
        *,
        account_name: str,
        full_name: str,
        email_address: str,
        user_name: str,
        password: str,
        imap_host: str,
        smtp_host: str,
        imap_user_name: str | None = None,
        imap_password: str | None = None,
        imap_port: int = 993,
        imap_ssl: bool = True,
        smtp_port: int = 465,
        smtp_ssl: bool = True,
        smtp_start_ssl: bool = False,
        smtp_user_name: str | None = None,
        smtp_password: str | None = None,
        save_to_sent: bool = True,
        sent_folder_name: str | None = None,
    ) -> EmailSettings:
        return cls(
            account_name=account_name,
            full_name=full_name,
            email_address=email_address,
            incoming=EmailServer(
                user_name=imap_user_name or user_name,
                password=imap_password or password,
                host=imap_host,
                port=imap_port,
                use_ssl=imap_ssl,
            ),
            outgoing=EmailServer(
                user_name=smtp_user_name or user_name,
                password=smtp_password or password,
                host=smtp_host,
                port=smtp_port,
                use_ssl=smtp_ssl,
                start_ssl=smtp_start_ssl,
            ),
            save_to_sent=save_to_sent,
            sent_folder_name=sent_folder_name,
        )

    # ruff: noqa: C901
    @classmethod
    def from_env(cls) -> list[EmailSettings] | None:
        """Create EmailSettings from environment variables.

        Expected environment variables:
        - MCP_EMAIL_<ACCOUNT>_FULL_NAME
        - MCP_EMAIL_<ACCOUNT>_EMAIL_ADDRESS
        - MCP_EMAIL_<ACCOUNT>_USER_NAME
        - MCP_EMAIL_<ACCOUNT>_PASSWORD
        - MCP_EMAIL_<ACCOUNT>_IMAP_HOST
        - MCP_EMAIL_<ACCOUNT>_IMAP_PORT (default: 993)
        - MCP_EMAIL_<ACCOUNT>_IMAP_SSL (default: true)
        - MCP_EMAIL_<ACCOUNT>_SMTP_HOST
        - MCP_EMAIL_<ACCOUNT>_SMTP_PORT (default: 465)
        - MCP_EMAIL_<ACCOUNT>_SMTP_SSL (default: true)
        - MCP_EMAIL_<ACCOUNT>_SMTP_START_SSL (default: false)
        - MCP_EMAIL_<ACCOUNT>_SAVE_TO_SENT (default: true)
        - MCP_EMAIL_<ACCOUNT>_SENT_FOLDER_NAME (default: auto-detect)
        """

        prefix = "MCP_EMAIL_"

        # Known configuration keys
        known_keys = {
            "FULL_NAME",
            "EMAIL_ADDRESS",
            "USER_NAME",
            "PASSWORD",
            "IMAP_HOST",
            "IMAP_PORT",
            "IMAP_SSL",
            "IMAP_USER_NAME",
            "IMAP_PASSWORD",
            "SMTP_HOST",
            "SMTP_PORT",
            "SMTP_SSL",
            "SMTP_START_SSL",
            "SMTP_USER_NAME",
            "SMTP_PASSWORD",
            "SAVE_TO_SENT",
            "SENT_FOLDER_NAME",
        }

        # Group env vars by account name
        accounts_dict: dict[str, dict[str, str]] = {}

        for key, value in os.environ.items():
            if not key.startswith(prefix):
                continue

            # Remove prefix
            remaining = key[len(prefix):]
            parts = remaining.split("_")

            # Find which parts form a known key by working backwards
            setting_key = None
            account_parts = []

            for i in range(len(parts), 0, -1):
                potential_key = "_".join(parts[i - 1 :])
                if potential_key in known_keys:
                    setting_key = potential_key
                    account_parts = parts[: i - 1]
                    break

            if setting_key is None:
                # Couldn't identify the key, skip this variable
                continue

            # Account name is everything before the setting key
            account_name = "_".join(account_parts).lower() if account_parts else "default"

            if account_name not in accounts_dict:
                accounts_dict[account_name] = {}

            accounts_dict[account_name][setting_key] = value

        # Create EmailSettings from each account group
        all_items: list[EmailSettings] = []

        for account_name, config in accounts_dict.items():
            # Extract required fields
            if not (email_address := config.get("EMAIL_ADDRESS")):
                logger.warning(f"Skipping account '{account_name}': missing EMAIL_ADDRESS")
                continue
            if not (password := config.get("PASSWORD")):
                logger.warning(f"Skipping account '{account_name}': missing PASSWORD")
                continue
            if not (imap_host := config.get("IMAP_HOST")):
                logger.warning(f"Skipping account '{account_name}': missing IMAP_HOST")
                continue
            if not (smtp_host := config.get("SMTP_HOST")):
                logger.warning(f"Skipping account '{account_name}': missing SMTP_HOST")
                continue

            try:
                email_settings = cls.init(
                    account_name=account_name,
                    full_name=config.get("FULL_NAME", email_address.split("@")[0]),
                    email_address=email_address,
                    user_name=config.get("USER_NAME", email_address),
                    password=password,
                    imap_host=imap_host,
                    imap_port=int(config.get("IMAP_PORT", "993")),
                    imap_ssl=_parse_bool_env(config.get("IMAP_SSL"), True),
                    imap_user_name=config.get("IMAP_USER_NAME", email_address),
                    imap_password=config.get("IMAP_PASSWORD", password),
                    smtp_host=smtp_host,
                    smtp_port=int(config.get("SMTP_PORT", "465")),
                    smtp_ssl=_parse_bool_env(config.get("SMTP_SSL"), True),
                    smtp_start_ssl=_parse_bool_env(config.get("SMTP_START_SSL"), False),
                    smtp_user_name=config.get("SMTP_USER_NAME", email_address),
                    smtp_password=config.get("SMTP_PASSWORD", password),
                    save_to_sent=_parse_bool_env(config.get("SAVE_TO_SENT"), True),
                    sent_folder_name=config.get("SENT_FOLDER_NAME"),
                )
                all_items.append(email_settings)
                logger.info(f"Loaded email account '{account_name}' from environment variables")
            except (ValueError, TypeError) as e:
                logger.error(
                    f"Failed to create email settings for account '{account_name}' from environment variables: {e}"
                )
                continue

        return all_items if all_items else None

    def masked(self) -> EmailSettings:
        return self.model_copy(
            update={
                "incoming": self.incoming.masked(),
                "outgoing": self.outgoing.masked(),
            }
        )


class ProviderSettings(AccountAttributes):
    provider_name: str
    api_key: str

    def masked(self) -> AccountAttributes:
        return self.model_copy(update={"api_key": "********"})


def _parse_bool_env(value: str | None, default: bool = False) -> bool:
    """Parse boolean value from environment variable."""
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes", "on")


class Settings(BaseSettings):
    emails: list[EmailSettings] = []
    providers: list[ProviderSettings] = []
    db_location: str = CONFIG_PATH.with_name("db.sqlite3").as_posix()
    enable_attachment_download: bool = False
    
    # Session management configuration
    use_session_management: bool = True
    session_max_retries: int = 3
    session_initial_backoff: float = 1.0
    session_max_backoff: float = 30.0
    session_timeout: int = 1800  # 30 minutes in seconds

    model_config = SettingsConfigDict(toml_file=CONFIG_PATH, validate_assignment=True, revalidate_instances="always")

    def __init__(self, **data: Any) -> None:
        """Initialize Settings with support for environment variables."""
        super().__init__(**data)

        # Check for enable_attachment_download from environment variable
        env_enable_attachment = os.getenv("MCP_EMAIL_SERVER_ENABLE_ATTACHMENT_DOWNLOAD")
        if env_enable_attachment is not None:
            self.enable_attachment_download = _parse_bool_env(env_enable_attachment, False)
            logger.info(f"Set enable_attachment_download={self.enable_attachment_download} from environment variable")

        # Check for session management settings from environment variables
        env_use_session_mgmt = os.getenv("MCP_EMAIL_SERVER_USE_SESSION_MANAGEMENT")
        if env_use_session_mgmt is not None:
            self.use_session_management = _parse_bool_env(env_use_session_mgmt, True)
            logger.info(f"Set use_session_management={self.use_session_management} from environment variable")
        
        env_session_max_retries = os.getenv("MCP_EMAIL_SERVER_SESSION_MAX_RETRIES")
        if env_session_max_retries is not None:
            try:
                self.session_max_retries = int(env_session_max_retries)
                logger.info(f"Set session_max_retries={self.session_max_retries} from environment variable")
            except ValueError:
                logger.warning(f"Invalid value for MCP_EMAIL_SERVER_SESSION_MAX_RETRIES: {env_session_max_retries}")
        
        env_session_initial_backoff = os.getenv("MCP_EMAIL_SERVER_SESSION_INITIAL_BACKOFF")
        if env_session_initial_backoff is not None:
            try:
                self.session_initial_backoff = float(env_session_initial_backoff)
                logger.info(f"Set session_initial_backoff={self.session_initial_backoff} from environment variable")
            except ValueError:
                logger.warning(f"Invalid value for MCP_EMAIL_SERVER_SESSION_INITIAL_BACKOFF: {env_session_initial_backoff}")
        
        env_session_max_backoff = os.getenv("MCP_EMAIL_SERVER_SESSION_MAX_BACKOFF")
        if env_session_max_backoff is not None:
            try:
                self.session_max_backoff = float(env_session_max_backoff)
                logger.info(f"Set session_max_backoff={self.session_max_backoff} from environment variable")
            except ValueError:
                logger.warning(f"Invalid value for MCP_EMAIL_SERVER_SESSION_MAX_BACKOFF: {env_session_max_backoff}")
        
        env_session_timeout = os.getenv("MCP_EMAIL_SERVER_SESSION_TIMEOUT")
        if env_session_timeout is not None:
            try:
                self.session_timeout = int(env_session_timeout)
                logger.info(f"Set session_timeout={self.session_timeout} from environment variable")
            except ValueError:
                logger.warning(f"Invalid value for MCP_EMAIL_SERVER_SESSION_TIMEOUT: {env_session_timeout}")

        # Check for email configuration from environment variables
        env_emails = EmailSettings.from_env()
        if not env_emails:
            logger.warning("No email configuration found in environment variables")
            return

        for env_email in env_emails:
            if env_email:
                # Check if this account already exists (from TOML)
                existing_account = None
                for i, email in enumerate(self.emails):
                    if email.account_name == env_email.account_name:
                        existing_account = i
                        break

                if existing_account is not None:
                    # Replace existing account with env configuration
                    self.emails[existing_account] = env_email
                    logger.info(f"Overriding email account '{env_email.account_name}' with environment variables")
                else:
                    # Add new account from env
                    self.emails.insert(0, env_email)
                    logger.info(f"Added email account '{env_email.account_name}' from environment variables")

    def add_email(self, email: EmailSettings) -> None:
        """Use re-assigned for validation to work."""
        self.emails = [email, *self.emails]

    def add_provider(self, provider: ProviderSettings) -> None:
        """Use re-assigned for validation to work."""
        self.providers = [provider, *self.providers]

    def delete_email(self, account_name: str) -> None:
        """Use re-assigned for validation to work."""
        self.emails = [email for email in self.emails if email.account_name != account_name]

    def delete_provider(self, account_name: str) -> None:
        """Use re-assigned for validation to work."""
        self.providers = [provider for provider in self.providers if provider.account_name != account_name]

    def get_account(self, account_name: str, masked: bool = False) -> EmailSettings | ProviderSettings | None:
        for email in self.emails:
            if email.account_name == account_name:
                return email if not masked else email.masked()
        for provider in self.providers:
            if provider.account_name == account_name:
                return provider if not masked else provider.masked()
        return None

    def get_accounts(self, masked: bool = False) -> list[EmailSettings | ProviderSettings]:
        accounts = self.emails + self.providers
        if masked:
            return [account.masked() for account in accounts]
        return accounts

    @model_validator(mode="after")
    @classmethod
    def check_unique_account_names(cls, obj: Settings) -> Settings:
        account_names = set()
        for email in obj.emails:
            if email.account_name in account_names:
                raise ValueError(f"Duplicate account name {email.account_name}")
            account_names.add(email.account_name)
        for provider in obj.providers:
            if provider.account_name in account_names:
                raise ValueError(f"Duplicate account name {provider.account_name}")
            account_names.add(provider.account_name)

        return obj

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (TomlConfigSettingsSource(settings_cls),)

    def _to_toml(self) -> str:
        data = self.model_dump(exclude_none=True)
        return tomli_w.dumps(data)

    def store(self) -> None:
        toml_file = self.model_config["toml_file"]
        toml_file.parent.mkdir(parents=True, exist_ok=True)
        toml_file.write_text(self._to_toml())
        logger.info(f"Settings stored in {toml_file}")


_settings = None


def get_settings(reload: bool = False) -> Settings:
    global _settings
    if not _settings or reload:
        logger.info(f"Loading settings from {CONFIG_PATH}")
        _settings = Settings()
    return _settings


def store_settings(settings: Settings | None = None) -> None:
    if not settings:
        settings = get_settings()
    settings.store()
    return


def delete_settings() -> None:
    if not CONFIG_PATH.exists():
        logger.info(f"Settings file {CONFIG_PATH} does not exist")
        return
    CONFIG_PATH.unlink()
    logger.info(f"Deleted settings file {CONFIG_PATH}")
