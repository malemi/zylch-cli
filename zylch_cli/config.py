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
        default="https://api.zylchai.com",
        description="Zylch API server URL"
    )

    # Session
    session_token: str = Field(
        default="",
        description="Current session token (JWT or Firebase)"
    )
    refresh_token: str = Field(
        default="",
        description="Firebase refresh token for auto-renewal"
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


# Refresh threshold: refresh token when less than 5 minutes remain
REFRESH_THRESHOLD_SECONDS = 5 * 60


def needs_token_refresh(token: str) -> bool:
    """Check if token needs to be refreshed.

    Args:
        token: JWT token string

    Returns:
        True if token expires within REFRESH_THRESHOLD_SECONDS or is already expired
    """
    if not token:
        return False

    is_valid, seconds_remaining = check_token_status(token)

    if not is_valid:
        return True  # Already expired

    if seconds_remaining is None:
        return False  # Can't determine, let server handle it

    return seconds_remaining < REFRESH_THRESHOLD_SECONDS


def refresh_token_via_server(server_url: str, refresh_token: str) -> Optional[Tuple[str, str]]:
    """Refresh session token via Zylch API server.

    Args:
        server_url: Zylch API server URL
        refresh_token: Firebase refresh token

    Returns:
        Tuple of (new_id_token, new_refresh_token) or None if refresh fails
    """
    import requests

    if not refresh_token:
        return None

    try:
        response = requests.post(
            f"{server_url.rstrip('/')}/api/auth/refresh",
            json={"refresh_token": refresh_token},
            timeout=30
        )

        if response.status_code != 200:
            return None

        data = response.json()
        new_id_token = data.get('token')
        new_refresh_token = data.get('refresh_token', refresh_token)

        if new_id_token:
            return new_id_token, new_refresh_token

        return None

    except Exception:
        return None
