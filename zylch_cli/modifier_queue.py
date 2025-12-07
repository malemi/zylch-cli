"""Offline modifier queue for CLI thin client.

This module implements the "modifier pattern" for offline operations.
When the CLI is offline, modifications are queued locally and synced
when connection is restored (Superhuman-style).

Operations supported:
- email_draft: Create email draft
- email_send: Send email
- calendar_create: Create calendar event
- calendar_update: Update calendar event
- contact_update: Update contact
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ModifierQueue:
    """Offline operation queue with sync support.

    This class manages offline modifications ("modifiers") that are queued
    when the client is offline and synced to the server when online.

    Each modifier has:
    - Unique client_id for idempotency
    - Operation type (email_draft, email_send, etc.)
    - Operation data (payload)
    - Sync status (pending, synced, failed)

    Example:
        queue = ModifierQueue()

        # Offline: Queue modification
        queue.add_modifier('email_draft', {
            'to': 'test@example.com',
            'subject': 'Test',
            'body': 'Hello'
        })

        # Online: Sync to server
        results = await queue.sync_modifiers(api_client)
    """

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize modifier queue.

        Args:
            db_path: Path to local SQLite database.
                    Uses same DB as LocalStorage (~/.zylch/local_data.db)
        """
        if db_path is None:
            db_path = Path.home() / ".zylch" / "local_data.db"

        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._ensure_table()
        logger.info(f"ModifierQueue initialized at {self.db_path}")

    def _ensure_table(self):
        """Ensure modifier_queue table exists."""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS modifier_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT UNIQUE NOT NULL,
                operation_type TEXT NOT NULL,
                operation_data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                synced BOOLEAN DEFAULT FALSE,
                synced_at TIMESTAMP,
                sync_error TEXT,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_modifier_synced
            ON modifier_queue(synced)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_modifier_created_at
            ON modifier_queue(created_at)
        """)

        conn.commit()
        conn.close()

    def add_modifier(
        self,
        operation_type: str,
        operation_data: Dict[str, Any],
        client_id: Optional[str] = None
    ) -> str:
        """Add offline modification to queue.

        Args:
            operation_type: Type of operation (email_draft, email_send, etc.)
            operation_data: Operation payload
            client_id: Optional client-generated unique ID (auto-generated if not provided)

        Returns:
            Client ID of the queued modifier
        """
        if client_id is None:
            client_id = f"cli-{uuid.uuid4()}"

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO modifier_queue
                (client_id, operation_type, operation_data, created_at)
                VALUES (?, ?, ?, ?)
            """, (
                client_id,
                operation_type,
                json.dumps(operation_data),
                datetime.now(timezone.utc)
            ))

            conn.commit()
            logger.info(f"Queued modifier {client_id}: {operation_type}")

            return client_id

        except sqlite3.IntegrityError:
            # Idempotency: client_id already exists
            logger.warning(f"Modifier {client_id} already queued")
            return client_id

        finally:
            conn.close()

    def get_pending_modifiers(
        self,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get pending (unsynced) modifiers.

        Args:
            limit: Maximum number of modifiers to return

        Returns:
            List of pending modifiers
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            query = """
                SELECT id, client_id, operation_type, operation_data,
                       created_at, retry_count, max_retries
                FROM modifier_queue
                WHERE synced = FALSE
                  AND retry_count < max_retries
                ORDER BY created_at ASC
            """

            if limit:
                query += f" LIMIT {limit}"

            cursor.execute(query)

            modifiers = []
            for row in cursor.fetchall():
                modifiers.append({
                    'id': row[0],
                    'client_id': row[1],
                    'operation_type': row[2],
                    'operation_data': json.loads(row[3]),
                    'created_at': row[4],
                    'retry_count': row[5],
                    'max_retries': row[6]
                })

            return modifiers

        finally:
            conn.close()

    def mark_synced(
        self,
        client_id: str,
        success: bool = True,
        error: Optional[str] = None
    ):
        """Mark modifier as synced.

        Args:
            client_id: Client ID of modifier
            success: Whether sync succeeded
            error: Optional error message if failed
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            if success:
                cursor.execute("""
                    UPDATE modifier_queue
                    SET synced = TRUE,
                        synced_at = ?,
                        sync_error = NULL
                    WHERE client_id = ?
                """, (datetime.now(timezone.utc), client_id))

                logger.info(f"Modifier {client_id} synced successfully")

            else:
                # Increment retry count
                cursor.execute("""
                    UPDATE modifier_queue
                    SET retry_count = retry_count + 1,
                        sync_error = ?
                    WHERE client_id = ?
                """, (error, client_id))

                logger.warning(f"Modifier {client_id} sync failed: {error}")

            conn.commit()

        finally:
            conn.close()

    async def sync_modifiers(self, api_client) -> Dict[str, Any]:
        """Sync pending modifiers to server.

        Args:
            api_client: ZylchAPIClient instance

        Returns:
            Sync results with success/failure counts
        """
        pending = self.get_pending_modifiers()

        if not pending:
            logger.info("No pending modifiers to sync")
            return {
                'success': True,
                'total': 0,
                'synced': 0,
                'failed': 0
            }

        logger.info(f"Syncing {len(pending)} pending modifiers...")

        # Prepare operations for batch API call
        operations = []
        for modifier in pending:
            operations.append({
                'type': modifier['operation_type'],
                'data': modifier['operation_data'],
                'timestamp': modifier['created_at'],
                'client_id': modifier['client_id']
            })

        # Call API
        try:
            response = api_client.apply_modifiers(operations)

            # Process results
            synced_count = 0
            failed_count = 0

            for result in response.get('results', []):
                client_id = result.get('client_id')
                status = result.get('status')

                if status in ['success', 'pending']:
                    self.mark_synced(client_id, success=True)
                    synced_count += 1
                else:
                    error = result.get('error', 'Unknown error')
                    self.mark_synced(client_id, success=False, error=error)
                    failed_count += 1

            logger.info(f"Sync complete: {synced_count} synced, {failed_count} failed")

            return {
                'success': (failed_count == 0),
                'total': len(pending),
                'synced': synced_count,
                'failed': failed_count
            }

        except Exception as e:
            logger.error(f"Failed to sync modifiers: {e}")

            # Mark all as failed with retry
            for modifier in pending:
                self.mark_synced(
                    modifier['client_id'],
                    success=False,
                    error=str(e)
                )

            return {
                'success': False,
                'total': len(pending),
                'synced': 0,
                'failed': len(pending),
                'error': str(e)
            }

    def get_failed_modifiers(self) -> List[Dict[str, Any]]:
        """Get modifiers that failed to sync (max retries reached).

        Returns:
            List of failed modifiers
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT id, client_id, operation_type, operation_data,
                       created_at, retry_count, max_retries, sync_error
                FROM modifier_queue
                WHERE synced = FALSE
                  AND retry_count >= max_retries
                ORDER BY created_at DESC
            """)

            modifiers = []
            for row in cursor.fetchall():
                modifiers.append({
                    'id': row[0],
                    'client_id': row[1],
                    'operation_type': row[2],
                    'operation_data': json.loads(row[3]),
                    'created_at': row[4],
                    'retry_count': row[5],
                    'max_retries': row[6],
                    'sync_error': row[7]
                })

            return modifiers

        finally:
            conn.close()

    def delete_modifier(self, client_id: str) -> bool:
        """Delete modifier from queue.

        Args:
            client_id: Client ID of modifier to delete

        Returns:
            True if deleted, False if not found
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                DELETE FROM modifier_queue WHERE client_id = ?
            """, (client_id,))

            conn.commit()

            if cursor.rowcount > 0:
                logger.info(f"Deleted modifier {client_id}")
                return True
            else:
                return False

        finally:
            conn.close()

    def clear_synced(self, older_than_days: int = 7):
        """Clear old synced modifiers.

        Args:
            older_than_days: Delete synced modifiers older than N days
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cutoff = datetime.now(timezone.utc).timestamp() - (older_than_days * 86400)

            cursor.execute("""
                DELETE FROM modifier_queue
                WHERE synced = TRUE
                  AND synced_at < datetime(?, 'unixepoch')
            """, (cutoff,))

            deleted = cursor.rowcount
            conn.commit()

            if deleted > 0:
                logger.info(f"Cleared {deleted} old synced modifiers")

        finally:
            conn.close()

    def get_queue_stats(self) -> Dict[str, Any]:
        """Get modifier queue statistics.

        Returns:
            Queue stats
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            # Pending count
            cursor.execute("""
                SELECT COUNT(*) FROM modifier_queue
                WHERE synced = FALSE AND retry_count < max_retries
            """)
            pending = cursor.fetchone()[0]

            # Failed count
            cursor.execute("""
                SELECT COUNT(*) FROM modifier_queue
                WHERE synced = FALSE AND retry_count >= max_retries
            """)
            failed = cursor.fetchone()[0]

            # Synced count (last 7 days)
            cursor.execute("""
                SELECT COUNT(*) FROM modifier_queue
                WHERE synced = TRUE
                  AND synced_at >= datetime('now', '-7 days')
            """)
            synced_recent = cursor.fetchone()[0]

            # Oldest pending
            cursor.execute("""
                SELECT MIN(created_at) FROM modifier_queue
                WHERE synced = FALSE
            """)
            oldest_pending = cursor.fetchone()[0]

            return {
                'pending': pending,
                'failed': failed,
                'synced_last_7_days': synced_recent,
                'oldest_pending': oldest_pending
            }

        finally:
            conn.close()
