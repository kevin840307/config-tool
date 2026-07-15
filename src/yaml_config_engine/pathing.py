from __future__ import annotations
import re
from typing import Any

UnionToken = tuple[str, ...]
from .errors import PathError

_TOKEN_RE = re.compile(r"(?:^|\.)([^.\[\]]+)|\[(\-?\d+|\*)\]")


def parse_path(path: str) -> list[str | int | UnionToken]:
    if path in ("$", "", None):
        return []
    if path.startswith("$/"):
        parts = path[2:].split("/")
        return [_parse_pointer_part(p) for p in parts]
    if path.startswith("/"):
        parts = path[1:].split("/")
        return [_parse_pointer_part(p) for p in parts]
    s = path[2:] if path.startswith("$.") else path
    tokens: list[str | int] = []
    for m in _TOKEN_RE.finditer(s):
        key, idx = m.groups()
        if key is not None:
            tokens.append(key)
        elif idx == '*':
            tokens.append('*')
        else:
            tokens.append(int(idx))
    if not tokens and s:
        tokens = [s]
    return tokens


def _parse_pointer_part(part: str) -> str | int | UnionToken:
    decoded = part.replace("~1", "/").replace("~0", "~")
    if decoded == '[*]':
        return '*'
    if decoded.startswith("[") and decoded.endswith("]") and "," in decoded:
        values = tuple(x.strip() for x in decoded[1:-1].split(",") if x.strip())
        if len(values) >= 2:
            return values
    return int(decoded) if decoded.lstrip("-").isdigit() else decoded

def path_has_selectors(path: str) -> bool:
    return any(token == '*' or isinstance(token, tuple) for token in parse_path(path))


def _pointer(tokens: list[str | int]) -> str:
    if not tokens:
        return '$'
    encoded = [('[' + ','.join(x) + ']') if isinstance(x, tuple) else str(x).replace('~', '~0').replace('/', '~1') for x in tokens]
    return '$/' + '/'.join(encoded)


def expand_paths(root: Any, path: str) -> list[str]:
    """Expand mapping/list wildcards into concrete JSON-pointer style paths.

    `*` and `[*]` both mean every direct mapping value or list item.
    Numeric YAML indices are zero-based, including negative indices.
    """
    tokens = parse_path(path)
    if not any(token == '*' or isinstance(token, tuple) for token in tokens):
        return [path]
    states: list[tuple[Any, list[str | int]]] = [(root, [])]
    for pos, token in enumerate(tokens):
        next_states: list[tuple[Any, list[str | int]]] = []
        remaining = tokens[pos + 1:]
        for node, concrete in states:
            if isinstance(token, tuple):
                if isinstance(node, dict):
                    for key in token:
                        if key in node:
                            next_states.append((node[key], [*concrete, key]))
                continue
            if token == '*':
                if isinstance(node, dict):
                    for key, value in node.items():
                        next_states.append((value, [*concrete, str(key)]))
                elif isinstance(node, list):
                    for idx, value in enumerate(node):
                        next_states.append((value, [*concrete, idx]))
                continue
            try:
                actual = token
                if isinstance(token, int) and isinstance(node, list) and token < 0:
                    actual = len(node) + token
                next_states.append((node[actual], [*concrete, actual]))
            except (KeyError, IndexError, TypeError):
                # Once all wildcard segments have already been resolved, keep
                # an exact missing suffix so missing:create can create it for
                # every concrete wildcard parent.
                if not any(x == '*' or isinstance(x, tuple) for x in remaining):
                    suffix = [token, *remaining]
                    next_states.append((None, [*concrete, *suffix]))
                continue
        states = next_states
        if not states:
            break
        # Missing suffix was appended in one step.
        if states and pos < len(tokens) - 1 and all(node is None for node, _ in states):
            break
    return [_pointer(concrete) for _, concrete in states]


def get_node(root: Any, path: str) -> Any:
    cur = root
    for token in parse_path(path):
        if token == '*' or isinstance(token, tuple):
            raise PathError(f"Wildcard path must be expanded before direct access: {path!r}")
        try:
            cur = cur[token]
        except (KeyError, IndexError, TypeError) as e:
            raise PathError(f"Path not found: {path!r} at token {token!r}") from e
    return cur


def get_parent(root: Any, path: str) -> tuple[Any, str | int]:
    tokens = parse_path(path)
    if not tokens:
        raise PathError("Document root has no parent")
    cur = root
    for token in tokens[:-1]:
        if token == '*' or isinstance(token, tuple):
            raise PathError(f"Wildcard path must be expanded before direct access: {path!r}")
        try:
            cur = cur[token]
        except (KeyError, IndexError, TypeError) as e:
            raise PathError(f"Parent path not found for {path!r}") from e
    return cur, tokens[-1]



def _adjust_inline_comment_column(parent: Any, key: Any, old_value: Any, new_value: Any) -> None:
    """Keep ruamel inline comments aligned when a scalar width changes.

    Direct assignment through CommentedMap normally updates this presentation
    metadata. Our generic path setter bypasses some of that behavior, so copied
    items could lose one or more spaces before an inline comment.
    """
    if not isinstance(parent, dict) or isinstance(old_value, (dict, list)) or isinstance(new_value, (dict, list)):
        return
    ca = getattr(parent, 'ca', None)
    slots = getattr(ca, 'items', {}).get(key) if ca is not None else None
    if not slots or len(slots) <= 2 or slots[2] is None:
        return
    token = slots[2]
    text = getattr(token, 'value', '')
    if text.startswith(('\n', '\r')):
        return
    column = getattr(token, 'column', None)
    if column is None:
        return
    try:
        token.column = max(0, int(column) + len(str(new_value)) - len(str(old_value)))
    except Exception:
        pass

def set_node(root: Any, path: str, value: Any, create_missing: bool = False) -> Any:
    tokens = parse_path(path)
    if not tokens:
        return value
    cur = root
    for token in tokens[:-1]:
        if token == '*' or isinstance(token, tuple):
            raise PathError(f"Wildcard path must be expanded before direct access: {path!r}")
        if isinstance(token, int):
            cur = cur[token]
        else:
            if token not in cur:
                if not create_missing:
                    raise PathError(f"Path not found: {path!r}")
                cur[token] = {}
            cur = cur[token]
    final_key = tokens[-1]
    old_value = cur.get(final_key) if isinstance(cur, dict) and final_key in cur else (cur[final_key] if isinstance(cur, list) and isinstance(final_key, int) and -len(cur) <= final_key < len(cur) else None)
    _adjust_inline_comment_column(cur, final_key, old_value, value)
    cur[final_key] = value
    return root


def remove_node(root: Any, path: str) -> Any:
    parent, key = get_parent(root, path)
    del parent[key]
    return root
