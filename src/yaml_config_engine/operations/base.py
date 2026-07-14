from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable

@dataclass
class OperationContext:
    document: Any
    original: Any
    variables: dict[str, Any]
    captures: dict[str, Any]

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
        return self._ops[name](ctx, spec)

registry = OperationRegistry()
