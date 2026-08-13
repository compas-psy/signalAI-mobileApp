from __future__ import annotations

from uuid import UUID, uuid4


def resolve_request_id(value: str | None) -> str:
    supplied = (value or "").strip()
    if supplied:
        try:
            UUID(supplied)
            return supplied
        except ValueError:
            pass
    return str(uuid4())
