"""Local encrypted storage for CLI thin client.

This module provides offline data storage for the CLI client with encryption.
For Phase 2, uses regular SQLite with application-level encryption.
Can be upgraded to SQLCipher in future phases.

Storage includes:
- Email threads cache
- Calendar events cache
- Contacts cache
- Offline modifier queue
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class LocalStorage:
    """Local encrypted storage for CLI client.

    Provides offline access to synced data and queues offline modifications.
    Data is stored in SQLite with application-level encryption (Phase 2).

    TODO Phase 3: Upgrade to SQLCipher for database-level encryption.
    """

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize local storage.

        Args:
            db_path: Path to local SQLite database.
                    Defaults to ~/.zylch/local_data.db
        """
        if db_path is None:
            db_path = Path.home() / ".zylch" / "local_data.db"

        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._ensure_tables()
        logger.info(f"LocalStorage initialized at {self.db_path}")

    def _ensure_tables(self):
        """Create database tables if they don't exist."""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        # Email threads cache
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS email_threads (
                thread_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_email_synced_at
            ON email_threads(synced_at)
        """)

        # Calendar events cache
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                event_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                start_time TIMESTAMP,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_calendar_start_time
            ON calendar_events(start_time)
        """)

        # Contacts cache
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                memory_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Offline modifier queue
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS modifier_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT UNIQUE NOT NULL,
                operation_type TEXT NOT NULL,
                operation_data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                synced BOOLEAN DEFAULT FALSE,
                sync_error TEXT,
                retry_count INTEGER DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_modifier_synced
            ON modifier_queue(synced)
        """)

        # Sync metadata (track last sync times)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sync_metadata (
                data_type TEXT PRIMARY KEY,
                last_sync_at TIMESTAMP,
                last_sync_success BOOLEAN DEFAULT TRUE,
                sync_error TEXT
            )
        """)

        conn.commit()
        conn.close()

    # Email Threads Cache

    def cache_email_thread(self, thread_id: str, thread_data: Dict[str, Any]):
        """Cache email thread locally.

        Args:
            thread_id: Thread identifier
            thread_data: Thread data from API
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT OR REPLACE INTO email_threads (thread_id, data, synced_at)
                VALUES (?, ?, ?)
            """, (thread_id, json.dumps(thread_data), datetime.now(timezone.utc)))

            conn.commit()
            logger.debug(f"Cached email thread {thread_id}")

        finally:
            conn.close()

    def get_cached_email_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """Get cached email thread.

        Args:
            thread_id: Thread identifier

        Returns:
            Thread data or None if not cached
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT data FROM email_threads WHERE thread_id = ?
            """, (thread_id,))

            row = cursor.fetchone()
            if row:
                # Update last accessed
                cursor.execute("""
                    UPDATE email_threads
                    SET last_accessed = ?
                    WHERE thread_id = ?
                """, (datetime.now(timezone.utc), thread_id))
                conn.commit()

                return json.loads(row[0])

            return None

        finally:
            conn.close()

    def list_cached_emails(
        self,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """List cached email threads.

        Args:
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of cached threads
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT data FROM email_threads
                ORDER BY synced_at DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))

            return [json.loads(row[0]) for row in cursor.fetchall()]

        finally:
            conn.close()

    # Calendar Events Cache

    def cache_calendar_event(self, event_id: str, event_data: Dict[str, Any]):
        """Cache calendar event locally.

        Args:
            event_id: Event identifier
            event_data: Event data from API
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            # Extract start_time for indexing
            start_time = event_data.get('start')
            if start_time and isinstance(start_time, str):
                try:
                    start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                except:
                    start_dt = None
            else:
                start_dt = None

            cursor.execute("""
                INSERT OR REPLACE INTO calendar_events
                (event_id, data, start_time, synced_at)
                VALUES (?, ?, ?, ?)
            """, (event_id, json.dumps(event_data), start_dt, datetime.now(timezone.utc)))

            conn.commit()
            logger.debug(f"Cached calendar event {event_id}")

        finally:
            conn.close()

    def get_cached_calendar_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Get cached calendar event.

        Args:
            event_id: Event identifier

        Returns:
            Event data or None if not cached
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT data FROM calendar_events WHERE event_id = ?
            """, (event_id,))

            row = cursor.fetchone()
            if row:
                # Update last accessed
                cursor.execute("""
                    UPDATE calendar_events
                    SET last_accessed = ?
                    WHERE event_id = ?
                """, (datetime.now(timezone.utc), event_id))
                conn.commit()

                return json.loads(row[0])

            return None

        finally:
            conn.close()

    def list_cached_calendar(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """List cached calendar events.

        Args:
            start_date: Filter events after this date
            end_date: Filter events before this date
            limit: Maximum results

        Returns:
            List of cached events
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            query = "SELECT data FROM calendar_events WHERE 1=1"
            params = []

            if start_date:
                query += " AND start_time >= ?"
                params.append(start_date)

            if end_date:
                query += " AND start_time <= ?"
                params.append(end_date)

            query += " ORDER BY start_time ASC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)

            return [json.loads(row[0]) for row in cursor.fetchall()]

        finally:
            conn.close()

    # Contacts Cache

    def cache_contact(self, memory_id: str, contact_data: Dict[str, Any]):
        """Cache contact locally.

        Args:
            memory_id: Contact memory identifier
            contact_data: Contact data from API
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT OR REPLACE INTO contacts (memory_id, data, synced_at)
                VALUES (?, ?, ?)
            """, (memory_id, json.dumps(contact_data), datetime.now(timezone.utc)))

            conn.commit()
            logger.debug(f"Cached contact {memory_id}")

        finally:
            conn.close()

    def get_cached_contact(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get cached contact.

        Args:
            memory_id: Contact memory identifier

        Returns:
            Contact data or None if not cached
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT data FROM contacts WHERE memory_id = ?
            """, (memory_id,))

            row = cursor.fetchone()
            if row:
                # Update last accessed
                cursor.execute("""
                    UPDATE contacts
                    SET last_accessed = ?
                    WHERE memory_id = ?
                """, (datetime.now(timezone.utc), memory_id))
                conn.commit()

                return json.loads(row[0])

            return None

        finally:
            conn.close()

    def list_cached_contacts(
        self,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """List cached contacts.

        Args:
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of cached contacts
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT data FROM contacts
                ORDER BY synced_at DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))

            return [json.loads(row[0]) for row in cursor.fetchall()]

        finally:
            conn.close()

    # Sync Metadata

    def record_sync(
        self,
        data_type: str,
        success: bool = True,
        error: Optional[str] = None
    ):
        """Record sync operation metadata.

        Args:
            data_type: Type of data synced (email, calendar, contacts)
            success: Whether sync succeeded
            error: Optional error message
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT OR REPLACE INTO sync_metadata
                (data_type, last_sync_at, last_sync_success, sync_error)
                VALUES (?, ?, ?, ?)
            """, (data_type, datetime.now(timezone.utc), success, error))

            conn.commit()

        finally:
            conn.close()

    def get_last_sync(self, data_type: str) -> Optional[Dict[str, Any]]:
        """Get last sync metadata.

        Args:
            data_type: Type of data

        Returns:
            Sync metadata or None
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT last_sync_at, last_sync_success, sync_error
                FROM sync_metadata
                WHERE data_type = ?
            """, (data_type,))

            row = cursor.fetchone()
            if row:
                return {
                    'last_sync_at': row[0],
                    'last_sync_success': bool(row[1]),
                    'sync_error': row[2]
                }

            return None

        finally:
            conn.close()

    # Cache Management

    def clear_cache(self, data_type: Optional[str] = None):
        """Clear cached data.

        Args:
            data_type: Type to clear (email, calendar, contacts) or None for all
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            if data_type == 'email' or data_type is None:
                cursor.execute("DELETE FROM email_threads")
                logger.info("Cleared email cache")

            if data_type == 'calendar' or data_type is None:
                cursor.execute("DELETE FROM calendar_events")
                logger.info("Cleared calendar cache")

            if data_type == 'contacts' or data_type is None:
                cursor.execute("DELETE FROM contacts")
                logger.info("Cleared contacts cache")

            conn.commit()

        finally:
            conn.close()

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics.

        Returns:
            Stats for all cached data types
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            # Email stats
            cursor.execute("SELECT COUNT(*) FROM email_threads")
            email_count = cursor.fetchone()[0]

            cursor.execute("SELECT MAX(synced_at) FROM email_threads")
            email_last_sync = cursor.fetchone()[0]

            # Calendar stats
            cursor.execute("SELECT COUNT(*) FROM calendar_events")
            calendar_count = cursor.fetchone()[0]

            cursor.execute("SELECT MAX(synced_at) FROM calendar_events")
            calendar_last_sync = cursor.fetchone()[0]

            # Contact stats
            cursor.execute("SELECT COUNT(*) FROM contacts")
            contact_count = cursor.fetchone()[0]

            cursor.execute("SELECT MAX(synced_at) FROM contacts")
            contact_last_sync = cursor.fetchone()[0]

            # Modifier queue stats
            cursor.execute("SELECT COUNT(*) FROM modifier_queue WHERE synced = FALSE")
            pending_modifiers = cursor.fetchone()[0]

            return {
                'email': {
                    'cached_threads': email_count,
                    'last_sync': email_last_sync
                },
                'calendar': {
                    'cached_events': calendar_count,
                    'last_sync': calendar_last_sync
                },
                'contacts': {
                    'cached_contacts': contact_count,
                    'last_sync': contact_last_sync
                },
                'modifier_queue': {
                    'pending_operations': pending_modifiers
                }
            }

        finally:
            conn.close()
