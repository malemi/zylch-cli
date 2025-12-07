"""Configuration for Zylch CLI thin client.

Minimal client-side configuration - no server secrets!
"""

import base64
import json
import time
from pathlib import Path
from typing import Optional, Tuple
from pydantic import BaseModel, Field


def parse_jwt_expiry(token: str) -> Optional[int]:
    """Parse expiry timestamp from JWT token without verification.

    Args:
        token: JWT token string

    Returns:
        Expiry timestamp (seconds since epoch) or None if parsing fails
    """
    try:
        # JWT format: header.payload.signature
        parts = token.split('.')
        if len(parts) != 3:
            return None

        # Decode payload (base64url)
        payload = parts[1]
        # Add padding if needed
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding

        decoded = base64.urlsafe_b64decode(payload)
        data = json.loads(decoded)

        return data.get('exp')
    except Exception:
        return None


def check_token_status(token: str) -> Tuple[bool, Optional[int]]:
    """Check if token is expired and return time until expiry.

    Args:
        token: JWT token string

    Returns:
        Tuple of (is_valid, seconds_until_expiry)
        - is_valid: True if token exists and not expired
        - seconds_until_expiry: Seconds until expiry (negative if expired), None if can't parse
    """
    if not token:
        return False, None

    exp = parse_jwt_expiry(token)
    if exp is None:
        # Can't parse expiry, assume valid (let server validate)
        return True, None

    now = int(time.time())
    seconds_remaining = exp - now

    return seconds_remaining > 0, seconds_remaining


class CLIConfig(BaseModel):
    """Client-side configuration (saved to ~/.zylch/cli_config.json)."""

    # API Server
    api_server_url: str = Field(
        default="http://localhost:9000",
        description="Zylch API server URL"
    )

    # Session
    session_token: str = Field(
        default="",
        description="Current session token (JWT or Firebase)"
    )
    owner_id: str = Field(
        default="",
        description="Owner ID (Firebase UID)"
    )
    email: str = Field(
        default="",
        description="User email"
    )

    # Local Storage
    local_db_path: str = Field(
        default=str(Path.home() / ".zylch" / "local_data.db"),
        description="Path to local SQLite database"
    )

    # Offline
    enable_offline: bool = Field(
        default=True,
        description="Enable offline support with modifier queue"
    )
    max_offline_days: int = Field(
        default=7,
        description="Purge local data older than N days"
    )

    # Auto-sync
    auto_sync_on_start: bool = Field(
        default=False,
        description="Auto-sync on CLI start"
    )


def load_config() -> CLIConfig:
    """Load CLI configuration from file.

    Returns:
        CLIConfig instance
    """
    config_path = Path.home() / ".zylch" / "cli_config.json"

    if config_path.exists():
        with open(config_path, 'r') as f:
            data = json.load(f)
            return CLIConfig(**data)

    # Return defaults
    return CLIConfig()


def save_config(config: CLIConfig):
    """Save CLI configuration to file.

    Args:
        config: CLIConfig instance
    """
    config_path = Path.home() / ".zylch" / "cli_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, 'w') as f:
        json.dump(config.model_dump(), f, indent=2)
