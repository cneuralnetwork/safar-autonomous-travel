"""Small JSONL helpers used throughout the reproducible pipeline."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def write_jsonl(path: Path, records: Iterable[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(record.model_dump_json() + "\n")
    temporary.replace(path)


def read_jsonl(path: Path, model: type[T]) -> list[T]:
    if not path.exists():
        raise FileNotFoundError(f"No records at {path}. Run the indexer workflow first.")
    records: list[T] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(model.model_validate_json(line))
            except Exception as exc:  # pragma: no cover - error path is operational context
                raise ValueError(f"Invalid JSONL record at {path}:{line_number}") from exc
    return records


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
