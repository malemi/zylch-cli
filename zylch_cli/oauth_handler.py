"""OAuth callback handler for CLI browser-based login.

Starts a local HTTP server to receive OAuth callbacks from the browser.
"""

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread, Event
from typing import Optional, Dict, Any
from urllib.parse import urlparse, parse_qs


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP request handler for OAuth callback."""

    # Class variable to store the received token
    received_data: Optional[Dict[str, Any]] = None
    shutdown_event: Event = Event()
    # Class variables for code exchange (set by initiate_service_connect)
    backend_url: Optional[str] = None
    auth_token: Optional[str] = None
    service: Optional[str] = None

    def do_GET(self):
        """Handle GET request from OAuth callback."""
        # Parse query parameters
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)

        # Check if this is the callback with token
        if parsed_url.path == '/callback':
            # Extract token from query params (direct token flow - Google/Microsoft via Zylch backend)
            token = query_params.get('token', [None])[0]
            refresh_token = query_params.get('refresh_token', [None])[0]
            owner_id = query_params.get('owner_id', [None])[0]
            email = query_params.get('email', [None])[0]
            error = query_params.get('error', [None])[0]

            # Also check for authorization code flow (MrCall/StarChat)
            code = query_params.get('code', [None])[0]
            state = query_params.get('state', [None])[0]

            if error:
                # OAuth error
                OAuthCallbackHandler.received_data = {'error': error}
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                html = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Zylch - Login Failed</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                        .error {{ color: #d32f2f; }}
                    </style>
                </head>
                <body>
                    <h1 class="error">❌ Login Failed</h1>
                    <p>{error}</p>
                    <p>You can close this window and try again.</p>
                </body>
                </html>
                """
                self.wfile.write(html.encode())
            elif code and state:
                # Authorization code flow (MrCall/StarChat) - exchange code for tokens
                self._handle_code_exchange(code, state)
            elif token:
                # Success - store token and refresh_token
                OAuthCallbackHandler.received_data = {
                    'token': token,
                    'refresh_token': refresh_token,
                    'owner_id': owner_id,
                    'email': email
                }
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Logged In - Zylch CLI</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            background: linear-gradient(to bottom, #ffffff, #f9fafb);
            padding: 20px;
        }
        .container { text-align: center; max-width: 400px; }
        .logo {
            font-size: 48px;
            font-weight: 300;
            color: #1a1a1a;
            margin-bottom: 32px;
            letter-spacing: -2px;
        }
        .logo span { font-weight: 600; }
        .check-icon {
            width: 64px;
            height: 64px;
            background: #22c55e;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 24px;
        }
        .check-icon svg {
            width: 32px;
            height: 32px;
            stroke: white;
            stroke-width: 3;
            fill: none;
        }
        h1 {
            font-size: 24px;
            font-weight: 600;
            color: #1a1a1a;
            margin-bottom: 12px;
        }
        .message {
            color: #6b7280;
            font-size: 16px;
            line-height: 1.5;
            margin-bottom: 24px;
        }
        .countdown-box {
            background: #f3f4f6;
            border-radius: 12px;
            padding: 16px 24px;
            margin-bottom: 16px;
        }
        .countdown {
            font-size: 32px;
            font-weight: 700;
            color: #4a9eff;
        }
        .countdown-label {
            font-size: 14px;
            color: #9ca3af;
            margin-top: 4px;
        }
        .hint {
            font-size: 14px;
            color: #9ca3af;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo"><span>Z</span>ylch</div>
        <div class="check-icon">
            <svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg>
        </div>
        <h1>You're Logged In!</h1>
        <p class="message">Authentication successful.<br>Return to your terminal to use Zylch CLI.</p>
        <div class="countdown-box">
            <div class="countdown" id="countdown">5</div>
            <div class="countdown-label">seconds until this window closes</div>
        </div>
        <p class="hint">Or close this tab manually.</p>
    </div>
    <script>
        let seconds = 5;
        const countdownEl = document.getElementById('countdown');
        const interval = setInterval(() => {
            seconds--;
            countdownEl.textContent = seconds;
            if (seconds <= 0) {
                clearInterval(interval);
                window.close();
            }
        }, 1000);
    </script>
</body>
</html>"""
                self.wfile.write(html.encode('utf-8'))
            else:
                # Missing parameters
                self.send_response(400)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                html = """
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Zylch - Invalid Callback</title>
                </head>
                <body>
                    <h1>Invalid callback parameters</h1>
                </body>
                </html>
                """
                self.wfile.write(html.encode())

            # Signal to shut down the server
            OAuthCallbackHandler.shutdown_event.set()

        else:
            # Unknown path
            self.send_response(404)
            self.end_headers()

    def _handle_code_exchange(self, code: str, state: str):
        """Handle authorization code exchange for OAuth flows like MrCall.

        Calls the Zylch backend to exchange the authorization code for tokens.
        """
        import requests

        # Check if we have the backend info needed for code exchange
        if not OAuthCallbackHandler.backend_url or not OAuthCallbackHandler.auth_token:
            # No backend info - just store code and state for manual handling
            OAuthCallbackHandler.received_data = {
                'code': code,
                'state': state,
                'needs_exchange': True
            }
            self._send_success_response("Authorization Code Received",
                "Code received. Exchanging for tokens...")
            return

        # Call the Zylch backend callback endpoint to exchange code for tokens
        service = OAuthCallbackHandler.service or 'mrcall'
        callback_url = f"{OAuthCallbackHandler.backend_url}/api/auth/{service}/callback"

        try:
            headers = {'Authorization': f'Bearer {OAuthCallbackHandler.auth_token}'}
            params = {'code': code, 'state': state}
            response = requests.get(callback_url, headers=headers, params=params, timeout=30)

            if response.status_code == 200:
                # Success - tokens exchanged and stored on backend
                OAuthCallbackHandler.received_data = {
                    'success': True,
                    'service': service,
                    'message': f'{service.capitalize()} connected successfully'
                }
                self._send_success_response(f"{service.capitalize()} Connected!",
                    f"Successfully connected {service.capitalize()}.<br>Return to your terminal.")
            else:
                # Backend returned an error
                error_detail = response.text
                try:
                    error_json = response.json()
                    error_detail = error_json.get('detail', error_detail)
                except:
                    pass
                OAuthCallbackHandler.received_data = {
                    'error': f'Token exchange failed: {error_detail}'
                }
                self._send_error_response(f"Connection Failed",
                    f"Failed to connect {service.capitalize()}: {error_detail}")
        except requests.RequestException as e:
            OAuthCallbackHandler.received_data = {
                'error': f'Failed to contact server: {str(e)}'
            }
            self._send_error_response("Connection Error",
                f"Failed to contact Zylch server: {str(e)}")

    def _send_success_response(self, title: str, message: str):
        """Send a success HTML response."""
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{title} - Zylch CLI</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            background: linear-gradient(to bottom, #ffffff, #f9fafb);
            padding: 20px;
        }}
        .container {{ text-align: center; max-width: 400px; }}
        .logo {{ font-size: 48px; font-weight: 300; color: #1a1a1a; margin-bottom: 32px; letter-spacing: -2px; }}
        .logo span {{ font-weight: 600; }}
        .check-icon {{
            width: 64px; height: 64px; background: #22c55e; border-radius: 50%;
            display: flex; align-items: center; justify-content: center; margin: 0 auto 24px;
        }}
        .check-icon svg {{ width: 32px; height: 32px; stroke: white; stroke-width: 3; fill: none; }}
        h1 {{ font-size: 24px; font-weight: 600; color: #1a1a1a; margin-bottom: 12px; }}
        .message {{ color: #6b7280; font-size: 16px; line-height: 1.5; margin-bottom: 24px; }}
        .countdown-box {{ background: #f3f4f6; border-radius: 12px; padding: 16px 24px; margin-bottom: 16px; }}
        .countdown {{ font-size: 32px; font-weight: 700; color: #4a9eff; }}
        .countdown-label {{ font-size: 14px; color: #9ca3af; margin-top: 4px; }}
        .hint {{ font-size: 14px; color: #9ca3af; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="logo"><span>Z</span>ylch</div>
        <div class="check-icon">
            <svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg>
        </div>
        <h1>{title}</h1>
        <p class="message">{message}</p>
        <div class="countdown-box">
            <div class="countdown" id="countdown">5</div>
            <div class="countdown-label">seconds until this window closes</div>
        </div>
        <p class="hint">Or close this tab manually.</p>
    </div>
    <script>
        let seconds = 5;
        const countdownEl = document.getElementById('countdown');
        const interval = setInterval(() => {{
            seconds--;
            countdownEl.textContent = seconds;
            if (seconds <= 0) {{ clearInterval(interval); window.close(); }}
        }}, 1000);
    </script>
</body>
</html>"""
        self.wfile.write(html.encode('utf-8'))

    def _send_error_response(self, title: str, message: str):
        """Send an error HTML response."""
        self.send_response(400)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>{title} - Zylch</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                .error {{ color: #d32f2f; }}
            </style>
        </head>
        <body>
            <h1 class="error">❌ {title}</h1>
            <p>{message}</p>
            <p>You can close this window and try again.</p>
        </body>
        </html>
        """
        self.wfile.write(html.encode())

    def log_message(self, format, *args):
        """Suppress logging to console."""
        pass


class OAuthCallbackServer:
    """Local HTTP server for OAuth callback."""

    def __init__(self, port: int = 8765):
        """Initialize callback server.

        Args:
            port: Local port to listen on (default 8765)
        """
        self.port = port
        self.server: Optional[HTTPServer] = None
        self.server_thread: Optional[Thread] = None

    def start(self):
        """Start the callback server in a background thread."""
        # Reset class variables
        OAuthCallbackHandler.received_data = None
        OAuthCallbackHandler.shutdown_event = Event()

        # Create server
        self.server = HTTPServer(('localhost', self.port), OAuthCallbackHandler)

        # Run in background thread
        self.server_thread = Thread(target=self._run_server, daemon=True)
        self.server_thread.start()

    def _run_server(self):
        """Run the HTTP server (called in background thread)."""
        if self.server:
            # Keep serving until shutdown event is set
            while not OAuthCallbackHandler.shutdown_event.is_set():
                self.server.handle_request()

    def wait_for_callback(self, timeout: int = 300) -> Optional[Dict[str, Any]]:
        """Wait for OAuth callback and return received data.

        Args:
            timeout: Maximum time to wait in seconds (default 5 minutes)

        Returns:
            Dictionary with token data, or None if timeout
        """
        # Wait for shutdown event (triggered when callback received)
        if OAuthCallbackHandler.shutdown_event.wait(timeout=timeout):
            return OAuthCallbackHandler.received_data
        return None

    def stop(self):
        """Stop the callback server."""
        # Don't call shutdown() - it's for serve_forever(), not handle_request() loop
        # The thread will exit naturally when the shutdown_event is set
        if self.server_thread:
            self.server_thread.join(timeout=2)
            self.server_thread = None
        if self.server:
            self.server.server_close()
            self.server = None


def initiate_browser_login(server_url: str, callback_port: int = 8765) -> Optional[Dict[str, Any]]:
    """Initiate browser-based OAuth login flow.

    Args:
        server_url: Zylch API server URL (e.g., http://localhost:9000)
        callback_port: Local port for OAuth callback (default 8765)

    Returns:
        Dictionary with token data if successful, None otherwise
    """
    # Start local callback server
    callback_server = OAuthCallbackServer(port=callback_port)
    callback_server.start()

    # Build OAuth initiation URL
    callback_url = f"http://localhost:{callback_port}/callback"
    base_url = server_url.rstrip('/')
    oauth_url = f"{base_url}/api/auth/oauth/initiate?callback_url={callback_url}"

    # Open browser
    print(f"\n🔐 Opening browser for authentication...")
    print(f"If browser doesn't open, visit: {oauth_url}\n")
    webbrowser.open(oauth_url)

    # Wait for callback
    print("⏳ Waiting for login... (this may take a few moments)")
    result = callback_server.wait_for_callback(timeout=300)

    # Clean up
    callback_server.stop()

    return result


def initiate_service_connect(
    server_url: str,
    service: str,
    auth_token: str,
    callback_port: int = 8766
) -> Optional[Dict[str, Any]]:
    """Initiate browser-based OAuth flow to connect a service (Google, Microsoft).

    Args:
        server_url: Zylch API server URL (e.g., https://api.zylchai.com)
        service: Service to connect ('google', 'microsoft')
        auth_token: User's session token for authentication
        callback_port: Local port for OAuth callback (default 8766)

    Returns:
        Dictionary with success/error info if callback received, None if timeout
    """
    import requests

    service_lower = service.lower()
    service_name = service.capitalize()
    base_url = server_url.rstrip('/')
    callback_url = f"http://localhost:{callback_port}/callback"

    # Determine endpoint based on service
    if service_lower == 'google':
        authorize_endpoint = f"{base_url}/api/auth/google/authorize"
    elif service_lower == 'microsoft':
        authorize_endpoint = f"{base_url}/api/auth/microsoft/authorize"
    elif service_lower == 'mrcall':
        authorize_endpoint = f"{base_url}/api/auth/mrcall/authorize"
    else:
        print(f"❌ Unknown service: {service}")
        return {'error': f'Unknown service: {service}'}

    # Call API to get auth URL (with authentication)
    try:
        headers = {'Authorization': f'Bearer {auth_token}'}
        params = {'cli_callback': callback_url}
        response = requests.get(authorize_endpoint, headers=headers, params=params, timeout=30)

        if response.status_code == 401:
            print("❌ Session expired. Please login again with: zylch --login")
            return {'error': 'Authentication required'}

        if response.status_code != 200:
            print(f"❌ Failed to get authorization URL: {response.status_code}")
            return {'error': f'API error: {response.status_code}'}

        data = response.json()
        auth_url = data.get('auth_url')

        if not auth_url:
            print("❌ No authorization URL received from server")
            return {'error': 'No auth_url in response'}

    except requests.RequestException as e:
        print(f"❌ Failed to connect to server: {e}")
        return {'error': str(e)}

    # Set class variables for code exchange (used by MrCall OAuth flow)
    OAuthCallbackHandler.backend_url = base_url
    OAuthCallbackHandler.auth_token = auth_token
    OAuthCallbackHandler.service = service_lower

    # Start local callback server
    callback_server = OAuthCallbackServer(port=callback_port)
    callback_server.start()

    # Open browser with the auth URL
    print(f"\n🔐 Opening browser to connect {service_name}...")
    print(f"If browser doesn't open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    # Wait for callback
    print(f"⏳ Waiting for {service_name} authorization... (complete in browser)")
    result = callback_server.wait_for_callback(timeout=300)

    # Clean up
    callback_server.stop()

    return result
