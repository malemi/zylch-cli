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
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from .config import load_config, save_config, CLIConfig, check_token_status
from .api_client import ZylchAPIClient, ZylchAPIError, ZylchAuthError
from .local_storage import LocalStorage
from .modifier_queue import ModifierQueue
from .oauth_handler import initiate_browser_login, initiate_service_connect

logger = logging.getLogger(__name__)
console = Console()


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
            owner_id = result.get('owner_id')
            email = result.get('email')

            if token:
                # Save session
                self.config.session_token = token
                self.config.owner_id = owner_id or ''
                self.config.email = email or ''
                save_config(self.config)

                # Update the api_client with new token
                self.api_client.set_token(token)

                console.print(f"\n✅ Logged in as {self.config.email}", style="green")
                console.print(f"Owner ID: {self.config.owner_id}")
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
            commands = [
                # Client-side commands
                '/login', '/logout', '/status', '/new', '/quit', '/exit',
                '/connect', '/connect google', '/connect anthropic', '/connect microsoft', '/connect --reset',
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

            def get_completions(self, document, complete_event):
                text = document.text_before_cursor.lower()
                for cmd in self.commands:
                    if cmd.lower().startswith(text):
                        yield Completion(cmd, start_position=-len(text))

        prompt_session = PromptSession(
            history=FileHistory(str(history_file)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=CommandCompleter()
        )

        try:
            while True:
                # Get user input with history and autocomplete
                try:
                    console.print()  # Newline before prompt
                    user_input = prompt_session.prompt('You: ')
                except (KeyboardInterrupt, EOFError):
                    console.print("\n\n👋 Goodbye!", style="yellow")
                    break

                # Check for empty input
                if not user_input.strip():
                    continue

                # Handle special commands (client-side only)
                cmd = user_input.strip().lower()

                # Commands that MUST be handled client-side (auth, session management)
                if cmd in ['/quit', '/exit', '/q']:
                    console.print("\n👋 Goodbye!", style="yellow")
                    break
                elif cmd == '/login':
                    self.login()
                    continue
                elif cmd == '/logout':
                    self.logout()
                    continue
                elif cmd == '/status':
                    self.status()
                    continue
                elif cmd == '/new':
                    session_id = None
                    console.print("\n✨ Started new conversation", style="green")
                    continue
                elif cmd == '/connect':
                    self.connect()
                    continue
                elif cmd == '/connect anthropic':
                    self.connect(service='anthropic')
                    continue
                elif cmd == '/connect google':
                    self.connect(service='google')
                    continue
                elif cmd == '/connect microsoft':
                    self.connect(service='microsoft')
                    continue
                elif cmd == '/connect --reset':
                    self.connect(reset=True)
                    continue
                elif cmd.startswith('/connect '):
                    # Handle "/connect <service>"
                    service = cmd.split(' ', 1)[1].strip()
                    self.connect(service=service)
                    continue

                # All other /commands go to server (including /help, /sync, /gaps, /archive, etc.)
                # The server's command_handlers.py handles them

                # Check auth before sending message
                if not self.config.session_token:
                    console.print("\n❌ Not logged in. Use /login first.", style="red")
                    continue

                # Send message to API
                try:
                    console.print("\n[dim]Thinking...[/dim]")

                    response = self.api_client.send_chat_message(
                        message=user_input,
                        session_id=session_id
                    )

                    # Update session ID
                    session_id = response.get('session_id')

                    # Display response
                    assistant_response = response.get('response', '')
                    console.print(f"\n[bold green]Zylch[/bold green]: {assistant_response}")

                    # Show metadata if available
                    metadata = response.get('metadata')
                    if metadata and logger.level == logging.DEBUG:
                        console.print(f"\n[dim]Metadata: {metadata}[/dim]")

                except ZylchAuthError:
                    console.print("\n❌ Session expired. Use /login to authenticate again.", style="red")
                except ZylchAPIError as e:
                    console.print(f"\n❌ Error: {e}", style="red")

        except Exception as e:
            console.print(f"\n❌ Chat error: {e}", style="red")
            logger.exception("Chat failed")

    def _show_startup_status(self):
        """Show connection status at chat startup."""
        console.print("\n[bold]Status:[/bold]")

        # Check login status
        if not self.config.session_token:
            console.print("  ❌ Not logged in → /login", style="red")
            return  # No point checking other services if not logged in

        # Check token expiry locally first (faster than API call)
        is_valid, seconds_remaining = check_token_status(self.config.session_token)

        if not is_valid:
            # Token is expired
            console.print("  ⚠️  Session expired → /login", style="yellow")
            return

        # Token not expired, show user info
        user_email = self.config.email
        if user_email:
            console.print(f"  ✅ Logged in as {user_email}", style="green")
        else:
            console.print("  ✅ Logged in", style="green")

        # Check Anthropic API key
        try:
            anthropic_status = self.api_client.get_anthropic_status()
            if anthropic_status.get('has_key'):
                console.print("  ✅ Anthropic API key configured", style="green")
            else:
                console.print("  ❌ Anthropic API key not configured → /connect anthropic", style="red")
        except Exception:
            console.print("  ❌ Anthropic API key not configured → /connect anthropic", style="red")

        # Check Google connection
        try:
            google_status = self.api_client.get_google_status()
            if google_status.get('has_credentials'):
                email = google_status.get('email', '')
                if google_status.get('expired'):
                    console.print(f"  ⚠️  Google token expired → /connect google", style="yellow")
                else:
                    # Show what permissions we have
                    console.print(f"  ✅ Google: Email (read/send), Calendar (read/write)", style="green")
            else:
                console.print("  ○  Google not connected → /connect google", style="dim")
        except Exception:
            console.print("  ○  Google not connected → /connect google", style="dim")

        # TODO: Add Microsoft check when implemented
        # try:
        #     microsoft_status = self.api_client.get_microsoft_status()
        #     ...
        # except Exception:
        #     pass

        console.print("")  # Empty line after status

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

    def connect(self, service: Optional[str] = None, reset: bool = False):
        """Connect or manage service integrations (Google, Microsoft, etc.).

        Args:
            service: Service to connect ('google', 'microsoft')
            reset: If True, disconnect all integrations
        """
        if not self.check_auth():
            console.print("❌ Not logged in. Run: /login", style="red")
            return

        # Handle reset (disconnect all)
        if reset:
            self._disconnect_all_services()
            return

        # Show status if no service specified
        if not service:
            self._show_connection_status()
            return

        # Handle specific service
        service_lower = service.lower()
        if service_lower == 'google':
            self._connect_google()
        elif service_lower == 'microsoft':
            self._connect_microsoft()
        elif service_lower == 'anthropic':
            self._connect_anthropic()
        else:
            console.print(f"❌ Unknown service: {service}", style="red")
            console.print("Supported services: anthropic, google, microsoft")

    def _show_connection_status(self):
        """Show status of all service connections."""
        console.print(Panel.fit(
            "[bold]Connected Services[/bold]\n\n"
            "Checking connection status...",
            title="Integrations",
            border_style="cyan"
        ))

        # Check Anthropic API key
        try:
            anthropic_status = self.api_client.get_anthropic_status()
            if anthropic_status.get('has_key'):
                console.print("✅ Anthropic: API key configured", style="green")
            else:
                console.print("❌ Anthropic: API key not configured", style="red")
        except Exception:
            console.print("❌ Anthropic: API key not configured", style="red")

        # Check Google
        try:
            google_status = self.api_client.get_google_status()
            if google_status.get('has_credentials'):
                email = google_status.get('email', 'Unknown')
                if google_status.get('expired'):
                    console.print(f"⚠️  Google: Connected as {email} (token expired - reconnect needed)", style="yellow")
                else:
                    console.print(f"✅ Google: Connected as {email}", style="green")
            else:
                console.print("○  Google: Not connected", style="dim")
        except Exception:
            console.print("○  Google: Not connected", style="dim")

        # Check Microsoft (placeholder - no API method yet)
        console.print("❌ Microsoft: Not connected", style="dim")

        console.print("\n[bold]Commands:[/bold]")
        console.print("  /connect anthropic   Set your Anthropic API key (required)")
        console.print("  /connect google      Connect Google (Gmail, Calendar)")
        console.print("  /connect microsoft   Connect Microsoft (Outlook, Calendar)")
        console.print("  /connect --reset     Disconnect all services")

    def _connect_google(self):
        """Connect Google account via OAuth with local callback."""
        self._connect_service('google')

    def _connect_microsoft(self):
        """Connect Microsoft account via OAuth with local callback."""
        self._connect_service('microsoft')

    def _connect_anthropic(self):
        """Set Anthropic API key for AI chat."""
        import os
        from rich.prompt import Confirm

        console.print(Panel.fit(
            "[bold]Connect Anthropic API[/bold]\n\n"
            "Zylch uses Claude AI for chat. You need your own API key.\n\n"
            "Get your API key at: https://console.anthropic.com/\n\n"
            "Your key will be stored securely and used for your chats.",
            title="Anthropic API Key",
            border_style="cyan"
        ))

        # Check if already configured
        try:
            status = self.api_client.get_anthropic_status()
            if status.get('has_key'):
                console.print("\n✅ API key already configured.", style="green")
                if not Confirm.ask("Replace with a new key?"):
                    return
        except Exception:
            pass  # Continue to prompt for key

        # Check for environment variable first
        env_key = os.environ.get('ANTHROPIC_API_KEY')
        if env_key and env_key.startswith('sk-ant-'):
            masked_key = env_key[:12] + '...' + env_key[-4:]
            console.print(f"\n Found ANTHROPIC_API_KEY in environment: {masked_key}", style="cyan")
            if Confirm.ask("Use this key?"):
                api_key = env_key
            else:
                api_key = None
        else:
            api_key = None

        # Prompt for API key if not using env var
        if not api_key:
            from rich.prompt import Prompt
            console.print("")
            api_key = Prompt.ask("Enter your Anthropic API key", password=True)

            if not api_key or not api_key.strip():
                console.print("❌ No API key provided.", style="red")
                return

        api_key = api_key.strip()

        # Validate format
        if not api_key.startswith('sk-ant-'):
            console.print("⚠️  Warning: API key doesn't look like an Anthropic key (should start with 'sk-ant-')", style="yellow")
            from rich.prompt import Confirm
            if not Confirm.ask("Continue anyway?"):
                return

        # Save to server
        try:
            result = self.api_client.set_anthropic_key(api_key)
            if result.get('success'):
                console.print("\n✅ Anthropic API key saved!", style="green")
                console.print("You can now use Zylch chat.")
            else:
                console.print(f"\n❌ Failed to save API key: {result.get('error', 'Unknown error')}", style="red")
        except Exception as e:
            console.print(f"\n❌ Error saving API key: {e}", style="red")

    def _connect_service(self, service: str):
        """Connect a service (Google/Microsoft) via OAuth with local callback.

        Args:
            service: 'google' or 'microsoft'
        """
        service_name = service.capitalize()
        console.print(Panel.fit(
            f"[bold]Connect {service_name} Account[/bold]\n\n"
            f"This will connect your {service_name} account to Zylch.\n"
            f"You'll be able to sync {'Gmail and Calendar' if service == 'google' else 'Outlook and Calendar'}.\n\n"
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
                    console.print("To reconnect, run: /connect --reset")
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
            if email and email != 'Unknown':
                console.print(f"Connected as: {email}")
            console.print(f"\nYou can now sync your {'Gmail and Calendar' if service == 'google' else 'Outlook and Calendar'}.")

        except Exception as e:
            console.print(f"❌ Error connecting {service_name}: {e}", style="red")

    def _disconnect_all_services(self):
        """Disconnect all service integrations."""
        console.print(Panel.fit(
            "[bold]Disconnect All Services[/bold]\n\n"
            "This will revoke access to:\n"
            "• Anthropic API key\n"
            "• Google (Gmail, Calendar)\n"
            "• Microsoft (when available)\n\n"
            "You will need to re-configure to use Zylch again.",
            title="Disconnect",
            border_style="yellow"
        ))

        from rich.prompt import Confirm
        if not Confirm.ask("\nAre you sure you want to disconnect all services?"):
            console.print("Cancelled.", style="dim")
            return

        # Disconnect Anthropic
        try:
            status = self.api_client.get_anthropic_status()
            if status.get('has_key'):
                self.api_client.revoke_anthropic()
                console.print("✅ Anthropic API key deleted", style="green")
            else:
                console.print("ℹ️  Anthropic was not configured", style="dim")
        except Exception as e:
            console.print(f"⚠️  Error deleting Anthropic key: {e}", style="yellow")

        # Disconnect Google
        try:
            status = self.api_client.get_google_status()
            if status.get('has_credentials'):
                self.api_client.revoke_google()
                console.print("✅ Google disconnected", style="green")
            else:
                console.print("ℹ️  Google was not connected", style="dim")
        except Exception as e:
            console.print(f"⚠️  Error disconnecting Google: {e}", style="yellow")

        console.print("\n✅ All services disconnected.", style="green")

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
@click.option('--server-url', help='Override server URL')
@click.option('--log', type=click.Choice(['debug', 'info', 'warning', 'error']), default='warning', help='Log level')
def main(server_url, log):
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

    # Override server URL if provided
    if server_url:
        cli.config.api_server_url = server_url
        cli.api_client.server_url = server_url

    # Check server connectivity
    if not cli.check_server():
        sys.exit(1)

    # Launch chat
    cli.chat()


if __name__ == '__main__':
    main()
