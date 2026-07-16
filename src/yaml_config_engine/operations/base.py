from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable
from ..errors import PathError, MatchError, OperationError

@dataclass
class OperationContext:
    document: Any
    original: Any
    variables: dict[str, Any]
    captures: dict[str, Any]
    skipped_operations: list[dict[str, Any]] | None = None

OperationFn = Callable[[OperationContext, dict[str, Any]], Any]

class OperationRegistry:
    def __init__(self) -> None:
        self._ops: dict[str, OperationFn] = {}
    def register(self, *names: str):
        def deco(fn: OperationFn):
            for name in names:
                self._ops[name] = fn
            return fn
        return deco
    def execute(self, name: str, ctx: OperationContext, spec: dict[str, Any]) -> Any:
        if name not in self._ops:
            raise ValueError(f"Unsupported operation: {name}")
        try:
            return self._ops[name](ctx, spec)
        except (PathError, MatchError, OperationError) as exc:
            # `missing: skip` applies to the whole selector chain, not only the
            # final key. This makes all operations consistently no-op when an
            # intermediate parent, source selector, list item, or position
            # target is absent. Configuration/schema errors still fail.
            policy = str(spec.get("missing", "skip")).lower()
            legacy_skip = str(spec.get("on_zero_matches", "")).lower() in {"skip", "ignore"}
            if policy == "skip" or legacy_skip:
                message = str(exc).lower()
                missing_markers = (
                    "path not found", "parent path not found", "missing key",
                    "no mapping key matched", "no match", "not found",
                    "expected exactly one", "expected 1 matches", "got 0",
                    "requires exactly one", "position target",
                )
                if isinstance(exc, (PathError, MatchError)) or any(m in message for m in missing_markers):
                    if ctx.skipped_operations is not None:
                        ctx.skipped_operations.append({"id": spec.get("id"), "op": name, "path": spec.get("path"), "reason": str(exc)})
                    return ctx.document
            raise

registry = OperationRegistry()
