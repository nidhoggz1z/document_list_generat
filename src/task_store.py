"""SQLite task identity and persistent ordering, independent of HTTP and workers.

Pass TaskSettings.storage_root. Connections live for one operation, so a store
can be shared by threads; separate store instances also serialize via SQLite.
PDF bytes and the execution loop belong to other modules.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from task_models import BusinessData, HistoricalTaskResult, TaskResult


class TaskStore:
    def __init__(self, storage_root):
        self.storage_root = Path(storage_root).resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.storage_root / 'tasks.sqlite3'
        with self._connection(write=True) as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS task_sequence (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                value INTEGER NOT NULL)''')
            connection.execute('INSERT OR IGNORE INTO task_sequence VALUES (1, 0)')
            connection.execute('''CREATE TABLE IF NOT EXISTS tasks (
                bsnEvtId TEXT NOT NULL, taskId TEXT NOT NULL, userNo TEXT NOT NULL,
                generationId TEXT NOT NULL UNIQUE, sequence INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK (status IN
                    ('PENDING','PROCESSING','DONE','FAILED','TERMINATED')),
                businessData TEXT NOT NULL, pdfPath TEXT NOT NULL,
                sourceDocumentHash TEXT, result TEXT, reason TEXT,
                artifacts TEXT NOT NULL, createdAt TEXT NOT NULL, updatedAt TEXT NOT NULL,
                PRIMARY KEY (bsnEvtId, taskId))''')

    @contextmanager
    def _connection(self, *, write=False):
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            if write:
                connection.execute('BEGIN IMMEDIATE')
            yield connection
            if write:
                connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def generation_path(self, generation_id):
        """Service-owned path for a UUID; never use a business/upload filename."""
        generation_id = UUID(str(generation_id)).hex
        return self.storage_root / 'generations' / generation_id / 'source.pdf'

    def _prepare(self, business_data, generation_id, source_document_hash):
        data = BusinessData.model_validate(business_data).model_dump(mode='json')
        generation_id = UUID(str(generation_id)).hex if generation_id else uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        return dict(bsnEvtId=data['bsnEvtId'], taskId=data['taskId'], userNo=data['userNo'],
                    generationId=generation_id, sequence=None, status='PENDING',
                    businessData=json.dumps(data, ensure_ascii=False, allow_nan=False),
                    pdfPath=str(self.generation_path(generation_id)),
                    sourceDocumentHash=source_document_hash, result=None, reason=None,
                    artifacts='{}', createdAt=now, updatedAt=now)

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        record = dict(row)
        for key in ('businessData', 'artifacts', 'result'):
            if record[key] is not None:
                record[key] = json.loads(record[key])
        return record

    def _insert(self, connection, record):
        connection.execute('UPDATE task_sequence SET value = value + 1 WHERE singleton = 1')
        record['sequence'] = connection.execute('SELECT value FROM task_sequence').fetchone()[0]
        connection.execute('''INSERT INTO tasks VALUES (
            :bsnEvtId, :taskId, :userNo, :generationId, :sequence, :status,
            :businessData, :pdfPath, :sourceDocumentHash, :result, :reason,
            :artifacts, :createdAt, :updatedAt)''', record)

    def insert_task(self, business_data, *, generation_id=None, source_document_hash=None):
        """Base insert; duplicate identity fails without changing the saved task.

        Submission should use accept_task once replacement semantics are needed.
        A caller may preallocate an internal UUID to save files before inserting.
        """
        record = self._prepare(business_data, generation_id, source_document_hash)
        with self._connection(write=True) as connection:
            self._insert(connection, record)
        return self._decode(record)

    def get_task(self, bsn_evt_id, task_id):
        """One committed snapshot, keyed solely by business and task identifiers."""
        with self._connection() as connection:
            row = connection.execute('SELECT * FROM tasks WHERE bsnEvtId = ? AND taskId = ?',
                                     (bsn_evt_id, task_id)).fetchone()
        return self._decode(row)

    def accept_task(self, business_data, *, generation_id=None, source_document_hash=None):
        """Atomically replace unfinished siblings, overwrite this key and enqueue.

        Returns (new generationId, cancelled generationIds) only after commit.
        The caller cancels local execution after this returns; no notifications or
        file writes occur here. Failed acceptance leaves all old state intact.
        BEGIN IMMEDIATE serializes acceptance before reading old generations;
        the durable counter is advanced in the same transaction as the records.
        """
        record = self._prepare(business_data, generation_id, source_document_hash)
        with self._connection(write=True) as connection:
            # A preallocated generation must be fresh, including for the same key.
            if connection.execute('SELECT 1 FROM tasks WHERE generationId = ?',
                                  (record['generationId'],)).fetchone():
                raise ValueError('generationId must identify a new submission')
            cancelled = [row[0] for row in connection.execute(
                "SELECT generationId FROM tasks WHERE bsnEvtId = ? "
                "AND status IN ('PENDING', 'PROCESSING') ORDER BY sequence",
                (record['bsnEvtId'],))]
            connection.execute(
                "UPDATE tasks SET status = 'TERMINATED', reason = ?, updatedAt = ? "
                "WHERE bsnEvtId = ? AND status IN ('PENDING', 'PROCESSING')",
                ('被同业务新任务替换', record['createdAt'], record['bsnEvtId']))
            connection.execute('DELETE FROM tasks WHERE bsnEvtId = ? AND taskId = ?',
                               (record['bsnEvtId'], record['taskId']))
            self._insert(connection, record)
        return record['generationId'], cancelled

    def claim_next(self):
        """Atomically claim the oldest PENDING row, or return None.

        Callers run one consumer loop (T21); this transaction prevents two
        callers from claiming the same generation, not multiple consumers.
        """
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE status = 'PENDING' ORDER BY sequence LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "UPDATE tasks SET status = 'PROCESSING', updatedAt = ? "
                "WHERE generationId = ? AND status = 'PENDING'", (now, row['generationId']))
            record = dict(row, status='PROCESSING', updatedAt=now)
        return self._decode(record)

    def save_artifact(self, generation_id, name, value):
        """Save a named JSON stage value/pointer for the current PROCESSING run.

        Merge within the write transaction so concurrent stages cannot lose
        each other's entries. This stores metadata, never writes stage files.
        Returns False when the generation is obsolete or no longer processing.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError('artifact name must be a nonblank string')
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT artifacts FROM tasks WHERE generationId = ? AND status = 'PROCESSING'",
                (generation_id,)).fetchone()
            if row is None:
                return False
            artifacts = json.loads(row['artifacts'])
            artifacts[name] = value
            connection.execute(
                "UPDATE tasks SET artifacts = ?, updatedAt = ? "
                "WHERE generationId = ? AND status = 'PROCESSING'",
                (json.dumps(artifacts, ensure_ascii=False, allow_nan=False),
                 datetime.now(timezone.utc).isoformat(), generation_id))
        return True

    def finish_done(self, generation_id, result):
        """Validate complete data, then publish it with DONE in one transaction.

        Returns False for a stale/terminal generation; storage errors propagate.
        Semantic rule coverage is checked by the pipeline before this call.
        """
        model = HistoricalTaskResult if isinstance(result, dict) and 'sourceDocumentHash' in result else TaskResult
        result = model.model_validate(result).model_dump(mode='json')
        with self._connection(write=True) as connection:
            changed = connection.execute(
                "UPDATE tasks SET status = 'DONE', result = ?, reason = NULL, updatedAt = ? "
                "WHERE generationId = ? AND status = 'PROCESSING'",
                (json.dumps(result, ensure_ascii=False, allow_nan=False),
                 datetime.now(timezone.utc).isoformat(), generation_id)).rowcount
        return changed == 1

    def finish_failed(self, generation_id, reason):
        """Publish a nonblank failure reason only for the current PROCESSING run."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('failure reason must be a nonblank string')
        with self._connection(write=True) as connection:
            changed = connection.execute(
                "UPDATE tasks SET status = 'FAILED', reason = ?, updatedAt = ? "
                "WHERE generationId = ? AND status = 'PROCESSING'",
                (reason, datetime.now(timezone.utc).isoformat(), generation_id)).rowcount
        return changed == 1

    def recover_interrupted(self):
        """Fail unfinished runs on startup; return a count, never a requeue list.

        Call only after exclusive service ownership is established (T22).
        Terminal rows and all stored inputs/artifacts/results remain unchanged.
        """
        with self._connection(write=True) as connection:
            changed = connection.execute(
                "UPDATE tasks SET status = 'FAILED', reason = ?, updatedAt = ? "
                "WHERE status IN ('PENDING', 'PROCESSING')",
                ('服务中断，任务未完成', datetime.now(timezone.utc).isoformat())).rowcount
        return changed
