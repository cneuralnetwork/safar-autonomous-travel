from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
from cryptography.fernet import Fernet

from app.config import Settings
from app.models import (
    AgentEvent,
    ChatMessage,
    Conversation,
    ModelCallRecord,
    RunState,
    TaskNode,
    UserPreferences,
)


class StoreError(RuntimeError):
    pass


class IdempotencyConflict(StoreError):
    pass


class Store(ABC):
    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def create_conversation(self, conversation: Conversation) -> Conversation: ...

    @abstractmethod
    async def get_conversation(
        self, conversation_id: UUID, user_id: str
    ) -> Conversation | None: ...

    @abstractmethod
    async def list_conversations(self, user_id: str) -> list[Conversation]: ...

    @abstractmethod
    async def update_conversation(self, conversation: Conversation) -> None: ...

    @abstractmethod
    async def add_message(
        self, message: ChatMessage, idempotency_key: str | None = None
    ) -> ChatMessage: ...

    @abstractmethod
    async def update_message_run_id(
        self, message_id: UUID, run_id: UUID, user_id: str
    ) -> None: ...

    @abstractmethod
    async def list_messages(self, conversation_id: UUID, user_id: str) -> list[ChatMessage]: ...

    @abstractmethod
    async def save_run(self, run: RunState) -> None: ...

    @abstractmethod
    async def get_run(self, run_id: UUID, user_id: str) -> RunState | None: ...

    @abstractmethod
    async def get_active_run(self, conversation_id: UUID, user_id: str) -> RunState | None: ...

    @abstractmethod
    async def list_recoverable_runs(self) -> list[RunState]: ...

    @abstractmethod
    async def add_event(self, event: AgentEvent) -> AgentEvent: ...

    @abstractmethod
    async def list_events(
        self,
        run_id: UUID,
        user_id: str,
        *,
        after_id: int = 0,
        limit: int = 200,
    ) -> list[AgentEvent]: ...

    @abstractmethod
    async def add_model_call(self, call: ModelCallRecord) -> None: ...

    @abstractmethod
    async def list_model_calls(
        self,
        run_id: UUID,
        user_id: str,
    ) -> list[ModelCallRecord]: ...

    @abstractmethod
    async def save_task(self, run: RunState, task: TaskNode) -> None: ...

    @abstractmethod
    async def claim_run(self, run_id: UUID, worker_id: str) -> bool: ...

    @abstractmethod
    async def release_run(self, run_id: UUID, worker_id: str) -> None: ...

    @abstractmethod
    async def save_calendar_connection(self, user_id: str, payload: dict[str, Any]) -> None: ...

    @abstractmethod
    async def get_calendar_connection(self, user_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def delete_calendar_connection(self, user_id: str) -> None: ...

    @abstractmethod
    async def get_user_preferences(self, user_id: str) -> UserPreferences | None: ...

    @abstractmethod
    async def save_user_preferences(self, preferences: UserPreferences) -> None: ...


class SQLiteStore(Store):
    def __init__(self, path: str) -> None:
        self.path = path
        self._memory_connection: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self.path == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(self.path, timeout=10)
            connection = self._memory_connection
        else:
            connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("pragma journal_mode = wal")
        connection.execute("pragma foreign_keys = on")
        return connection

    async def initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                create table if not exists conversations (
                    id text primary key,
                    user_id text not null,
                    payload text not null,
                    updated_at text not null
                );
                create index if not exists conversations_user_updated
                    on conversations(user_id, updated_at desc);

                create table if not exists messages (
                    id text primary key,
                    conversation_id text not null,
                    user_id text not null,
                    payload text not null,
                    created_at text not null,
                    idempotency_key text,
                    unique(user_id, idempotency_key)
                );
                create index if not exists messages_conversation_created
                    on messages(conversation_id, created_at);

                create table if not exists runs (
                    id text primary key,
                    conversation_id text not null,
                    user_id text not null,
                    status text not null,
                    payload text not null,
                    updated_at text not null
                );
                create index if not exists runs_conversation_updated
                    on runs(conversation_id, updated_at desc);

                create table if not exists calendar_connections (
                    user_id text primary key,
                    payload text not null,
                    updated_at text not null
                );

                create table if not exists user_preferences (
                    user_id text primary key,
                    payload text not null,
                    updated_at text not null
                );

                create table if not exists agent_events (
                    id integer primary key autoincrement,
                    run_id text not null,
                    conversation_id text not null,
                    user_id text not null,
                    payload text not null,
                    created_at text not null
                );
                create index if not exists agent_events_run_id
                    on agent_events(run_id, id);

                create table if not exists model_calls (
                    id text primary key,
                    run_id text not null,
                    conversation_id text not null,
                    user_id text not null,
                    payload text not null,
                    created_at text not null
                );
                create index if not exists model_calls_run_created
                    on model_calls(run_id, created_at);

                create table if not exists run_tasks (
                    run_id text not null,
                    task_id text not null,
                    user_id text not null,
                    payload text not null,
                    updated_at text not null,
                    primary key (run_id, task_id)
                );
                """
            )

    async def create_conversation(self, conversation: Conversation) -> Conversation:
        with self._connect() as db:
            db.execute(
                "insert into conversations(id, user_id, payload, updated_at) values (?, ?, ?, ?)",
                (
                    str(conversation.id),
                    conversation.user_id,
                    conversation.model_dump_json(),
                    conversation.updated_at.isoformat(),
                ),
            )
        return conversation

    async def get_conversation(self, conversation_id: UUID, user_id: str) -> Conversation | None:
        with self._connect() as db:
            cursor = db.execute(
                "select payload from conversations where id = ? and user_id = ?",
                (str(conversation_id), user_id),
            )
            row = cursor.fetchone()
        return Conversation.model_validate_json(row[0]) if row else None

    async def list_conversations(self, user_id: str) -> list[Conversation]:
        with self._connect() as db:
            cursor = db.execute(
                "select payload from conversations where user_id = ? order by updated_at desc",
                (user_id,),
            )
            rows = cursor.fetchall()
        return [Conversation.model_validate_json(row[0]) for row in rows]

    async def update_conversation(self, conversation: Conversation) -> None:
        with self._connect() as db:
            db.execute(
                "update conversations set payload = ?, updated_at = ? where id = ? and user_id = ?",
                (
                    conversation.model_dump_json(),
                    conversation.updated_at.isoformat(),
                    str(conversation.id),
                    conversation.user_id,
                ),
            )

    async def add_message(
        self, message: ChatMessage, idempotency_key: str | None = None
    ) -> ChatMessage:
        with self._connect() as db:
            conversation = db.execute(
                "select user_id from conversations where id = ?",
                (str(message.conversation_id),),
            )
            row = conversation.fetchone()
            if not row:
                raise StoreError("Conversation does not exist")
            try:
                db.execute(
                    """
                    insert into messages(
                        id, conversation_id, user_id, payload, created_at, idempotency_key
                    ) values (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(message.id),
                        str(message.conversation_id),
                        row[0],
                        message.model_dump_json(),
                        message.created_at.isoformat(),
                        idempotency_key,
                    ),
                )
            except sqlite3.IntegrityError as error:
                if idempotency_key:
                    cursor = db.execute(
                        "select payload from messages where user_id = ? and idempotency_key = ?",
                        (row[0], idempotency_key),
                    )
                    existing = cursor.fetchone()
                    if existing:
                        return ChatMessage.model_validate_json(existing[0])
                raise IdempotencyConflict(str(error)) from error
        return message

    async def list_messages(self, conversation_id: UUID, user_id: str) -> list[ChatMessage]:
        with self._connect() as db:
            cursor = db.execute(
                """
                select payload from messages
                where conversation_id = ? and user_id = ?
                order by created_at asc
                """,
                (str(conversation_id), user_id),
            )
            rows = cursor.fetchall()
        return [ChatMessage.model_validate_json(row[0]) for row in rows]

    async def update_message_run_id(
        self, message_id: UUID, run_id: UUID, user_id: str
    ) -> None:
        with self._connect() as db:
            cursor = db.execute(
                "select payload from messages where id = ? and user_id = ?",
                (str(message_id), user_id),
            )
            row = cursor.fetchone()
            if not row:
                raise StoreError("Message does not exist")
            message = ChatMessage.model_validate_json(row[0])
            message.run_id = run_id
            db.execute(
                "update messages set payload = ? where id = ? and user_id = ?",
                (message.model_dump_json(), str(message_id), user_id),
            )

    async def save_run(self, run: RunState) -> None:
        with self._connect() as db:
            db.execute(
                """
                insert into runs(id, conversation_id, user_id, status, payload, updated_at)
                values (?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                    status = excluded.status,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    str(run.id),
                    str(run.conversation_id),
                    run.user_id,
                    run.status.value,
                    run.model_dump_json(),
                    datetime.now().isoformat(),
                ),
            )

    async def get_run(self, run_id: UUID, user_id: str) -> RunState | None:
        with self._connect() as db:
            cursor = db.execute(
                "select payload from runs where id = ? and user_id = ?",
                (str(run_id), user_id),
            )
            row = cursor.fetchone()
        return RunState.model_validate_json(row[0]) if row else None

    async def get_active_run(self, conversation_id: UUID, user_id: str) -> RunState | None:
        with self._connect() as db:
            cursor = db.execute(
                """
                select payload from runs
                where conversation_id = ? and user_id = ?
                order by updated_at desc limit 1
                """,
                (str(conversation_id), user_id),
            )
            row = cursor.fetchone()
        return RunState.model_validate_json(row[0]) if row else None

    async def list_recoverable_runs(self) -> list[RunState]:
        with self._connect() as db:
            cursor = db.execute(
                """
                select payload from runs
                where status in ('planned', 'running', 'replanning')
                order by updated_at asc
                """
            )
            rows = cursor.fetchall()
        return [RunState.model_validate_json(row[0]) for row in rows]

    async def close(self) -> None:
        if self._memory_connection is not None:
            self._memory_connection.close()
            self._memory_connection = None

    async def add_event(self, event: AgentEvent) -> AgentEvent:
        with self._connect() as db:
            cursor = db.execute(
                """
                insert into agent_events(
                    run_id, conversation_id, user_id, payload, created_at
                ) values (?, ?, ?, ?, ?)
                """,
                (
                    str(event.run_id),
                    str(event.conversation_id),
                    event.user_id,
                    event.model_dump_json(exclude={"id"}),
                    event.created_at.isoformat(),
                ),
            )
            event.id = int(cursor.lastrowid)
            db.execute(
                "update agent_events set payload = ? where id = ?",
                (event.model_dump_json(), event.id),
            )
        return event

    async def list_events(
        self,
        run_id: UUID,
        user_id: str,
        *,
        after_id: int = 0,
        limit: int = 200,
    ) -> list[AgentEvent]:
        with self._connect() as db:
            cursor = db.execute(
                """
                select payload from agent_events
                where run_id = ? and user_id = ? and id > ?
                order by id asc limit ?
                """,
                (str(run_id), user_id, after_id, limit),
            )
            rows = cursor.fetchall()
        return [AgentEvent.model_validate_json(row[0]) for row in rows]

    async def add_model_call(self, call: ModelCallRecord) -> None:
        with self._connect() as db:
            db.execute(
                """
                insert into model_calls(
                    id, run_id, conversation_id, user_id, payload, created_at
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(call.id),
                    str(call.run_id),
                    str(call.conversation_id),
                    call.user_id,
                    call.model_dump_json(),
                    call.created_at.isoformat(),
                ),
            )

    async def list_model_calls(
        self,
        run_id: UUID,
        user_id: str,
    ) -> list[ModelCallRecord]:
        with self._connect() as db:
            cursor = db.execute(
                """
                select payload from model_calls
                where run_id = ? and user_id = ?
                order by created_at asc
                """,
                (str(run_id), user_id),
            )
            rows = cursor.fetchall()
        return [ModelCallRecord.model_validate_json(row[0]) for row in rows]

    async def save_task(self, run: RunState, task: TaskNode) -> None:
        with self._connect() as db:
            db.execute(
                """
                insert into run_tasks(run_id, task_id, user_id, payload, updated_at)
                values (?, ?, ?, ?, ?)
                on conflict(run_id, task_id) do update set
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    str(run.id),
                    task.id,
                    run.user_id,
                    task.model_dump_json(),
                    datetime.now().isoformat(),
                ),
            )

    async def claim_run(self, run_id: UUID, worker_id: str) -> bool:
        return True

    async def release_run(self, run_id: UUID, worker_id: str) -> None:
        return None

    async def save_calendar_connection(self, user_id: str, payload: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                """
                insert into calendar_connections(user_id, payload, updated_at)
                values (?, ?, ?)
                on conflict(user_id) do update set
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (user_id, json.dumps(payload), datetime.now().isoformat()),
            )

    async def get_calendar_connection(self, user_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            cursor = db.execute(
                "select payload from calendar_connections where user_id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
        return json.loads(row[0]) if row else None

    async def delete_calendar_connection(self, user_id: str) -> None:
        with self._connect() as db:
            db.execute("delete from calendar_connections where user_id = ?", (user_id,))

    async def get_user_preferences(self, user_id: str) -> UserPreferences | None:
        with self._connect() as db:
            cursor = db.execute(
                "select payload from user_preferences where user_id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
        return UserPreferences.model_validate_json(row[0]) if row else None

    async def save_user_preferences(self, preferences: UserPreferences) -> None:
        with self._connect() as db:
            db.execute(
                """
                insert into user_preferences(user_id, payload, updated_at)
                values (?, ?, ?)
                on conflict(user_id) do update set
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    preferences.user_id,
                    preferences.model_dump_json(),
                    preferences.updated_at.isoformat(),
                ),
            )


class SupabaseStore(Store):
    def __init__(self, url: str, secret_key: str) -> None:
        self.base_url = f"{url.rstrip('/')}/rest/v1"
        self.headers = {
            "apikey": secret_key,
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
        }
        self.client = httpx.AsyncClient(timeout=20)

    async def initialize(self) -> None:
        required_schema = {
            "conversations": "id",
            "runs": "id,harness_version,phase",
            "run_tasks": "run_id,task_id",
            "agent_events": "id",
            "model_calls": "id",
        }
        for table, columns in required_schema.items():
            response = await self.client.get(
                f"{self.base_url}/{table}",
                headers=self.headers,
                params={"select": columns, "limit": "1"},
            )
            if response.status_code >= 400:
                raise StoreError(
                    "Supabase agent schema is unavailable. "
                    "Apply every migration before starting production."
                )

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, str] | None = None,
        json_body: object | None = None,
        prefer: str | None = None,
    ) -> Any:
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        response = await self.client.request(
            method,
            f"{self.base_url}/{table}",
            headers=headers,
            params=params,
            json=json_body,
        )
        if response.status_code >= 400:
            raise StoreError(f"Supabase {table} request failed: {response.text[:400]}")
        return response.json() if response.content else None

    async def create_conversation(self, conversation: Conversation) -> Conversation:
        await self._request(
            "POST",
            "conversations",
            json_body=conversation.model_dump(mode="json"),
            prefer="return=minimal",
        )
        return conversation

    async def get_conversation(self, conversation_id: UUID, user_id: str) -> Conversation | None:
        rows = await self._request(
            "GET",
            "conversations",
            params={
                "select": "*",
                "id": f"eq.{conversation_id}",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        )
        return Conversation.model_validate(rows[0]) if rows else None

    async def list_conversations(self, user_id: str) -> list[Conversation]:
        rows = await self._request(
            "GET",
            "conversations",
            params={"select": "*", "user_id": f"eq.{user_id}", "order": "updated_at.desc"},
        )
        return [Conversation.model_validate(row) for row in rows]

    async def update_conversation(self, conversation: Conversation) -> None:
        await self._request(
            "PATCH",
            "conversations",
            params={"id": f"eq.{conversation.id}", "user_id": f"eq.{conversation.user_id}"},
            json_body=conversation.model_dump(mode="json"),
            prefer="return=minimal",
        )

    async def add_message(
        self, message: ChatMessage, idempotency_key: str | None = None
    ) -> ChatMessage:
        conversation = await self._request(
            "GET",
            "conversations",
            params={"select": "user_id", "id": f"eq.{message.conversation_id}", "limit": "1"},
        )
        if not conversation:
            raise StoreError("Conversation does not exist")
        row = message.model_dump(mode="json")
        row["user_id"] = conversation[0]["user_id"]
        row["idempotency_key"] = idempotency_key
        if idempotency_key:
            existing = await self._request(
                "GET",
                "messages",
                params={
                    "select": "*",
                    "user_id": f"eq.{conversation[0]['user_id']}",
                    "idempotency_key": f"eq.{idempotency_key}",
                    "limit": "1",
                },
            )
            if existing:
                return ChatMessage.model_validate(existing[0])
            inserted = await self._request(
                "POST",
                "messages",
                params={"on_conflict": "user_id,idempotency_key"},
                json_body=row,
                prefer="return=representation,resolution=ignore-duplicates",
            )
            if inserted:
                return ChatMessage.model_validate(inserted[0])
            existing = await self._request(
                "GET",
                "messages",
                params={
                    "select": "*",
                    "user_id": f"eq.{conversation[0]['user_id']}",
                    "idempotency_key": f"eq.{idempotency_key}",
                    "limit": "1",
                },
            )
            if existing:
                return ChatMessage.model_validate(existing[0])
            raise IdempotencyConflict("Message insert was ignored without an existing row")
        await self._request(
            "POST",
            "messages",
            json_body=row,
            prefer="return=minimal",
        )
        return message

    async def update_message_run_id(
        self, message_id: UUID, run_id: UUID, user_id: str
    ) -> None:
        await self._request(
            "PATCH",
            "messages",
            params={"id": f"eq.{message_id}", "user_id": f"eq.{user_id}"},
            json_body={"run_id": str(run_id)},
            prefer="return=minimal",
        )

    async def list_messages(self, conversation_id: UUID, user_id: str) -> list[ChatMessage]:
        rows = await self._request(
            "GET",
            "messages",
            params={
                "select": "id,conversation_id,run_id,role,kind,text,payload,created_at",
                "conversation_id": f"eq.{conversation_id}",
                "user_id": f"eq.{user_id}",
                "order": "created_at.asc",
            },
        )
        return [ChatMessage.model_validate(row) for row in rows]

    async def save_run(self, run: RunState) -> None:
        row = {
            "id": str(run.id),
            "conversation_id": str(run.conversation_id),
            "user_id": run.user_id,
            "status": run.status.value,
            "harness_version": run.harness_version,
            "phase": run.phase.value,
            "state": run.model_dump(mode="json"),
            "updated_at": datetime.now().isoformat(),
        }
        await self._request(
            "POST",
            "runs",
            json_body=row,
            prefer="return=minimal,resolution=merge-duplicates",
        )

    async def get_run(self, run_id: UUID, user_id: str) -> RunState | None:
        rows = await self._request(
            "GET",
            "runs",
            params={
                "select": "state",
                "id": f"eq.{run_id}",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        )
        return RunState.model_validate(rows[0]["state"]) if rows else None

    async def get_active_run(self, conversation_id: UUID, user_id: str) -> RunState | None:
        rows = await self._request(
            "GET",
            "runs",
            params={
                "select": "state",
                "conversation_id": f"eq.{conversation_id}",
                "user_id": f"eq.{user_id}",
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
        return RunState.model_validate(rows[0]["state"]) if rows else None

    async def list_recoverable_runs(self) -> list[RunState]:
        rows = await self._request(
            "GET",
            "runs",
            params={
                "select": "state",
                "status": "in.(planned,running,replanning)",
                "order": "updated_at.asc",
            },
        )
        return [RunState.model_validate(row["state"]) for row in rows]

    async def add_event(self, event: AgentEvent) -> AgentEvent:
        row = event.model_dump(mode="json", exclude={"id"})
        inserted = await self._request(
            "POST",
            "agent_events",
            json_body=row,
            prefer="return=representation",
        )
        if not inserted:
            raise StoreError("Agent event insert returned no row")
        return AgentEvent.model_validate(inserted[0])

    async def list_events(
        self,
        run_id: UUID,
        user_id: str,
        *,
        after_id: int = 0,
        limit: int = 200,
    ) -> list[AgentEvent]:
        rows = await self._request(
            "GET",
            "agent_events",
            params={
                "select": "*",
                "run_id": f"eq.{run_id}",
                "user_id": f"eq.{user_id}",
                "id": f"gt.{after_id}",
                "order": "id.asc",
                "limit": str(min(max(limit, 1), 500)),
            },
        )
        return [AgentEvent.model_validate(row) for row in rows]

    async def add_model_call(self, call: ModelCallRecord) -> None:
        await self._request(
            "POST",
            "model_calls",
            json_body=call.model_dump(mode="json"),
            prefer="return=minimal",
        )

    async def list_model_calls(
        self,
        run_id: UUID,
        user_id: str,
    ) -> list[ModelCallRecord]:
        rows = await self._request(
            "GET",
            "model_calls",
            params={
                "select": "*",
                "run_id": f"eq.{run_id}",
                "user_id": f"eq.{user_id}",
                "order": "created_at.asc",
            },
        )
        return [ModelCallRecord.model_validate(row) for row in rows]

    async def save_task(self, run: RunState, task: TaskNode) -> None:
        await self._request(
            "POST",
            "run_tasks",
            json_body={
                "run_id": str(run.id),
                "task_id": task.id,
                "user_id": run.user_id,
                "status": task.status.value,
                "payload": task.model_dump(mode="json"),
                "updated_at": datetime.now().isoformat(),
            },
            prefer="return=minimal,resolution=merge-duplicates",
        )

    async def claim_run(self, run_id: UUID, worker_id: str) -> bool:
        result = await self._request(
            "POST",
            "rpc/claim_agent_run",
            json_body={
                "p_run_id": str(run_id),
                "p_worker_id": worker_id,
                "p_lease_seconds": 120,
            },
        )
        return bool(result)

    async def release_run(self, run_id: UUID, worker_id: str) -> None:
        await self._request(
            "POST",
            "rpc/release_agent_run",
            json_body={
                "p_run_id": str(run_id),
                "p_worker_id": worker_id,
            },
        )

    async def save_calendar_connection(self, user_id: str, payload: dict[str, Any]) -> None:
        await self._request(
            "POST",
            "oauth_tokens",
            json_body={
                "user_id": user_id,
                "provider": "google_calendar",
                "encrypted_payload": payload,
                "updated_at": datetime.now().isoformat(),
            },
            prefer="return=minimal,resolution=merge-duplicates",
        )

    async def get_calendar_connection(self, user_id: str) -> dict[str, Any] | None:
        rows = await self._request(
            "GET",
            "oauth_tokens",
            params={
                "select": "encrypted_payload",
                "user_id": f"eq.{user_id}",
                "provider": "eq.google_calendar",
                "limit": "1",
            },
        )
        return rows[0]["encrypted_payload"] if rows else None

    async def delete_calendar_connection(self, user_id: str) -> None:
        await self._request(
            "DELETE",
            "oauth_tokens",
            params={"user_id": f"eq.{user_id}", "provider": "eq.google_calendar"},
            prefer="return=minimal",
        )

    async def get_user_preferences(self, user_id: str) -> UserPreferences | None:
        rows = await self._request(
            "GET",
            "user_preferences",
            params={"select": "*", "user_id": f"eq.{user_id}", "limit": "1"},
        )
        return UserPreferences.model_validate(rows[0]) if rows else None

    async def save_user_preferences(self, preferences: UserPreferences) -> None:
        await self._request(
            "POST",
            "user_preferences",
            json_body=preferences.model_dump(mode="json"),
            prefer="return=minimal,resolution=merge-duplicates",
        )


class TokenCipher:
    def __init__(self, key: str | None) -> None:
        if key:
            self.fernet = Fernet(key.encode())
        else:
            self.fernet = Fernet(Fernet.generate_key())

    def encrypt(self, payload: dict[str, Any]) -> dict[str, str]:
        token = self.fernet.encrypt(json.dumps(payload).encode()).decode()
        return {"ciphertext": token}

    def decrypt(self, payload: dict[str, Any]) -> dict[str, Any]:
        ciphertext = str(payload["ciphertext"]).encode()
        return json.loads(self.fernet.decrypt(ciphertext))


def create_store(settings: Settings) -> Store:
    if settings.has_supabase:
        return SupabaseStore(settings.supabase_url or "", settings.supabase_secret_key or "")
    return SQLiteStore(str(settings.database_path))
