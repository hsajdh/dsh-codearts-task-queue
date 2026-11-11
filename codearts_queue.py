"""Local task handoff using the official MCP connector in CodeArts Desktop."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid

from mcp.server.fastmcp import FastMCP

DATABASE = Path(os.environ.get('CODEARTS_QUEUE_DATABASE',
                               str(Path.home() / '.dsh-codearts-task-queue/queue.sqlite'))).expanduser()


@contextmanager
def database():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, project TEXT NOT NULL, request_key TEXT NOT NULL,
        title TEXT NOT NULL, instructions TEXT NOT NULL, acceptance TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'queued', claim_key TEXT, lease_token TEXT,
        lease_until REAL, progress TEXT NOT NULL DEFAULT '', delivery TEXT,
        review TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        UNIQUE(project, request_key)
    )''')
    connection.commit()
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def task_row(connection, task_id: str):
    row = connection.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    if row is None:
        raise ValueError('Unknown CodeArts handoff task')
    return row


def view(row, include_lease=False):
    result = dict(row)
    result['acceptance'] = json.loads(result['acceptance'])
    result['delivery'] = json.loads(result['delivery']) if result['delivery'] else None
    result['lease_expired'] = bool(result['lease_until'] and result['lease_until'] < time.time())
    if not include_lease:
        result.pop('lease_token', None)
        result.pop('claim_key', None)
    return result


def validate_key(key: str):
    if not key or len(key) > 120:
        raise ValueError('Provide a nonempty key of at most 120 characters')


def submit_task(project_dir: str, request_key: str, title: str, instructions: str,
                acceptance: list[str]) -> dict:
    """Queue one user-authorized task for CodeArts Desktop. Reuse request_key when retrying.

    The user manually asks CodeArts Desktop to receive a task through its official connector.
    DSH remains responsible for independently reviewing files and tests after delivery.
    """
    validate_key(request_key)
    folder = Path(project_dir).expanduser()
    if not folder.is_absolute() or not folder.is_dir():
        raise ValueError('project_dir must be an existing absolute project directory')
    if not title.strip() or not instructions.strip() or len(instructions) > 100000:
        raise ValueError('Provide a title and nonempty instructions of at most 100000 characters')
    if not acceptance or len(acceptance) > 30 or not all(item.strip() for item in acceptance):
        raise ValueError('Provide 1-30 concrete acceptance criteria')
    project = str(folder.resolve())
    criteria = json.dumps(acceptance, ensure_ascii=False)
    with database() as connection:
        previous = connection.execute('SELECT * FROM tasks WHERE project=? AND request_key=?',
                                      (project, request_key)).fetchone()
        if previous:
            if (previous['title'], previous['instructions'], previous['acceptance']) != (title, instructions, criteria):
                raise ValueError('This request_key is already used for a different task')
            return view(previous)
        task_id = 'ca-' + uuid.uuid4().hex[:16]
        now = time.time()
        connection.execute('''INSERT INTO tasks
            (id,project,request_key,title,instructions,acceptance,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?)''', (task_id, project, request_key, title, instructions, criteria, now, now))
        return {**view(task_row(connection, task_id)),
                'next_step': 'In CodeArts Desktop, use the DSH task queue connector and send: 领取并处理一个 DSH 任务'}


def queue_status(task_id: str = '') -> dict:
    """Read a task or the last 30 tasks, including progress and deliveries awaiting DSH review."""
    with database() as connection:
        if task_id:
            return view(task_row(connection, task_id))
        rows = connection.execute('SELECT * FROM tasks ORDER BY created_at DESC LIMIT 30').fetchall()
        counts = dict(connection.execute('SELECT state,count(*) FROM tasks GROUP BY state').fetchall())
        return {'counts': counts, 'tasks': [view(row) for row in rows]}


def claim_next(claim_key: str, lease_seconds: int = 3600) -> dict:
    """Receive one task in CodeArts Desktop after the user asks to receive a DSH task.

    Use one stable claim_key for this receive action and reuse it on retries. Only one
    task executes at a time. When the queue is empty, stop without polling or scheduling.
    Respect the project directory and all native CodeArts permission confirmations.
    """
    validate_key(claim_key)
    if not 300 <= lease_seconds <= 7200:
        raise ValueError('lease_seconds must be between 300 and 7200')
    with database() as connection:
        active = connection.execute("SELECT * FROM tasks WHERE state IN ('running','needs_attention') LIMIT 1").fetchone()
        if active:
            if active['claim_key'] == claim_key:
                return {'task': view(active, True), 'reused': True}
            return {'task': None, 'busy': True, 'active_task_id': active['id'],
                    'needs_attention': bool(active['lease_until'] and active['lease_until'] < time.time())}
        row = connection.execute("SELECT * FROM tasks WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
        if row is None:
            return {'task': None, 'busy': False, 'instruction': 'Queue empty. Stop and wait for the user.'}
        now = time.time()
        connection.execute("UPDATE tasks SET state='running',claim_key=?,lease_token=?,lease_until=?,updated_at=? WHERE id=?",
                           (claim_key, secrets.token_urlsafe(24), now + lease_seconds, now, row['id']))
        return {'task': view(task_row(connection, row['id']), True), 'reused': False}


def owned_task(connection, task_id, lease_token):
    row = task_row(connection, task_id)
    if row['state'] not in ['running', 'needs_attention'] or not row['lease_token']:
        raise ValueError('This task is not owned by an active desktop executor')
    if not secrets.compare_digest(row['lease_token'], lease_token):
        raise ValueError('Task ownership token does not match')
    return row


def report_progress(task_id: str, lease_token: str, summary: str) -> dict:
    """Report actual progress and renew ownership. Stop if cancellation is requested."""
    with database() as connection:
        row = owned_task(connection, task_id, lease_token)
        now = time.time()
        connection.execute('UPDATE tasks SET progress=?,lease_until=?,updated_at=? WHERE id=?',
                           (summary[:10000], now + 3600, now, task_id))
        return {'task_id': task_id, 'cancel_requested': bool(row['cancel_requested']), 'lease_until': now + 3600}


def report_delivery(task_id: str, lease_token: str, summary: str, artifacts: list[str],
                    tests: list[str], blocked: bool = False) -> dict:
    """Report project deliverables through MCP after finishing the task.

    Use real project files and actual test results. Delivery remains awaiting_review until
    parent DSH independently checks it. Report blocked when native permission or user input is needed.
    """
    with database() as connection:
        row = owned_task(connection, task_id, lease_token)
        project = Path(row['project']).resolve()
        paths = []
        for item in artifacts:
            path = Path(item)
            if not path.is_absolute():
                path = project / path
            path = path.resolve()
            if not path.is_relative_to(project) or not path.exists():
                raise ValueError('Deliverables must exist inside the assigned project directory')
            paths.append(str(path))
        delivery = {'summary': summary, 'artifacts': paths, 'tests': tests}
        if row['cancel_requested']:
            state = 'canceled'
        elif blocked:
            state = 'needs_attention'
        else:
            state = 'awaiting_review'
        connection.execute('UPDATE tasks SET state=?,delivery=?,updated_at=? WHERE id=?',
                           (state, json.dumps(delivery, ensure_ascii=False), time.time(), task_id))
        return {'task_id': task_id, 'state': state, 'instruction': 'Parent DSH must independently review the deliverables.'}


def review_delivery(task_id: str, accepted: bool, evidence: str) -> dict:
    """DSH records its independent file/test review. A rejected delivery returns to the queue with feedback."""
    if not evidence.strip():
        raise ValueError('Record concrete independent review evidence')
    with database() as connection:
        row = task_row(connection, task_id)
        if row['state'] != 'awaiting_review':
            raise ValueError('This task has no delivery awaiting review')
        state = 'accepted' if accepted else 'queued'
        connection.execute('''UPDATE tasks SET state=?,review=?,lease_token=NULL,claim_key=NULL,
            lease_until=NULL,cancel_requested=0,updated_at=? WHERE id=?''',
                           (state, evidence[:30000], time.time(), task_id))
        return view(task_row(connection, task_id))


def cancel_task(task_id: str, executor_stopped: bool = False) -> dict:
    """Cancel queued tasks. For an executing task, first request cancellation.

    Confirm executor_stopped only after the user/native desktop actually stops execution.
    This tool never terminates CodeArts Desktop or changes its account/session state.
    """
    with database() as connection:
        row = task_row(connection, task_id)
        if row['state'] in ['accepted', 'canceled']:
            return view(row)
        if row['state'] in ['running', 'needs_attention'] and not executor_stopped:
            connection.execute('UPDATE tasks SET cancel_requested=1,updated_at=? WHERE id=?', (time.time(), task_id))
        else:
            connection.execute("UPDATE tasks SET state='canceled',lease_token=NULL,claim_key=NULL,lease_until=NULL,updated_at=? WHERE id=?",
                               (time.time(), task_id))
        return view(task_row(connection, task_id))


def main():
    global DATABASE
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', required=True, choices=['dispatcher', 'executor'])
    parser.add_argument('--database', type=Path, default=DATABASE,
                        help='Shared absolute SQLite path. Both clients must use the same file.')
    args = parser.parse_args()
    DATABASE = args.database.expanduser()
    if not DATABASE.is_absolute():
        parser.error('--database must be an absolute path')
    DATABASE = DATABASE.resolve()
    mcp = FastMCP('DSH CodeArts Desktop Queue', instructions=(
        'Use this local queue only for current user-authorized DSH delegation. '
        'CodeArts Desktop receives one task when the user asks. Do not loop on an empty queue. '
        'Preserve native permission confirmations. Report project deliverables through tools. '
        'DSH separately inspects files and tests before accepting delivery.'))
    read_only = {'readOnlyHint': True, 'openWorldHint': False}
    writes = {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False}
    if args.role == 'dispatcher':
        for function in [submit_task, review_delivery, cancel_task]:
            mcp.tool(annotations=writes)(function)
        mcp.tool(annotations=read_only)(queue_status)
    else:
        for function in [claim_next, report_progress, report_delivery]:
            mcp.tool(annotations=writes)(function)
    mcp.run(transport='stdio')


if __name__ == '__main__':
    main()
