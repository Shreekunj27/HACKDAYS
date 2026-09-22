from __future__ import annotations

import json
import os
import sqlite3


class InMemoryStore:
    def __init__(self) -> None:
        self.workflows: dict[str, dict] = {}
        self.decisions: dict[str, dict] = {}
        self.runs: dict[str, list[dict]] = {}
        self.events: dict[str, list[dict]] = {}
        self.carbon_snapshot: dict | None = None

    def save_workflow(self, workflow: dict) -> dict:
        self.workflows[workflow["workflow_id"]] = workflow
        return workflow

    def get_workflow(self, workflow_id: str) -> dict | None:
        return self.workflows.get(workflow_id)

    def save_decision(self, workflow_id: str, step_id: str, decision: dict) -> dict:
        key = f"{workflow_id}:{step_id}"
        self.decisions[key] = decision
        return decision

    def get_decision(self, workflow_id: str, step_id: str) -> dict | None:
        return self.decisions.get(f"{workflow_id}:{step_id}")

    def get_decisions(self, workflow_id: str) -> dict[str, dict]:
        prefix = f"{workflow_id}:"
        return {
            key.removeprefix(prefix): decision
            for key, decision in self.decisions.items()
            if key.startswith(prefix)
        }

    def append_run(self, workflow_id: str, run: dict) -> dict:
        self.runs.setdefault(workflow_id, []).append(run)
        return run

    def get_runs(self, workflow_id: str) -> list[dict]:
        return self.runs.get(workflow_id, [])

    def append_event(self, workflow_id: str, event: dict) -> dict:
        self.events.setdefault(workflow_id, []).append(event)
        return event

    def get_events(self, workflow_id: str) -> list[dict]:
        return self.events.get(workflow_id, [])


class SQLiteStore(InMemoryStore):
    def __init__(self, db_path: str | None = None) -> None:
        super().__init__()
        self.db_path = db_path or os.getenv("GREENPILOT_DB_PATH", "greenpilot.sqlite3")
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._load_snapshot()

    def _load_snapshot(self) -> None:
        for key, value in self._conn.execute("SELECT key, value FROM state"):
            if key == "workflows":
                self.workflows = json.loads(value)
            elif key == "decisions":
                self.decisions = json.loads(value)
            elif key == "runs":
                self.runs = json.loads(value)
            elif key == "events":
                self.events = json.loads(value)
            elif key == "carbon_snapshot":
                self.carbon_snapshot = json.loads(value)

    def _persist(self) -> None:
        payload = {
            "workflows": json.dumps(self.workflows),
            "decisions": json.dumps(self.decisions),
            "runs": json.dumps(self.runs),
            "events": json.dumps(self.events),
            "carbon_snapshot": json.dumps(self.carbon_snapshot),
        }
        for key, value in payload.items():
            self._conn.execute(
                "INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        self._conn.commit()

    def save_workflow(self, workflow: dict) -> dict:
        saved = super().save_workflow(workflow)
        self._persist()
        return saved

    def save_decision(self, workflow_id: str, step_id: str, decision: dict) -> dict:
        saved = super().save_decision(workflow_id, step_id, decision)
        self._persist()
        return saved

    def append_run(self, workflow_id: str, run: dict) -> dict:
        saved = super().append_run(workflow_id, run)
        self._persist()
        return saved

    def append_event(self, workflow_id: str, event: dict) -> dict:
        saved = super().append_event(workflow_id, event)
        self._persist()
        return saved


def _select_store() -> InMemoryStore:
    backend = os.getenv("GREENPILOT_STORAGE_BACKEND", "in_memory").strip().lower()
    return SQLiteStore() if backend == "sqlite" else InMemoryStore()


store = _select_store()
