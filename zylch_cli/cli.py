"""Zylch CLI - Main entry point for thin client.

Interactive CLI that communicates with Zylch API server.
"""

import logging
import sys
from pathlib import Path
from typing import Optional

import click
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from .config import load_config, save_config, CLIConfig, check_token_status, needs_token_refresh, refresh_token_via_server
from .api_client import ZylchAPIClient, ZylchAPIError, ZylchAuthError
from .local_storage import LocalStorage
from .modifier_queue import ModifierQueue
from .oauth_handler import initiate_browser_login, initiate_service_connect

logger = logging.getLogger(__name__)
console = Console()

# LLM Provider configurations for /connect command
LLM_PROVIDERS = {
    'anthropic': {
        'display_name': 'Anthropic Claude',
        'model': 'claude-sonnet-4-20250514',
        'env_var': 'ANTHROPIC_API_KEY',
        'key_prefix': 'sk-ant-',
        'url': 'https://console.anthropic.com/',
        'features': {'web_search': True, 'prompt_caching': True, 'tool_calling': True}
    },
    'openai': {
        'display_name': 'OpenAI GPT-4',
        'model': 'gpt-4.1',
        'env_var': 'OPENAI_API_KEY',
        'key_prefix': 'sk-',
        'url': 'https://platform.openai.com/api-keys',
        'features': {'web_search': False, 'prompt_caching': False, 'tool_calling': True}
    },
    'mistral': {
        'display_name': 'Mistral AI',
        'model': 'mistral-large-3',
        'env_var': 'MISTRAL_API_KEY',
        'key_prefix': '',  # Mistral keys don't have a standard prefix
        'url': 'https://console.mistral.ai/api-keys/',
        'features': {'web_search': False, 'prompt_caching': False, 'tool_calling': True, 'eu_based': True}
    }
}


def validate_llm_api_key(provider: str, api_key: str) -> tuple[bool, str]:
    """Validate an LLM provider API key by making a minimal test request.

    Args:
        provider: Provider name ('anthropic', 'openai', 'mistral')
        api_key: The API key to validate

    Returns:
        Tuple of (is_valid, error_message). If valid, error_message is empty.
    """
    import requests

    try:
        if provider == "anthropic":
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-3-haiku-20240307",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}]
                },
                timeout=15
            )
            if resp.status_code == 401:
                return False, "Invalid or expired API key"
            if resp.status_code == 200:
                return True, ""
            return False, f"Unexpected response: {resp.status_code}"

        elif provider == "openai":
            resp = requests.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15
            )
            if resp.status_code == 401:
                return False, "Invalid or expired API key"
            if resp.status_code == 200:
                return True, ""
            return False, f"Unexpected response: {resp.status_code}"

        elif provider == "mistral":
            resp = requests.get(
                "https://api.mistral.ai/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15
            )
            if resp.status_code == 401:
                return False, "Invalid or expired API key"
            if resp.status_code == 200:
                return True, ""
            return False, f"Unexpected response: {resp.status_code}"

        else:
            return False, f"Unknown provider: {provider}"

    except requests.exceptions.Timeout:
        return False, "Connection timeout - please try again"
    except requests.exceptions.ConnectionError:
        return False, "Connection error - please check your internet"
    except Exception as e:
        return False, f"Validation error: {str(e)}"


# Profile path
PROFILE_PATH = Path.home() / ".zylch" / "profile"
DEFAULT_PROFILE = """# Zylch CLI Profile
# Commands here run at startup (after login)
# Lines starting with # are comments

# Show connection status at startup
/connect
"""


class ZylchCLI:
    """Zylch thin CLI client."""

    def __init__(self):
        """Initialize CLI."""
        self.config = load_config()
        self.api_client = ZylchAPIClient(
            server_url=self.config.api_server_url,
            session_token=self.config.session_token
        )
        self.storage = LocalStorage(db_path=Path(self.config.local_db_path))
        self.queue = ModifierQueue(db_path=Path(self.config.local_db_path))

        logger.info("Zylch CLI initialized")

    def check_server(self) -> bool:
        """Check if server is reachable.

        Returns:
            True if server is up
        """
        try:
            health = self.api_client.health_check()
            if health.get('status') == 'healthy':
                console.print("✅ Server is running", style="green")
                return True
            else:
                console.print("⚠️  Server responded but unhealthy", style="yellow")
                return False
        except Exception as e:
            console.print(f"❌ Cannot reach server: {e}", style="red")
            console.print(f"\nMake sure the server is running:")
            console.print(f"  cd /Users/mal/hb/zylch")
            console.print(f"  uvicorn zylch.api.main:app --reload --port 8000")
            return False

    def check_auth(self, verbose: bool = False) -> bool:
        """Check if user is authenticated.

        Args:
            verbose: If True, print status messages

        Returns:
            True if authenticated
        """
        if not self.config.session_token:
            return False

        # Check token expiry locally first
        is_valid, _ = check_token_status(self.config.session_token)
        if not is_valid:
            return False

        if verbose:
            console.print(
                f"✅ Logged in as {self.config.email}",
                style="green"
            )
        return True

    def try_refresh_token(self) -> bool:
        """Try to refresh the session token if it's expiring soon.

        Returns:
            True if token is valid (either still valid or successfully refreshed)
        """
        if not self.config.session_token:
            return False

        # Check if refresh is needed
        if not needs_token_refresh(self.config.session_token):
            return True  # Token still valid, no refresh needed

        # Try to refresh if we have a refresh token
        if not self.config.refresh_token:
            logger.debug("Token expiring but no refresh token available")
            return False

        logger.debug("Token expiring soon, attempting refresh...")
        result = refresh_token_via_server(self.config.api_server_url, self.config.refresh_token)

        if result:
            new_token, new_refresh_token = result

            # Update config
            self.config.session_token = new_token
            self.config.refresh_token = new_refresh_token
            save_config(self.config)

            # Update API client
            self.api_client.set_token(new_token)

            logger.debug("Token refreshed successfully")
            return True
        else:
            logger.debug("Token refresh failed")
            return False

    def login(self):
        """Login flow - opens browser for OAuth authentication."""
        console.print(Panel.fit(
            "[bold]Zylch CLI Login[/bold]\n\n"
            "Your browser will open for authentication.\n"
            "Please sign in and authorize the application.\n\n"
            "If the browser doesn't open automatically,\n"
            "you'll see a URL to visit manually.",
            title="Login",
            border_style="cyan"
        ))

        try:
            # Initiate browser-based OAuth flow
            result = initiate_browser_login(
                server_url=self.config.api_server_url,
                callback_port=8765
            )

            if result is None:
                console.print("❌ Login timeout - no response received", style="red")
                return

            if 'error' in result:
                console.print(f"❌ Login failed: {result['error']}", style="red")
                return

            # Extract token data
            token = result.get('token')
            refresh_token = result.get('refresh_token')
            owner_id = result.get('owner_id')
            email = result.get('email')

            if token:
                # Save session with refresh token
                self.config.session_token = token
                self.config.refresh_token = refresh_token or ''
                self.config.owner_id = owner_id or ''
                self.config.email = email or ''
                save_config(self.config)

                # Update the api_client with new token
                self.api_client.set_token(token)

                console.print(f"\n✅ Logged in as {self.config.email}", style="green")

                # Run profile commands after successful login
                self._run_profile()
            else:
                console.print("❌ Login failed - no token received", style="red")

        except Exception as e:
            console.print(f"❌ Login error: {e}", style="red")
            logger.exception("Login failed")

    def logout(self):
        """Logout and clear session."""
        try:
            self.api_client.logout()
        except:
            pass  # Ignore errors on logout

        # Clear local session
        self.config.session_token = ""
        self.config.refresh_token = ""
        self.config.owner_id = ""
        self.config.email = ""
        save_config(self.config)

        console.print("✅ Logged out", style="green")

    def status(self):
        """Show CLI status."""
        console.print(Panel.fit(
            f"[bold]Zylch CLI Status[/bold]\n\n"
            f"Server URL: {self.config.api_server_url}\n"
            f"Logged in: {'Yes' if self.config.session_token else 'No'}\n"
            f"Email: {self.config.email or 'N/A'}\n"
            f"Owner ID: {self.config.owner_id or 'N/A'}\n"
            f"Offline mode: {'Enabled' if self.config.enable_offline else 'Disabled'}\n"
            f"Local DB: {self.config.local_db_path}",
            title="Status",
            border_style="cyan"
        ))

        # Cache stats
        if self.config.session_token:
            stats = self.storage.get_cache_stats()
            console.print("\n[bold]Local Cache:[/bold]")
            console.print(f"  Emails: {stats['email']['cached_threads']}")
            console.print(f"  Calendar: {stats['calendar']['cached_events']}")
            console.print(f"  Contacts: {stats['contacts']['cached_contacts']}")
            console.print(f"  Pending modifiers: {stats['modifier_queue']['pending_operations']}")

    def sync(self):
        """Sync data from server."""
        if not self.check_auth():
            console.print("❌ Not logged in. Run: /login", style="red")
            return

        console.print("🔄 Syncing data from server...", style="cyan")

        try:
            # Sync emails
            console.print("  Fetching emails...")
            emails_response = self.api_client.list_emails(days_back=30, limit=100)
            threads = emails_response.get('threads', [])
            for thread in threads:
                self.storage.cache_email_thread(thread['thread_id'], thread)
            console.print(f"    ✅ Cached {len(threads)} email threads")

            # Sync calendar
            console.print("  Fetching calendar events...")
            calendar_response = self.api_client.list_calendar_events(limit=100)
            events = calendar_response.get('events', [])
            for event in events:
                self.storage.cache_calendar_event(event['event_id'], event)
            console.print(f"    ✅ Cached {len(events)} calendar events")

            # Sync contacts
            console.print("  Fetching contacts...")
            contacts_response = self.api_client.list_contacts(limit=100)
            contacts = contacts_response.get('contacts', [])
            for contact in contacts:
                self.storage.cache_contact(contact['memory_id'], contact)
            console.print(f"    ✅ Cached {len(contacts)} contacts")

            # Record sync
            self.storage.record_sync('email', success=True)
            self.storage.record_sync('calendar', success=True)
            self.storage.record_sync('contacts', success=True)

            console.print("\n✅ Sync complete!", style="green")

        except ZylchAuthError:
            console.print("❌ Authentication failed - please login again", style="red")
        except ZylchAPIError as e:
            console.print(f"❌ Sync failed: {e}", style="red")

    def chat(self):
        """Start interactive chat with Zylch AI."""
        console.print(Panel.fit(
            "[bold]Zylch AI Chat[/bold]\n\n"
            "Chat with your AI assistant.\n\n"
            "[bold]Input:[/bold]\n"
            "  Enter          Send message\n"
            "  Ctrl+J         New line\n"
            "  Paste          Multiline text supported\n\n"
            "[bold]Commands:[/bold]\n"
            "  /login     Login to Zylch\n"
            "  /logout    Logout from Zylch\n"
            "  /connect   Connect services\n"
            "  /status    Show connection status\n"
            "  /help      Show all commands\n"
            "  /quit      Exit Zylch",
            title="Zylch",
            border_style="cyan"
        ))

        # Show connection status at startup
        self._show_startup_status()

        session_id = None

        # Setup prompt_toolkit for history and autocomplete
        history_file = Path.home() / ".zylch" / "chat_history"
        history_file.parent.mkdir(parents=True, exist_ok=True)

        class CommandCompleter(Completer):
            """Custom completer for slash commands."""
            def __init__(self, api_client):
                self.api_client = api_client
                self.base_commands = [
                    # Client-side commands
                    '/login', '/logout', '/status', '/new', '/quit', '/exit',
                    '/connect', '/connect --reset',
                    # Server-side commands (sent to backend)
                    '/help', '/sync', '/gaps', '/briefing',
                    '/archive', '/archive --help', '/archive --stats', '/archive --init', '/archive --sync', '/archive --search',
                    '/cache', '/cache --help', '/cache --clear',
                    '/memory', '/memory --help', '/memory --list', '/memory --stats', '/memory --add',
                    '/model', '/model haiku', '/model sonnet', '/model opus', '/model auto',
                    '/trigger', '/trigger --help', '/trigger --list', '/trigger --add', '/trigger --remove', '/trigger --types',
                    '/mrcall', '/mrcall --help',
                    '/share', '/revoke', '/sharing',
                    '/tutorial',
                ]
                self.connect_commands = []
                self._load_connect_commands()

            def _load_connect_commands(self):
                """Load /connect provider commands from API."""
                try:
                    # Only load if authenticated (avoid 401 errors during login flow)
                    if self.api_client and hasattr(self.api_client, 'get_connections_status'):
                        # Check if we have an auth token before making the request
                        if 'Authorization' not in self.api_client.session.headers:
                            return  # Skip if not authenticated yet

                        status_data = self.api_client.get_connections_status(include_unavailable=False)
                        providers = status_data.get('connections', [])
                        for provider in providers:
                            if provider.get('is_available'):
                                self.connect_commands.append(f"/connect {provider['provider_key']}")
                except Exception:
                    # Fallback to empty if API fails
                    pass

            def get_completions(self, document, complete_event):
                text = document.text_before_cursor.lower()
                all_commands = self.base_commands + self.connect_commands
                for cmd in all_commands:
                    if cmd.lower().startswith(text):
                        yield Completion(cmd, start_position=-len(text))

        # Key bindings for multiline input
        # - Enter: submit (unless Shift/Alt held)
        # - Shift+Enter or Alt+Enter: insert newline
        kb = KeyBindings()

        @kb.add(Keys.Enter)
        def handle_enter(event):
            """Submit on Enter (single line or end of multiline)."""
            buf = event.app.current_buffer
            # If text is empty or doesn't look like it needs continuation, submit
            text = buf.text
            # Submit the input
            buf.validate_and_handle()

        @kb.add('escape', 'enter')  # Option+Enter on macOS (Esc then Enter)
        def handle_option_enter(event):
            """Insert newline on Option+Enter (or Esc then Enter)."""
            event.app.current_buffer.insert_text('\n')

        @kb.add('c-j')  # Control+J (universal newline)
        def handle_ctrl_j(event):
            """Insert newline on Control+J."""
            event.app.current_buffer.insert_text('\n')


        prompt_session = PromptSession(
            history=FileHistory(str(history_file)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=CommandCompleter(self.api_client),
            key_bindings=kb,
            multiline=True
        )

        last_was_eof = False  # Track consecutive Ctrl+D
        mrcall_mode = False  # Track MrCall config mode for prompt indicator

        try:
            while True:
                # Get user input with history and autocomplete
                try:
                    console.print()  # Newline before prompt
                    prompt_prefix = '(mrcall) You: ' if mrcall_mode else 'You: '
                    user_input = prompt_session.prompt(prompt_prefix)
                    last_was_eof = False  # Reset on successful input
                except KeyboardInterrupt:
                    # Ctrl+C: just cancel current input, don't exit
                    console.print("\n(Ctrl+C to cancel, Ctrl+D twice to exit)", style="dim")
                    last_was_eof = False
                    continue
                except EOFError:
                    # Ctrl+D: require twice to exit (like Claude Code)
                    if last_was_eof:
                        console.print("\n\n👋 Goodbye!", style="yellow")
                        break
                    else:
                        console.print("\n(Press Ctrl+D again to exit)", style="dim yellow")
                        last_was_eof = True
                        continue

                # Check for empty input
                if not user_input.strip():
                    continue

                # Handle special commands (client-side only - auth & session mgmt)
                cmd = user_input.strip().lower()

                # Commands that MUST be handled client-side
                if cmd in ['/quit', '/exit', '/q']:
                    console.print("\n👋 Goodbye!", style="yellow")
                    break
                elif cmd == '/login':
                    self.login()
                    continue
                elif cmd == '/logout':
                    self.logout()
                    continue
                elif cmd == '/new':
                    session_id = None
                    console.print("\n✨ Started new conversation", style="green")
                    continue
                elif cmd == '/connect' or cmd.startswith('/connect '):
                    # OAuth requires local browser - must stay client-side
                    # EXCEPT: --help, reset, status → go to backend
                    parts = cmd.split()
                    subcommand = parts[1] if len(parts) > 1 else None

                    if subcommand in ('--help', 'reset', 'status'):
                        pass  # Let it fall through to backend
                    elif cmd == '/connect':
                        self.connect()
                        continue
                    else:
                        # /connect <provider> - OAuth flow
                        service = parts[1]
                        self.connect(service=service)
                        continue

                # All other commands & messages go to backend
                # Backend handles: /sync, /help, /gaps, /status, /archive, /memory, etc.
                # Backend also handles semantic matching (e.g., "sync my emails" -> /sync)

                # Check auth before sending message
                if not self.config.session_token:
                    console.print("\n❌ Not logged in. Use /login first.", style="red")
                    continue

                # Try to refresh token if expiring soon
                if not self.try_refresh_token():
                    # Token expired and couldn't refresh
                    console.print("\n❌ Session expired. Use /login to authenticate again.", style="red")
                    continue

                # Send message to API
                try:
                    import time
                    start_time = time.time()

                    # Show appropriate waiting message
                    if user_input.startswith('/'):
                        console.print(f"\n[dim]Running {user_input.split()[0]}...[/dim]")
                    else:
                        console.print("\n[dim]Thinking...[/dim]")

                    response = self.api_client.send_chat_message(
                        message=user_input,
                        session_id=session_id
                    )

                    elapsed = time.time() - start_time

                    # Update session ID
                    session_id = response.get('session_id')

                    # Display response
                    assistant_response = response.get('response', '')
                    console.print(f"\n[bold green]Zylch[/bold green]: {assistant_response}")

                    # Update MrCall config mode from server metadata
                    metadata = response.get('metadata', {})
                    if 'mrcall_config_mode' in metadata:
                        mrcall_mode = metadata['mrcall_config_mode']

                    # Show timing for commands or in debug mode
                    if user_input.startswith('/') or logger.level <= logging.DEBUG:
                        server_time = metadata.get('execution_time_ms', 0) / 1000
                        console.print(f"\n[dim]⏱ {elapsed:.1f}s total ({server_time:.1f}s server)[/dim]")

                except ZylchAuthError:
                    console.print("\n❌ Session expired. Use /login to authenticate again.", style="red")
                except ZylchAPIError as e:
                    console.print(f"\n❌ Error: {e}", style="red")

        except Exception as e:
            console.print(f"\n❌ Chat error: {e}", style="red")
            logger.exception("Chat failed")

    def _ensure_profile_exists(self):
        """Ensure ~/.zylch/profile exists with default content."""
        if not PROFILE_PATH.exists():
            PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            PROFILE_PATH.write_text(DEFAULT_PROFILE)
            logger.info(f"Created default profile at {PROFILE_PATH}")

    def _run_profile(self):
        """Run commands from ~/.zylch/profile.

        Executes each command, shows output, continues on error (like bashrc).
        """
        self._ensure_profile_exists()

        try:
            profile_content = PROFILE_PATH.read_text()
        except Exception as e:
            logger.warning(f"Could not read profile: {e}")
            return

        console.print("\n[dim]Running profile...[/dim]")

        for line in profile_content.splitlines():
            line = line.strip()

            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue

            # Execute the command
            logger.debug(f"Profile: executing '{line}'")
            try:
                self._execute_profile_command(line)
            except Exception as e:
                # Continue on error (like bashrc)
                console.print(f"[yellow]Profile error on '{line}': {e}[/yellow]")
                logger.warning(f"Profile command '{line}' failed: {e}")

    def _execute_profile_command(self, cmd: str):
        """Execute a single profile command.

        Args:
            cmd: Command to execute (e.g., '/connect')
        """
        if not self.config.session_token:
            console.print(f"[dim]Skipping '{cmd}' (not logged in)[/dim]")
            return

        # Handle commands that require local execution (OAuth, etc.)
        cmd_lower = cmd.strip().lower()
        if cmd_lower == '/connect' or cmd_lower.startswith('/connect '):
            if cmd_lower == '/connect':
                self.connect()
            elif cmd_lower == '/connect --reset':
                self.connect(reset=True)
            else:
                service = cmd.split(' ', 1)[1].strip()
                self.connect(service=service)
            return

        # All other commands go to backend
        try:
            response = self.api_client.send_chat_message(message=cmd, session_id=None)
            assistant_response = response.get('response', '')
            if assistant_response:
                console.print(f"[green]Zylch[/green]: {assistant_response}")
        except Exception as e:
            console.print(f"[yellow]Error: {e}[/yellow]")

    def _show_startup_status(self):
        """Show connection status at chat startup (legacy - now uses profile)."""
        # Check login status first
        if not self.config.session_token:
            console.print("\n[bold]Status:[/bold]")
            console.print("  ❌ Not logged in → /login", style="red")
            return

        # Check token expiry
        is_valid, _ = check_token_status(self.config.session_token)
        if not is_valid:
            console.print("\n[bold]Status:[/bold]")
            console.print("  ⚠️  Session expired → /login", style="yellow")
            return

        # Run profile commands (replaces old hardcoded status checks)
        self._run_profile()

    def _show_help(self):
        """Show help for chat commands."""
        console.print(Panel.fit(
            "[bold]Session & Auth:[/bold]\n"
            "  /login               Login to Zylch\n"
            "  /logout              Logout from Zylch\n"
            "  /status              Show CLI status\n"
            "  /new                 Start new conversation\n"
            "  /quit                Exit Zylch\n\n"
            "[bold]Integrations:[/bold]\n"
            "  /connect             Show connected services\n"
            "  /connect anthropic   Set your Anthropic API key\n"
            "  /connect google      Connect Google (Gmail, Calendar)\n"
            "  /connect microsoft   Connect Microsoft (Outlook)\n"
            "  /connect --reset     Disconnect all services\n"
            "  /mrcall              Link to MrCall assistant\n\n"
            "[bold]Data & Sync:[/bold]\n"
            "  /sync [days]         Sync emails & calendar\n"
            "  /gaps                Show relationship gaps\n"
            "  /archive             Email archive (--help for details)\n"
            "  /cache               Cache management (--help for details)\n\n"
            "[bold]AI & Memory:[/bold]\n"
            "  /memory              Behavioral memory (--help for details)\n"
            "  /model               Switch AI model (haiku/sonnet/opus)\n"
            "  /trigger             Event automation (--help for details)\n\n"
            "[bold]Sharing:[/bold]\n"
            "  /share <email>       Share data with user\n"
            "  /revoke <email>      Revoke sharing access\n"
            "  /sharing             Show sharing status\n\n"
            "[bold]Other:[/bold]\n"
            "  /tutorial            Interactive tutorial\n"
            "  /help                Show this help",
            title="Zylch Commands",
            border_style="cyan"
        ))

    def connect(self, service: Optional[str] = None):
        """Connect a service integration via OAuth (Google, Microsoft, etc.).

        Note: /connect --help, /connect reset, and /connect status are handled
        by the backend. This method only handles OAuth flows that require a
        local browser.

        Args:
            service: Service to connect ('google', 'microsoft', 'mrcall', etc.)
        """
        if not self.check_auth():
            console.print("❌ Not logged in. Run: /login", style="red")
            return

        # Show status if no service specified
        if not service:
            self._show_connection_status()
            return

        # Route to specific service handlers
        service_lower = service.lower()

        if service_lower == 'google':
            self._connect_google()
        elif service_lower == 'microsoft':
            self._connect_microsoft()
        elif service_lower == 'mrcall':
            self._connect_mrcall()
        elif service_lower in ['anthropic', 'openai', 'mistral']:
            self._connect_llm_provider(service_lower)
        else:
            # Try to connect via dynamic provider info from backend
            self._connect_api_key_service(service_lower)

    def _show_connection_status(self):
        """Show status of all service connections from backend API."""
        try:
            # Get all connections from backend API
            status_data = self.api_client.get_connections_status(include_unavailable=True)
            connections = status_data.get('connections', [])

            # Group by status
            connected = [c for c in connections if c.get('status') == 'connected']
            disconnected = [c for c in connections if c.get('is_available') and c.get('status') == 'disconnected']
            coming_soon = [c for c in connections if not c.get('is_available')]

            # Build output
            lines = []
            lines.append("[bold]Your Connections[/bold]")
            lines.append("")
            lines.append("Use /connect {provider} to connect")
            lines.append("")

            # Connected
            if connected:
                lines.append("[green]✅ Connected:[/green]")
                for i, conn in enumerate(connected, 1):
                    line = f"{i}. {conn['display_name']}"
                    if conn.get('connected_email'):
                        line += f" - {conn['connected_email']}"
                    lines.append(line)
                lines.append("")

            # Available but not connected
            if disconnected:
                lines.append("[yellow]❌ Available (Not Connected):[/yellow]")
                for i, conn in enumerate(disconnected, len(connected) + 1):
                    lines.append(f"{i}. {conn['display_name']}  [dim]\\[{conn['provider_key']}][/dim]")
                lines.append("")

            # Coming soon
            if coming_soon:
                lines.append("[dim]⏳ Coming Soon:[/dim]")
                for i, conn in enumerate(coming_soon, len(connected) + len(disconnected) + 1):
                    lines.append(f"{i}. {conn['display_name']}  [dim]\\[{conn['provider_key']}][/dim]")
                lines.append("")

            console.print(Panel("\n".join(lines), title="Integrations", border_style="cyan"))

        except Exception as e:
            console.print(f"❌ Error fetching connections: {e}", style="red")
            console.print("Run /connect {provider} to manage connections")

    def _connect_google(self):
        """Connect Google account via OAuth with local callback."""
        self._connect_service('google')

    def _connect_microsoft(self):
        """Connect Microsoft account via OAuth with local callback."""
        self._connect_service('microsoft')

    def _connect_mrcall(self):
        """Connect MrCall account via OAuth with local callback."""
        self._connect_service('mrcall')

    def _connect_llm_provider(self, provider: str):
        """Connect an LLM provider (Anthropic, OpenAI, Mistral) via API key.

        Args:
            provider: Provider key ('anthropic', 'openai', 'mistral')
        """
        import os
        from rich.prompt import Confirm, Prompt

        info = LLM_PROVIDERS.get(provider)
        if not info:
            console.print(f"❌ Unknown LLM provider: {provider}", style="red")
            return

        # Build feature list
        features = info['features']
        feature_lines = []
        feature_lines.append(f"• Tool calling: {'✅' if features.get('tool_calling') else '❌'}")
        feature_lines.append(f"• Web search: {'✅' if features.get('web_search') else '❌'}")
        feature_lines.append(f"• Prompt caching: {'✅' if features.get('prompt_caching') else '❌'}")
        if features.get('eu_based'):
            feature_lines.append("• EU-based (GDPR compliant): ✅")

        console.print(Panel.fit(
            f"[bold]Connect {info['display_name']}[/bold]\n\n"
            f"Model: {info['model']}\n\n"
            f"[bold]Features:[/bold]\n" + "\n".join(feature_lines) + "\n\n"
            f"Get your API key at: {info['url']}\n\n"
            f"Your key will be stored securely and used for your chats.",
            title=info['display_name'],
            border_style="cyan"
        ))

        # Check if already configured
        try:
            status = self.api_client.get_connections_status()
            connections = status.get('connections', [])
            existing = next((c for c in connections if c.get('provider_key') == provider and c.get('status') == 'connected'), None)
            if existing:
                console.print(f"\n✅ {info['display_name']} already connected.", style="green")
                if not Confirm.ask("Replace with a new key?"):
                    return
        except Exception:
            pass  # Continue to prompt for key

        # Check for environment variable first
        env_var = info['env_var']
        key_prefix = info['key_prefix']
        env_key = os.environ.get(env_var)

        if env_key and (not key_prefix or env_key.startswith(key_prefix)):
            masked_key = env_key[:12] + '...' + env_key[-4:] if len(env_key) > 16 else '***'
            console.print(f"\n Found {env_var} in environment: {masked_key}", style="cyan")
            if Confirm.ask("Use this key?"):
                api_key = env_key
            else:
                api_key = None
        else:
            api_key = None

        # Prompt for API key if not using env var
        if not api_key:
            console.print("")
            api_key = Prompt.ask(f"Enter your {info['display_name']} API key", password=True)

            if not api_key or not api_key.strip():
                console.print("❌ No API key provided.", style="red")
                return

        api_key = api_key.strip()

        # Validate format (if prefix is specified)
        if key_prefix and not api_key.startswith(key_prefix):
            console.print(f"⚠️  Warning: API key doesn't look like a {info['display_name']} key (should start with '{key_prefix}')", style="yellow")
            if not Confirm.ask("Continue anyway?"):
                return

        # Validate API key by making a test request
        console.print(f"\n[dim]Validating API key with {info['display_name']}...[/dim]")
        is_valid, error_msg = validate_llm_api_key(provider, api_key)

        if not is_valid:
            console.print(f"\n❌ **Invalid API key**: {error_msg}", style="red")
            console.print(f"\nPlease check your key at: {info['url']}", style="yellow")
            return

        console.print("[green]✓ API key validated[/green]")

        # Save to server using unified credentials API
        try:
            result = self.api_client.save_provider_credentials(provider, {"api_key": api_key})
            if result.get('success'):
                console.print(f"\n✅ {info['display_name']} connected!", style="green")
                console.print("You can now use Zylch chat with this provider.")

                # Show feature notes for non-Anthropic providers
                if provider != 'anthropic':
                    console.print("\n[dim]Note: Web search and prompt caching are Anthropic-only features.[/dim]")
            else:
                console.print(f"\n❌ Failed to save API key: {result.get('error', 'Unknown error')}", style="red")
        except Exception as e:
            console.print(f"\n❌ Error saving API key: {e}", style="red")

    def _connect_api_key_service(self, service: str):
        """Connect an API key-based service dynamically from backend config.

        Fetches provider info from backend and builds form dynamically.
        No hardcoded service list - adding new providers only requires DB migration.

        Args:
            service: Service name (vonage, pipedrive, sendgrid, etc.)
        """
        import os
        from rich.prompt import Prompt, Confirm

        # Fetch provider info from backend
        try:
            provider_info = self.api_client.get_provider_info(service)
        except Exception as e:
            error_msg = str(e)
            if '404' in error_msg or 'not found' in error_msg.lower():
                console.print(f"❌ Unknown service: {service}", style="red")
                console.print("\nAvailable services:")
                self._show_connection_status()
            else:
                console.print(f"❌ Error fetching provider info: {e}", style="red")
            return

        # Check if provider requires OAuth (shouldn't reach here, but safety check)
        if provider_info.get('requires_oauth'):
            console.print(f"❌ {provider_info.get('display_name', service)} requires OAuth flow", style="red")
            console.print(f"Use: /connect {service}")
            return

        # Check if provider is available
        if not provider_info.get('is_available', True):
            console.print(f"⏳ {provider_info.get('display_name', service)} is coming soon!", style="yellow")
            return

        display_name = provider_info.get('display_name', service.title())
        description = provider_info.get('description', '')
        doc_url = provider_info.get('documentation_url', '')
        config_fields = provider_info.get('config_fields', {})

        # Convert config_fields dict to list format for processing
        fields = []
        for field_name, field_config in config_fields.items():
            fields.append({
                'name': field_name,
                'label': field_config.get('label', field_name),
                'env_var': field_config.get('env_var'),
                'required': field_config.get('required', True),
                'encrypted': field_config.get('encrypted', False)
            })

        if not fields:
            console.print(f"❌ No configuration fields defined for {service}", style="red")
            return

        # Build env var hint
        env_vars = [f.get('env_var') for f in fields if f.get('env_var')]
        env_hint = f"\n\n[dim]Tip: Set {', '.join(env_vars)} to auto-detect[/dim]" if env_vars else ""

        # Build panel content
        panel_content = f"[bold]Connect {display_name}[/bold]"
        if description:
            panel_content += f"\n\n{description}"
        if doc_url:
            panel_content += f"\n\nGet your credentials at: {doc_url}"
        panel_content += env_hint

        console.print(Panel.fit(panel_content, title=display_name, border_style="cyan"))

        # Check for environment variables
        env_credentials = {}
        for field in fields:
            env_var = field.get('env_var')
            if env_var:
                value = os.environ.get(env_var)
                if value:
                    env_credentials[field['name']] = value.strip()

        # Show found env vars and ask to use them
        credentials = {}
        if env_credentials:
            console.print(f"\n Found {len(env_credentials)} environment variable(s):", style="cyan")
            for field in fields:
                env_var = field.get('env_var')
                value = env_credentials.get(field['name'])
                if value:
                    # Mask sensitive values (encrypted fields or known sensitive names)
                    is_sensitive = field.get('encrypted') or field['name'] in ['api_secret', 'api_token', 'api_key']
                    if is_sensitive:
                        masked = value[:4] + '...' + value[-4:] if len(value) > 8 else '***'
                    else:
                        masked = value
                    console.print(f"  {env_var}: {masked}", style="dim")

            if Confirm.ask("\nUse these values?"):
                credentials = env_credentials.copy()

        # Prompt for any missing credentials
        for field in fields:
            if field['name'] not in credentials:
                # Skip optional fields if user presses Enter
                is_required = field.get('required', True)
                is_sensitive = field.get('encrypted') or field['name'] in ['api_secret', 'api_token', 'api_key']

                label = field['label']
                if not is_required:
                    label += " (press Enter to skip)"

                value = Prompt.ask(f"\n{label}", password=is_sensitive, default="" if not is_required else ...)

                if not value and is_required:
                    console.print("❌ Setup cancelled.", style="yellow")
                    return

                if value:
                    credentials[field['name']] = value.strip()

        # Save to server
        try:
            result = self.api_client.save_provider_credentials(service, credentials)
            if result.get('success'):
                console.print(f"\n✅ {display_name} connected successfully!", style="green")
            else:
                console.print(f"\n⚠️  {result.get('message', 'Unknown error')}", style="yellow")
        except Exception as e:
            console.print(f"\n❌ Error saving credentials: {e}", style="red")

    def _connect_service(self, service: str):
        """Connect a service (Google/Microsoft) via OAuth with local callback.

        Args:
            service: 'google' or 'microsoft'
        """
        service_name = service.capitalize()
        console.print(Panel.fit(
            f"[bold]Connect {service_name} Account[/bold]\n\n"
            f"This will connect your {service_name} account to Zylch.\n"
            f"You'll be able to sync {service}'s data.\n\n"
            "Your browser will open for authentication.",
            title=f"{service_name} OAuth",
            border_style="cyan"
        ))

        try:
            # Check if already connected (Google only for now)
            if service == 'google':
                status = self.api_client.get_google_status()
                if status.get('has_credentials') and not status.get('expired'):
                    email = status.get('email', 'Unknown')
                    console.print(f"\n✅ Already connected as {email}", style="green")
                    console.print("To reconnect, run: /connect reset google")
                    return

            # Use local callback server flow
            result = initiate_service_connect(
                server_url=self.config.api_server_url,
                service=service,
                auth_token=self.config.session_token or '',
                callback_port=8766
            )

            if result is None:
                console.print(f"\n⏱️  Timeout waiting for {service_name} authorization.", style="yellow")
                console.print("If you completed authorization in browser, try again.")
                return

            if 'error' in result:
                console.print(f"\n❌ {service_name} connection failed: {result['error']}", style="red")
                return

            # Success
            email = result.get('email', 'Unknown')
            console.print(f"\n✅ {service_name} connected successfully!", style="green")
            console.print(f"Connected with email address: {email}")

        except Exception as e:
            console.print(f"❌ Error connecting {service_name}: {e}", style="red")

    def _show_history(self, session_id: Optional[str] = None):
        """Show chat history."""
        try:
            response = self.api_client.get_chat_history(session_id=session_id, limit=20)

            messages = response.get('messages', [])
            if not messages:
                console.print("\n[dim]No messages in this conversation[/dim]")
                return

            console.print("\n[bold]Conversation History:[/bold]")
            for msg in messages:
                role = msg.get('role', '')
                content = msg.get('content', '')
                timestamp = msg.get('timestamp', '')

                if role == 'user':
                    console.print(f"\n[cyan]You[/cyan] ({timestamp}):")
                    console.print(f"  {content}")
                else:
                    console.print(f"\n[green]Zylch[/green] ({timestamp}):")
                    console.print(f"  {content}")

        except ZylchAPIError as e:
            console.print(f"\n❌ Error fetching history: {e}", style="red")


@click.command()
@click.option('--host', default=None, help='Server host (default: from config)')
@click.option('--port', default=None, type=int, help='Server port (default: from config)')
@click.option('--log', type=click.Choice(['debug', 'info', 'warning', 'error']), default='warning', help='Log level')
def main(host, port, log):
    """Zylch - Your AI assistant for email and calendar."""
    # Setup logging based on --log flag
    log_levels = {
        'debug': logging.DEBUG,
        'info': logging.INFO,
        'warning': logging.WARNING,
        'error': logging.ERROR
    }
    logging.basicConfig(
        level=log_levels.get(log, logging.WARNING),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Initialize CLI
    cli = ZylchCLI()

    # Override server URL if host/port provided
    if host or port:
        # Parse existing URL to get defaults
        from urllib.parse import urlparse
        parsed = urlparse(cli.config.api_server_url)

        new_host = host or parsed.hostname or 'localhost'
        new_port = port or parsed.port or 8000
        new_scheme = 'http' if new_host in ['localhost', '127.0.0.1'] else 'https'

        # Build new URL
        server_url = f"{new_scheme}://{new_host}:{new_port}"
        cli.config.api_server_url = server_url
        cli.api_client.server_url = server_url

    # Check server connectivity
    if not cli.check_server():
        sys.exit(1)

    # Launch chat
    cli.chat()


if __name__ == '__main__':
    main()
