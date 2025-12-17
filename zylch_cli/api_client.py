"""Zylch API Client - Thin wrapper for server communication.

This client provides a clean interface for CLI to communicate with the Zylch API server.
It handles:
- JWT token management
- Request/response formatting
- Error handling
- Retry logic
- Session management
"""

import logging
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class ZylchAPIError(Exception):
    """Base exception for API errors."""
    pass


class ZylchAuthError(ZylchAPIError):
    """Authentication error."""
    pass


class ZylchAPIClient:
    """Thin client for Zylch API server.

    This client is designed for the CLI to communicate with the server API.
    It provides methods for all server endpoints with proper authentication.

    Example:
        client = ZylchAPIClient(server_url="http://localhost:8000")
        client.login(firebase_token="...")
        emails = client.list_emails(days_back=30)
    """

    def __init__(
        self,
        server_url: str = "http://localhost:8000",
        session_token: Optional[str] = None
    ):
        """Initialize API client.

        Args:
            server_url: Base URL of Zylch API server (default: localhost:8000)
            session_token: Optional pre-existing session token
        """
        self.server_url = server_url
        self.session = requests.Session()

        # Set session token if provided
        if session_token:
            self.set_token(session_token)

        # Configure session
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'Zylch-CLI/1.0'
        })

        logger.info(f"ZylchAPIClient initialized for {self.server_url}")

    def set_token(self, token: str):
        """Set authentication token for subsequent requests.

        Args:
            token: JWT or Firebase token
        """
        self.session.headers['Authorization'] = f"Bearer {token}"
        logger.debug("Authentication token set")

    def clear_token(self):
        """Clear authentication token."""
        if 'Authorization' in self.session.headers:
            del self.session.headers['Authorization']
        logger.debug("Authentication token cleared")

    def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Make HTTP request to API server.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., "/api/data/emails")
            **kwargs: Additional arguments for requests (json, params, etc.)

        Returns:
            Response data as dict

        Raises:
            ZylchAuthError: If authentication fails
            ZylchAPIError: If request fails
        """
        url = f"{self.server_url.rstrip('/')}{endpoint}"

        try:
            logger.debug(f"{method} {url}")
            response = self.session.request(method, url, **kwargs)

            # Handle auth errors
            if response.status_code in [401, 403]:
                raise ZylchAuthError(
                    f"Authentication failed: {response.status_code} {response.text}"
                )

            # Handle other errors
            if response.status_code >= 400:
                raise ZylchAPIError(
                    f"API request failed: {response.status_code} {response.text}"
                )

            # Parse JSON response
            return response.json()

        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            raise ZylchAPIError(f"Request failed: {str(e)}")

    # Auth Endpoints

    def login(self, firebase_token: str) -> Dict[str, Any]:
        """Login with Firebase token and get session info.

        Args:
            firebase_token: Firebase ID token

        Returns:
            Session info with token, owner_id, etc.
        """
        response = self._request(
            "POST",
            "/api/auth/login",
            json={"firebase_token": firebase_token}
        )

        # Set token for subsequent requests
        if response.get('success') and 'token' in response:
            self.set_token(response['token'])
            logger.info(f"Logged in as {response.get('owner_id')}")

        return response

    def refresh_token(self) -> Dict[str, Any]:
        """Refresh current session token.

        Returns:
            Refreshed session info
        """
        response = self._request("POST", "/api/auth/refresh")

        # Update token
        if response.get('success') and 'token' in response:
            self.set_token(response['token'])
            logger.debug("Session token refreshed")

        return response

    def logout(self) -> Dict[str, Any]:
        """Logout and invalidate session.

        Returns:
            Logout confirmation
        """
        response = self._request("POST", "/api/auth/logout")

        # Clear local token
        if response.get('success'):
            self.clear_token()
            logger.info("Logged out")

        return response

    def get_session_info(self) -> Dict[str, Any]:
        """Get current session information.

        Returns:
            Session info with owner_id, email, etc.
        """
        return self._request("GET", "/api/auth/session")

    # Data Endpoints - Emails

    def list_emails(
        self,
        days_back: Optional[int] = 30,
        limit: int = 100,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List email threads.

        Args:
            days_back: Filter emails from last N days
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Email threads and stats
        """
        params = {
            'limit': limit,
            'offset': offset
        }
        if days_back is not None:
            params['days_back'] = days_back

        return self._request("GET", "/api/data/emails", params=params)

    def get_email_thread(self, thread_id: str) -> Dict[str, Any]:
        """Get specific email thread.

        Args:
            thread_id: Email thread identifier

        Returns:
            Email thread data
        """
        return self._request("GET", f"/api/data/emails/{thread_id}")

    # Data Endpoints - Calendar

    def list_calendar_events(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List calendar events.

        Args:
            start: Filter events after this date (ISO 8601)
            end: Filter events before this date (ISO 8601)
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Calendar events and stats
        """
        params = {
            'limit': limit,
            'offset': offset
        }
        if start:
            params['start'] = start
        if end:
            params['end'] = end

        return self._request("GET", "/api/data/calendar", params=params)

    def get_calendar_event(self, event_id: str) -> Dict[str, Any]:
        """Get specific calendar event.

        Args:
            event_id: Calendar event identifier

        Returns:
            Calendar event data
        """
        return self._request("GET", f"/api/data/calendar/{event_id}")

    # Data Endpoints - Contacts

    def list_contacts(
        self,
        query: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List or search contacts.

        Args:
            query: Optional search query
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Contacts and stats
        """
        params = {
            'limit': limit,
            'offset': offset
        }
        if query:
            params['query'] = query

        return self._request("GET", "/api/data/contacts", params=params)

    def get_contact(self, memory_id: str) -> Dict[str, Any]:
        """Get specific contact.

        Args:
            memory_id: Contact memory identifier

        Returns:
            Contact data
        """
        return self._request("GET", f"/api/data/contacts/{memory_id}")

    # Data Endpoints - Storage Stats

    def get_storage_stats(self) -> Dict[str, Any]:
        """Get storage statistics for current user.

        Returns:
            Stats for email, calendar, contacts
        """
        return self._request("GET", "/api/data/stats")

    # Data Endpoints - Modifier (Offline Sync)

    def apply_modifiers(self, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Apply offline modifications to server.

        Args:
            operations: List of offline operations to apply

        Returns:
            Results for each operation
        """
        return self._request(
            "POST",
            "/api/data/modifier",
            json={"operations": operations}
        )

    # Chat Endpoint

    def send_chat_message(
        self,
        message: str,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send chat message to Zylch AI.

        Args:
            message: User message or command
            session_id: Optional session ID to continue conversation

        Returns:
            AI response and session info
        """
        payload = {"message": message}
        if session_id:
            payload["session_id"] = session_id

        return self._request("POST", "/api/chat/message", json=payload)

    def get_chat_history(
        self,
        session_id: Optional[str] = None,
        limit: int = 50
    ) -> Dict[str, Any]:
        """Get chat conversation history.

        Args:
            session_id: Optional session ID (uses latest if not provided)
            limit: Maximum messages to return

        Returns:
            Chat history
        """
        params = {'limit': limit}
        if session_id:
            params['session_id'] = session_id

        return self._request("GET", "/api/chat/history", params=params)

    # Sync Endpoints (existing)

    def sync_email(
        self,
        days_back: int = 30,
        force_full: bool = False
    ) -> Dict[str, Any]:
        """Trigger email sync on server.

        Args:
            days_back: Number of days to sync
            force_full: Force full sync instead of incremental

        Returns:
            Sync results
        """
        return self._request(
            "POST",
            "/api/sync/email",
            json={
                "days_back": days_back,
                "force_full": force_full
            }
        )

    def sync_calendar(self) -> Dict[str, Any]:
        """Trigger calendar sync on server.

        Returns:
            Sync results
        """
        return self._request("POST", "/api/sync/calendar")

    def sync_full(self, days_back: int = 30) -> Dict[str, Any]:
        """Trigger full sync (email + calendar + analysis).

        Args:
            days_back: Number of days to sync

        Returns:
            Complete sync results
        """
        return self._request(
            "POST",
            "/api/sync/full",
            json={"days_back": days_back}
        )

    # Health Check

    def health_check(self) -> Dict[str, Any]:
        """Check API server health.

        Returns:
            Health status
        """
        return self._request("GET", "/health")

    # Google OAuth Integration

    def get_google_status(self) -> Dict[str, Any]:
        """Get Google OAuth connection status.

        Returns:
            Status with has_credentials, email, valid, expired fields
        """
        return self._request("GET", "/api/auth/google/status")

    def get_google_auth_url(self) -> Dict[str, Any]:
        """Get Google OAuth authorization URL.

        Returns:
            auth_url to redirect user to Google consent
        """
        return self._request("GET", "/api/auth/google/authorize")

    def revoke_google(self) -> Dict[str, Any]:
        """Revoke Google OAuth credentials.

        Returns:
            Success confirmation
        """
        return self._request("POST", "/api/auth/google/revoke")

    # Anthropic API Key Management

    def get_anthropic_status(self) -> Dict[str, Any]:
        """Check if user has Anthropic API key configured.

        Returns:
            Status with has_key field
        """
        return self._request("GET", "/api/auth/anthropic/status")

    def set_anthropic_key(self, api_key: str) -> Dict[str, Any]:
        """Set Anthropic API key for the user.

        Args:
            api_key: Anthropic API key (sk-ant-...)

        Returns:
            Success confirmation
        """
        return self._request(
            "POST",
            "/api/auth/anthropic/key",
            json={"api_key": api_key}
        )

    def revoke_anthropic(self) -> Dict[str, Any]:
        """Revoke/delete Anthropic API key.

        Returns:
            Success confirmation
        """
        return self._request("POST", "/api/auth/anthropic/revoke")

    # Connections Management

    def get_connections_status(self, include_unavailable: bool = True) -> Dict[str, Any]:
        """Get status of all integration connections.

        Args:
            include_unavailable: Include "coming soon" providers

        Returns:
            Dict with connections list and counts
        """
        params = {"include_unavailable": str(include_unavailable).lower()}
        return self._request("GET", "/api/connections/status", params=params)

    def save_provider_credentials(
        self,
        provider_key: str,
        credentials: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Save credentials for any provider (Vonage, Pipedrive, etc.).

        Args:
            provider_key: Provider identifier (vonage, pipedrive, etc.)
            credentials: Dict of credential fields (api_key, api_secret, etc.)
            metadata: Optional metadata (scopes, etc.)

        Returns:
            Success confirmation with provider info
        """
        payload = {"credentials": credentials}
        if metadata:
            payload["metadata"] = metadata

        return self._request(
            "POST",
            f"/api/connections/provider/{provider_key}/credentials",
            json=payload
        )

    def disconnect_provider(self, provider_key: str) -> Dict[str, Any]:
        """Disconnect/revoke a provider connection.

        Args:
            provider_key: Provider identifier (vonage, pipedrive, etc.)

        Returns:
            Success confirmation
        """
        return self._request(
            "DELETE",
            f"/api/connections/provider/{provider_key}/credentials"
        )
