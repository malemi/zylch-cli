# Zylch CLI Setup & Testing Guide

**Separated thin client - requires running server!**

## Directory Structure

```
/Users/mal/hb/
├── zylch/              ← SERVER (FastAPI)
│   ├── zylch/
│   │   ├── api/        ← API endpoints
│   │   ├── storage/    ← Server storage (email_store, calendar_store, contact_store)
│   │   └── ...
│   └── ...
│
└── zylch-cli/          ← THIN CLIENT (this directory)
    ├── zylch_cli/
    │   ├── api_client.py       ← API wrapper
    │   ├── local_storage.py    ← Local cache
    │   ├── modifier_queue.py   ← Offline operations
    │   ├── config.py           ← Client config
    │   └── cli.py              ← Main CLI
    ├── pyproject.toml
    ├── zylch                   ← Launcher script
    └── README.md
```

## Setup

### 1. Create Virtual Environment

```bash
cd /Users/mal/hb/zylch-cli
python3 -m venv venv
source venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Check Installation

```bash
python -c "from zylch_cli import api_client; print('✅ OK')"
```

**Expected:** `✅ OK`

## Testing Separation

### Terminal 1: Start Server

```bash
cd /Users/mal/hb/zylch
source venv/bin/activate
uvicorn zylch.api.main:app --reload --port 8000
```

**Expected output:**
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete.
```

**Verify server:**
```bash
curl http://localhost:8000/health
# Should return: {"status":"healthy",...}
```

### Terminal 2: Use CLI

```bash
cd /Users/mal/hb/zylch-cli

# Check CLI works
./zylch

# Or with poetry
poetry run python -m zylch_cli.cli

# Login
./zylch --login
# Paste Firebase token (from ~/.zylch/credentials.json)

# Check status
./zylch --status

# Sync data
./zylch --sync
```

## Verification Checklist

- [ ] Server runs independently in `/Users/mal/hb/zylch/`
- [ ] CLI runs independently in `/Users/mal/hb/zylch-cli/`
- [ ] CLI cannot work without server running
- [ ] CLI communicates via HTTP API (check server logs)
- [ ] CLI creates local cache at `~/.zylch/local_data.db`
- [ ] CLI creates config at `~/.zylch/cli_config.json`
- [ ] No shared code between server and CLI (separate imports)

## What's Separated

### Server (`/Users/mal/hb/zylch/`)
- ✅ FastAPI application
- ✅ Business logic (AI agent, tools)
- ✅ OAuth integrations (Google, Microsoft)
- ✅ Database (PostgreSQL/SQLite)
- ✅ Email/Calendar processing
- ✅ Multi-tenant storage

### CLI (`/Users/mal/hb/zylch-cli/`)
- ✅ HTTP API client only
- ✅ Local cache (SQLite)
- ✅ Offline modifier queue
- ✅ Session token management
- ✅ No business logic
- ✅ No OAuth credentials (only session token)

## Testing API Communication

### 1. Health Check

```bash
cd /Users/mal/hb/zylch-cli
poetry run python -c "
from zylch_cli.api_client import ZylchAPIClient
client = ZylchAPIClient()
print(client.health_check())
"
```

**Expected:** `{'status': 'healthy', ...}`

### 2. Login Flow

```bash
# Get Firebase token
cat ~/.zylch/credentials.json | jq -r '.token'

# Test login
poetry run python -c "
from zylch_cli.api_client import ZylchAPIClient
client = ZylchAPIClient()
response = client.login('YOUR_FIREBASE_TOKEN_HERE')
print(response)
"
```

**Expected:** `{'success': True, 'owner_id': '...', ...}`

### 3. Data Sync

```bash
poetry run python -c "
from zylch_cli.api_client import ZylchAPIClient
from zylch_cli.config import load_config

config = load_config()
client = ZylchAPIClient(session_token=config.session_token)

# List emails
emails = client.list_emails(days_back=7)
print(f'Emails: {len(emails[\"threads\"])}')

# List calendar
calendar = client.list_calendar_events()
print(f'Events: {len(calendar[\"events\"])}')
"
```

### 4. Local Storage

```bash
poetry run python -c "
from zylch_cli.local_storage import LocalStorage

storage = LocalStorage()
stats = storage.get_cache_stats()
print(stats)
"
```

**Expected:** Cache stats with counts

### 5. Modifier Queue

```bash
poetry run python -c "
from zylch_cli.modifier_queue import ModifierQueue

queue = ModifierQueue()
client_id = queue.add_modifier('email_draft', {
    'to': 'test@example.com',
    'subject': 'Test',
    'body': 'Hello'
})
print(f'Queued: {client_id}')

pending = queue.get_pending_modifiers()
print(f'Pending: {len(pending)}')
"
```

## Common Issues

### "Cannot reach server"
- Server not running → Start in Terminal 1
- Wrong port → Check server is on 8000
- Wrong URL → Check `~/.zylch/cli_config.json`

### "Authentication failed"
- No session token → Run `./zylch --login`
- Token expired → Login again
- Wrong owner_id → Clear config and re-login

### Import errors
- Dependencies not installed → Run `poetry install`
- Wrong directory → Must be in `/Users/mal/hb/zylch-cli/`

## Next Steps

After verifying separation:

1. ✅ Server and CLI fully separated
2. ⏳ Implement more CLI commands (chat, gaps, etc.)
3. ⏳ Add offline modifier sync
4. ⏳ Improve error handling
5. ⏳ Add progress bars for sync
6. ⏳ Add interactive chat mode

See: `/Users/mal/hb/zylch/.claude/plans/lazy-frolicking-nest.md` for full plan.
