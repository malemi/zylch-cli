# Zylch CLI

Thin CLI client for Zylch AI - your AI assistant for email, calendar, and relationship intelligence.

## Architecture

The CLI is a **thin client** that communicates with the Zylch API server. All business logic, AI processing, and data storage happens on the server. The CLI handles:

- User authentication (Firebase OAuth via browser)
- Service connections (Google, Microsoft, Anthropic)
- Interactive chat with slash command support
- Local caching for offline access
- Modifier queue for offline operations

```
┌─────────────┐         HTTP API          ┌──────────────┐
│  Zylch CLI  │ ───────────────────────► │ Zylch Server │
│  (Thin)     │ ◄─────────────────────── │   (FastAPI)  │
└─────────────┘                           └──────────────┘
      │                                          │
      ▼                                          ▼
 Local Cache                               Supabase
 (SQLite)                                  (PostgreSQL)
```

## Installation

```bash
pip install zylch-cli
```

### From source

```bash
git clone https://github.com/malemi/zylch-cli.git
cd zylch-cli
pip install -e .
```

## Quick Start

```bash
# Start the CLI (connects to production server by default)
zylch

# Or connect to a specific server
zylch --server-url http://localhost:9000
```

## Configuration

Configuration is stored in `~/.zylch/cli_config.json`:

```json
{
  "api_server_url": "https://api.zylchai.com",
  "session_token": "",
  "owner_id": "",
  "email": "",
  "local_db_path": "~/.zylch/local_data.db",
  "enable_offline": true,
  "max_offline_days": 7,
  "auto_sync_on_start": false
}
```

## Commands

### Session & Authentication (Client-side)

| Command | Description |
|---------|-------------|
| `/login` | Login via browser (Firebase OAuth) |
| `/logout` | Logout and clear session |
| `/status` | Show CLI status and cache stats |
| `/new` | Start new conversation |
| `/quit`, `/exit` | Exit Zylch |

### Integrations (Client-side)

| Command | Description |
|---------|-------------|
| `/connect` | Show all service connection status |
| `/connect anthropic` | Set your Anthropic API key (required for chat) |
| `/connect google` | Connect Google (Gmail, Calendar) via OAuth |
| `/connect microsoft` | Connect Microsoft (Outlook, Calendar) |
| `/connect --reset` | Disconnect all services |

### Data & Sync (Server-side)

| Command | Description |
|---------|-------------|
| `/sync [days]` | Sync emails & calendar from connected services |
| `/gaps` | Show relationship gaps analysis |
| `/briefing` | Get daily briefing |
| `/archive` | Email archive management (`--help` for details) |
| `/cache` | Cache management (`--help` for details) |

### AI & Memory (Server-side)

| Command | Description |
|---------|-------------|
| `/memory` | Behavioral memory management (`--help` for details) |
| `/model [haiku\|sonnet\|opus\|auto]` | Switch AI model tier |
| `/trigger` | Event automation (`--help` for details) |

### Sharing (Server-side)

| Command | Description |
|---------|-------------|
| `/share <email>` | Share data with another user |
| `/revoke <email>` | Revoke sharing access |
| `/sharing` | Show current sharing status |

### Other (Server-side)

| Command | Description |
|---------|-------------|
| `/mrcall` | Link to MrCall assistant |
| `/tutorial` | Interactive tutorial |
| `/help` | Show all commands |

## Authentication Flow

1. User runs `/login`
2. CLI starts local HTTP server on `localhost:8765`
3. Browser opens to `{server}/api/auth/oauth/initiate`
4. User completes Firebase/Google OAuth in browser
5. Server redirects to `localhost:8765/callback?token=...`
6. CLI captures JWT token, saves to config
7. Token used for all subsequent API calls

**Token Expiry:** Firebase JWT tokens expire after ~1 hour. The CLI checks token expiry locally before making requests. If expired, user is prompted to `/login` again.

## Service Connection Flow (Google/Microsoft)

1. User runs `/connect google`
2. CLI calls `/api/auth/google/authorize` to get OAuth URL
3. Browser opens to Google consent screen
4. User authorizes Gmail/Calendar access
5. Callback stores tokens in Supabase (server-side)
6. CLI receives success confirmation

**Important:** OAuth tokens are stored in Supabase, not locally. This allows the same credentials to work across CLI and web dashboard.

## Offline Support

The CLI includes offline capabilities:

- **Local Cache:** SQLite database at `~/.zylch/local_data.db`
  - Cached emails, calendar events, contacts
  - 7-day TTL for cached data

- **Modifier Queue:** Operations queued when offline
  - Email drafts, sends
  - Calendar event creation
  - Synced when connection restored via `POST /api/data/modifier`

## Project Structure

```
zylch-cli/
├── zylch_cli/
│   ├── __init__.py
│   ├── cli.py           # Main CLI (Click + Rich + prompt_toolkit)
│   ├── api_client.py    # HTTP client for Zylch API
│   ├── config.py        # Configuration + JWT parsing
│   ├── oauth_handler.py # Browser OAuth flow
│   ├── local_storage.py # SQLite cache
│   └── modifier_queue.py # Offline operation queue
├── pyproject.toml       # Poetry configuration
└── README.md
```

## Key Files

| File | Purpose |
|------|---------|
| `cli.py` | Main entry point, slash commands, chat loop with autocomplete |
| `api_client.py` | All HTTP calls to `api.zylchai.com` |
| `config.py` | Load/save config, JWT expiry parsing |
| `oauth_handler.py` | Local callback server for OAuth |
| `local_storage.py` | SQLite caching for offline access |
| `modifier_queue.py` | Queue offline operations for later sync |

## API Endpoints Used

### Authentication
- `POST /api/auth/login` - Login with Firebase token
- `POST /api/auth/logout` - Invalidate session
- `GET /api/auth/session` - Get session info
- `POST /api/auth/refresh` - Refresh token (exists but not auto-used)

### Service Connections
- `GET /api/auth/google/status` - Check Google connection
- `GET /api/auth/google/authorize` - Get Google OAuth URL
- `POST /api/auth/google/revoke` - Disconnect Google
- `GET /api/auth/anthropic/status` - Check Anthropic key
- `POST /api/auth/anthropic/key` - Save Anthropic API key
- `POST /api/auth/anthropic/revoke` - Delete Anthropic key

### Chat
- `POST /api/chat/message` - Send message (includes slash commands)
- `GET /api/chat/history` - Get conversation history

### Data
- `GET /api/data/emails` - List email threads
- `GET /api/data/calendar` - List calendar events
- `GET /api/data/contacts` - List contacts
- `POST /api/data/modifier` - Apply offline modifications

### Health
- `GET /health` - Server health check

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run CLI
zylch

# Run tests
pytest

# Format code
black zylch_cli/
ruff check zylch_cli/
```

## Production URLs

| Service | URL |
|---------|-----|
| API Server | https://api.zylchai.com |
| Web Dashboard | https://app.zylchai.com |
| Website | https://zylchai.com |

## Known Gaps

1. **Token Refresh:** The `refresh_token()` method exists but isn't called automatically. Users must manually `/login` when token expires.

2. **Microsoft Support:** Endpoints exist but not fully implemented yet.

3. **Keychain Storage:** Tokens stored in plaintext JSON. TODO: Use system keychain.

## What This CLI Does NOT Do

- Access Google/Microsoft APIs directly (server handles this)
- Store OAuth credentials locally (only session token)
- Process emails/calendar locally (server-side)
- Run AI models (server uses your Anthropic key)

All business logic lives on the server (`api.zylchai.com`).
