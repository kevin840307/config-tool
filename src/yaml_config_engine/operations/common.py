from __future__ import annotations
from copy import deepcopy
from typing import Any
import fnmatch
import re
from .base import registry, OperationContext
from ..pathing import get_node, set_node, remove_node, get_parent
from ..matcher import find_indices
from ..errors import MatchError, OperationError
from ..comparison import strict_equal



def _missing_policy(spec: dict[str, Any], *, allow_create: bool = True) -> str:
    explicit = spec.get("missing")
    legacy_present = "create_missing" in spec
    legacy = bool(spec.get("create_missing", False))
    if explicit is None:
        zero_policy = str(spec.get("on_zero_matches", "")).lower()
        if zero_policy in {"ignore", "skip"}:
            policy = "skip"
        else:
            policy = "create" if legacy else "skip"
    else:
        policy = str(explicit).lower()
        if policy not in {"error", "skip", "create"}:
            raise OperationError("missing must be error, skip, or create")
        if legacy_present and ((legacy and policy != "create") or (not legacy and policy == "create")):
            raise OperationError("Conflicting missing and create_missing settings")
    if policy == "create" and not allow_create:
        raise OperationError("missing: create is not supported for pattern selectors")
    return policy


def _pattern_matches(value: str, pattern: str, mode: str = "glob") -> bool:
    if mode == "regex":
        return re.search(pattern, value) is not None
    if mode == "iregex":
        return re.search(pattern, value, re.I) is not None
    if mode == "iglob":
        return fnmatch.fnmatch(value.lower(), pattern.lower())
    return fnmatch.fnmatchcase(value, pattern)


def _selected_mapping_keys(parent: Any, spec: dict[str, Any]) -> tuple[list[str], str | None]:
    exact = spec.get("key", spec.get("name"))
    pattern = spec.get("key_pattern", spec.get("name_pattern"))
    if exact is None and pattern is None:
        return [], None
    if not isinstance(parent, dict):
        raise OperationError("key/name selector requires a mapping at path")
    if exact is not None:
        return ([str(exact)] if str(exact) in parent else []), str(exact)
    mode = str(spec.get("pattern_type", "glob")).lower()
    return [str(k) for k in parent.keys() if _pattern_matches(str(k), str(pattern), mode)], None


def _normalize_name_pattern_match(spec: dict[str, Any]) -> dict[str, Any]:
    match = deepcopy(spec.get("match", {}))
    if "name" in spec and "name" not in match:
        match["name"] = spec["name"]
    if "name_pattern" in spec and "name" not in match:
        mode = str(spec.get("pattern_type", "glob")).lower()
        op = {"glob": "$glob", "iglob": "$iglob", "regex": "$regex", "iregex": "$iregex"}.get(mode, "$glob")
        match["name"] = {op: spec["name_pattern"]}
    return match

def _require_matches(indices: list[int], spec: dict[str, Any], label: str = "match") -> None:
    expected = spec.get("expect_matches")
    if expected is None:
        expected = spec.get("expect", {}).get("matches") if isinstance(spec.get("expect"), dict) else None
    # expect_matches: -1 explicitly means "accept every current match".
    # It is useful for update_item without a match selector and also suppresses
    # the normal multiple-match safety error.
    all_matches = expected == -1
    if expected is not None and not all_matches and len(indices) != expected:
        raise MatchError(f"{label}: expected {expected} matches, got {len(indices)}")
    if not all_matches and spec.get("on_multiple_matches", "error") == "error" and len(indices) > 1:
        raise MatchError(f"{label}: multiple matches ({len(indices)})")


def _resolve_position(seq: list[Any], position: dict[str, Any] | None, default: int | None = None) -> int:
    if not position:
        return len(seq) if default is None else default
    if position.get("first") is True: return 0
    if position.get("last") is True: return len(seq)
    if "index" in position:
        idx = position["index"]
        if idx == "append": return len(seq)
        if not isinstance(idx, int): raise OperationError("position.index must be integer or append")
        if idx < 0: idx = max(0, len(seq) + idx)
        if idx > len(seq):
            policy = position.get("on_out_of_range", "error")
            if policy == "append": return len(seq)
            if policy == "clamp": return len(seq)
            raise OperationError(f"insert index out of range: {idx}")
        return idx
    for side in ("before", "after"):
        if side in position:
            target = position[side]
            if "index" in target:
                idx = target["index"]
            else:
                indices = find_indices(seq, target.get("match", {}))
                _require_matches(indices, target, "position")
                if not indices: raise MatchError("position target not found")
                idx = indices[0]
            return idx if side == "before" else idx + 1
    raise OperationError(f"Unsupported position: {position}")


def _merge(dst: Any, src: Any, strategy: str = "overwrite") -> Any:
    if isinstance(dst, dict) and isinstance(src, dict):
        for k, v in src.items():
            if v is None and strategy == "delete_null":
                dst.pop(k, None); continue
            if k in dst:
                if strategy == "keep_existing":
                    continue
                dst[k] = _merge(dst[k], v, strategy)
            else:
                dst[k] = deepcopy(v)
        return dst
    if isinstance(dst, list) and isinstance(src, list):
        if strategy == "append": dst.extend(deepcopy(src)); return dst
        if strategy == "prepend": dst[0:0] = deepcopy(src); return dst
        if strategy == "unique":
            for item in src:
                if item not in dst: dst.append(deepcopy(item))
            return dst
    return deepcopy(src)



def _replace_string_value(current: Any, spec: dict[str, Any]) -> Any:
    if not isinstance(current, str):
        raise OperationError("replace_value target must be a string scalar")
    search = spec.get("search", spec.get("old"))
    if search is None:
        raise OperationError("replace_value requires search (or old)")
    replacement = spec.get("replacement", spec.get("new", ""))
    mode = str(spec.get("pattern_type", spec.get("mode", "literal"))).lower()
    count = int(spec.get("count", 0))
    if count < 0:
        raise OperationError("replace_value count must be >= 0")
    if mode in {"regex", "iregex"}:
        flags = re.I if mode == "iregex" else 0
        updated, matched = re.subn(str(search), str(replacement), current, count=count, flags=flags)
    elif mode in {"literal", "text"}:
        matched = current.count(str(search))
        effective = matched if count == 0 else min(matched, count)
        updated = current.replace(str(search), str(replacement), count if count else -1)
        matched = effective
    else:
        raise OperationError("replace_value pattern_type must be literal, regex, or iregex")
    if matched == 0:
        policy = str(spec.get("on_no_match", spec.get("missing_text", "error"))).lower()
        if policy in {"skip", "ignore"}:
            return current
        raise OperationError(f"replace_value search text not found: {search!r}")
    expected = spec.get("expect_replacements")
    if expected is not None and matched != int(expected):
        raise OperationError(f"replace_value expected {expected} replacements, got {matched}")
    # Preserve ruamel scalar subclasses such as quoted strings when possible.
    if type(current) is not str:
        try:
            return type(current)(updated)
        except Exception:
            pass
    return updated

@registry.register("replace_value", "replace_text_value")
def op_replace_value(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    path = spec.get("path", "$")
    policy = _missing_policy(spec, allow_create=False)
    try:
        current = get_node(ctx.document, path)
    except Exception:
        if policy == "skip":
            return ctx.document
        raise OperationError(f"Path not found: {path!r}")
    updated = _replace_string_value(current, spec)
    if updated is current or updated == current:
        return ctx.document
    ctx.document = set_node(ctx.document, path, updated, False)
    return ctx.document

@registry.register("set", "replace")
def op_set(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    path = spec.get("path", "$")
    if any(k in spec for k in ("key", "key_pattern", "name", "name_pattern")):
        parent = get_node(ctx.document, path)
        keys, exact = _selected_mapping_keys(parent, spec)
        policy = _missing_policy(spec, allow_create=exact is not None)
        if not keys:
            if policy == "skip": return ctx.document
            if policy == "create" and exact is not None:
                _mapping_insert(
                    parent,
                    exact,
                    deepcopy(spec["value"]),
                    spec.get("position") or {"last": True},
                )
                return ctx.document
            raise OperationError(f"No mapping key matched selector at {path}")
        if len(keys) > 1 and spec.get("on_multiple_matches", "all") == "error":
            raise MatchError(f"key selector matched multiple keys ({len(keys)})")
        for key in keys: parent[key] = deepcopy(spec["value"])
        return ctx.document
    policy = _missing_policy(spec)
    try:
        get_node(ctx.document, path)
        exists = True
    except Exception:
        exists = False
    if not exists and policy == "skip": return ctx.document
    if not exists and policy == "error": raise OperationError(f"Path not found: {path!r}")
    if not exists and policy == "create":
        # When only the final mapping key is missing, insert it through the
        # comment-aware mapping helper. This keeps comments that visually sit
        # between the previous section and the newly-created section attached
        # to the correct boundary instead of duplicating or stranding them.
        try:
            parent, key = get_parent(ctx.document, path)
            if isinstance(parent, dict) and isinstance(key, str):
                _mapping_insert(
                    parent,
                    key,
                    deepcopy(spec["value"]),
                    spec.get("position") or {"last": True},
                )
                return ctx.document
        except Exception:
            # A deeper parent is missing; retain the existing recursive-create
            # behavior for that case.
            pass
    ctx.document = set_node(ctx.document, path, deepcopy(spec["value"]), policy == "create")
    return ctx.document

@registry.register("remove")
def op_remove(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    path = spec.get("path", "$")
    if any(k in spec for k in ("key", "key_pattern", "name", "name_pattern")):
        parent = get_node(ctx.document, path)
        keys, exact = _selected_mapping_keys(parent, spec)
        policy = _missing_policy(spec, allow_create=False)
        if not keys:
            if policy == "skip": return ctx.document
            raise OperationError(f"No mapping key matched selector at {path}")
        for key in keys: del parent[key]
        return ctx.document
    policy = _missing_policy(spec, allow_create=False)
    try:
        ctx.document = remove_node(ctx.document, path)
    except Exception:
        if policy == "skip": return ctx.document
        raise
    return ctx.document

@registry.register("merge")
def op_merge(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    path = spec.get("path", "$")
    if any(k in spec for k in ("key", "key_pattern", "name", "name_pattern")):
        parent = get_node(ctx.document, path)
        keys, exact = _selected_mapping_keys(parent, spec)
        policy = _missing_policy(spec, allow_create=exact is not None)
        if not keys:
            if policy == "skip": return ctx.document
            if policy == "create" and exact is not None:
                parent[exact] = deepcopy(spec["value"]); return ctx.document
            raise OperationError(f"No mapping key matched selector at {path}")
        for key in keys: _merge(parent[key], spec["value"], spec.get("strategy", "overwrite"))
        return ctx.document
    policy = _missing_policy(spec)
    try:
        node = get_node(ctx.document, path)
    except Exception:
        if policy == "skip": return ctx.document
        if policy == "create":
            ctx.document = set_node(ctx.document, path, deepcopy(spec["value"]), True); return ctx.document
        raise
    _merge(node, spec["value"], spec.get("strategy", "overwrite"))
    return ctx.document

@registry.register("rename_key")
def op_rename_key(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    parent = get_node(ctx.document, spec.get("path", "$"))
    old, new = spec["old_key"], spec["new_key"]
    keys = list(parent.keys())
    if old not in parent:
        policy = _missing_policy(spec, allow_create=False)
        if policy == "skip" and new in parent:
            return ctx.document
        if policy == "skip":
            return ctx.document
        raise OperationError(f"Missing key: {old}")
    idx = keys.index(old)
    value = parent.pop(old)
    if hasattr(parent, "insert"):
        parent.insert(idx, new, value)
    else:
        items = list(parent.items())
        items.insert(idx, (new, value))
        parent.clear(); parent.update(items)
    return ctx.document

@registry.register("insert_key")
def op_insert_key(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    parent = get_node(ctx.document, spec.get("path", "$"))
    position = spec.get("position", {})
    keys = list(parent.keys())
    if "before_key" in position:
        idx = keys.index(position["before_key"])
    elif "after_key" in position:
        idx = keys.index(position["after_key"]) + 1
    else:
        idx = position.get("index", len(keys))
    key, value = spec["key"], deepcopy(spec.get("value"))
    if hasattr(parent, "insert"):
        parent.insert(idx, key, value)
    else:
        items = list(parent.items())
        items.insert(idx, (key, value))
        parent.clear(); parent.update(items)
    return ctx.document


@registry.register("copy_key", "move_key")
def op_copy_move_key(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    parent = get_node(ctx.document, spec.get("path", "$"))
    source_key = spec["source_key"]
    target_key = spec.get("target_key", source_key)
    if source_key not in parent: raise OperationError(f"Missing key: {source_key}")
    value = deepcopy(parent[source_key])
    if spec["op"] == "move_key":
        del parent[source_key]
    keys = list(parent.keys())
    position = spec.get("position", {})
    if "before_key" in position:
        idx = keys.index(position["before_key"])
    elif "after_key" in position:
        idx = keys.index(position["after_key"]) + 1
    else:
        idx = position.get("index", len(keys))
    if target_key in parent:
        policy = spec.get("on_conflict", "error")
        if policy == "error": raise OperationError(f"Target key already exists: {target_key}")
        del parent[target_key]
        keys = list(parent.keys())
        idx = min(idx, len(keys))
    if hasattr(parent, "insert"):
        parent.insert(idx, target_key, value)
    else:
        items = list(parent.items())
        items.insert(idx, (target_key, value))
        parent.clear(); parent.update(items)
    return ctx.document

@registry.register("copy_node", "move_node")
def op_copy_move_node(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    source_path = spec.get("from_path") or spec.get("source")
    target_path = spec.get("to_path") or spec.get("path")
    if not isinstance(source_path, str) or not isinstance(target_path, str):
        raise OperationError("copy_node/move_node require from_path and to_path")
    value = deepcopy(get_node(ctx.document, source_path))
    ctx.document = set_node(ctx.document, target_path, value, spec.get("create_missing", True))
    if spec["op"] == "move_node":
        ctx.document = remove_node(ctx.document, source_path)
    return ctx.document

@registry.register("append", "prepend", "insert", "insert_at", "insert_before", "insert_after")
def op_insert(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    path = spec["path"]
    policy = _missing_policy(spec)
    try:
        seq = get_node(ctx.document, path)
    except Exception:
        if policy == "skip": return ctx.document
        if policy == "create":
            ctx.document = set_node(ctx.document, path, [], True)
            seq = get_node(ctx.document, path)
        else:
            raise OperationError(f"Path not found: {path!r}")
    if not isinstance(seq, list):
        raise OperationError(f"Insert operation path must select a list: {path!r}")
    values = deepcopy(spec.get("values", [spec.get("value")]))
    op = spec["op"]
    position = spec.get("position")
    if op == "append": position = {"last": True}
    elif op == "prepend": position = {"first": True}
    elif op == "insert_at": position = {"index": spec["index"]}
    elif op in ("insert_before", "insert_after"):
        position = {op.split("_")[1]: {"match": spec["match"], **({"expect_matches": spec["expect_matches"]} if "expect_matches" in spec else {})}}
    idx = _resolve_position(seq, position)
    unique_by = spec.get("duplicate", {}).get("unique_by", spec.get("unique_by", []))
    policy = spec.get("duplicate", {}).get("policy", spec.get("duplicate_policy", "allow"))
    for value in values:
        if unique_by:
            duplicate = next((x for x in seq if isinstance(x, dict) and all(x.get(k) == value.get(k) for k in unique_by)), None)
            if duplicate is not None:
                if policy == "skip": continue
                if policy == "skip_if_equal":
                    if strict_equal(duplicate, value):
                        continue
                    raise OperationError(f"Duplicate item differs by {unique_by}")
                if policy == "update": duplicate.update(value); continue
                if policy == "error": raise OperationError(f"Duplicate item by {unique_by}")
        seq.insert(idx, value); idx += 1
    return ctx.document

@registry.register("update_item", "upsert_item")
def op_update_item(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    path = spec["path"]
    policy = _missing_policy(spec, allow_create=spec["op"] == "upsert_item")
    try:
        seq = get_node(ctx.document, path)
    except Exception:
        if policy == "skip": return ctx.document
        if policy == "create" and spec["op"] == "upsert_item":
            ctx.document = set_node(ctx.document, path, [], True)
            seq = get_node(ctx.document, path)
        else:
            raise OperationError(f"Path not found: {path!r}")
    if not isinstance(seq, list):
        raise OperationError(f"{spec['op']} path must select a list: {path!r}")
    match_spec = _normalize_name_pattern_match(spec)
    indices = find_indices(seq, match_spec)
    if spec["op"] == "upsert_item" and not indices:
        value = deepcopy(spec.get("value", spec.get("set", {})))
        seq.insert(_resolve_position(seq, spec.get("position")), value)
        return ctx.document
    _require_matches(indices, spec)
    if not indices:
        policy = _missing_policy(spec, allow_create=spec["op"] == "upsert_item")
        if policy == "skip": return ctx.document
        if policy == "create" and spec["op"] == "upsert_item":
            value = deepcopy(spec.get("value", spec.get("set", {}))); seq.insert(_resolve_position(seq, spec.get("position")), value); return ctx.document
        raise MatchError("No matching item")
    for idx in indices:
        item = seq[idx]
        for path, value in spec.get("set", {}).items():
            target_path = path if ("." in path or "[" in path or str(path).startswith(("$", "/"))) else "$/{0}".format(str(path).replace("~", "~0").replace("/", "~1"))
            set_node(item, target_path, deepcopy(value), True)
        for path in spec.get("remove", []):
            remove_node(item, path)
        if isinstance(spec.get('merge'), dict):
            _merge(item, deepcopy(spec['merge']), spec.get('merge_strategy', 'overwrite'))
        # Run nested operations against the matched item itself. This lets the
        # compiler update deep mappings/lists without replacing the whole
        # subtree, preserving comments, anchors, quoting, and unaffected style.
        if spec.get("item_operations"):
            nested = OperationContext(document=item, original=deepcopy(item), variables=ctx.variables, captures=ctx.captures, skipped_operations=ctx.skipped_operations)
            for nested_spec in spec["item_operations"]:
                if not isinstance(nested_spec, dict) or "op" not in nested_spec:
                    raise OperationError("update_item.item_operations entries require op")
                registry.execute(nested_spec["op"], nested, nested_spec)
            seq[idx] = nested.document
    return ctx.document

def _preserve_removed_item_trailing_comments(seq: Any, indices: list[int]) -> None:
    """Keep comments visually belonging to the next sequence item.

    ruamel may attach a standalone comment before item N to the deepest final
    mapping key of item N-1. Only newline-prefixed standalone comments are
    moved; inline comments remain with the deleted item.
    """
    def collect(node: Any) -> list[str]:
        values: list[str] = []
        ca = getattr(node, 'ca', None)
        for slots in getattr(ca, 'items', {}).values() if ca is not None else []:
            if slots and len(slots) > 2 and slots[2] is not None:
                value = getattr(slots[2], 'value', '')
                if value.startswith(('\n', '\r')) and value.strip():
                    values.append(value.strip())
        if isinstance(node, dict):
            for child in node.values():
                values.extend(collect(child))
        elif isinstance(node, list):
            for child in node:
                values.extend(collect(child))
        return values

    removed = set(indices)
    for idx in indices:
        if idx < 0 or idx >= len(seq):
            continue
        comment_values = collect(seq[idx])
        if not comment_values:
            continue
        next_idx = next((j for j in range(idx + 1, len(seq)) if j not in removed), None)
        if next_idx is None:
            continue
        target = seq[next_idx]
        setter = getattr(target, 'yaml_set_start_comment', None)
        if setter is not None:
            setter('\n'.join(comment_values), indent=2)


@registry.register("remove_item")
def op_remove_item(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    seq = get_node(ctx.document, spec["path"])
    indices = find_indices(seq, _normalize_name_pattern_match(spec))
    _require_matches(indices, spec)
    if not indices:
        policy = _missing_policy(spec, allow_create=False)
        if policy == 'skip': return ctx.document
        raise MatchError('No matching item')
    _preserve_removed_item_trailing_comments(seq, indices)
    if spec.get('remove_leading_comments', False) and 0 in indices:
        ca = getattr(seq, 'ca', None)
        if ca is not None:
            ca.comment = None
        try:
            parent, key = get_parent(ctx.document, spec['path'])
            parent_ca = getattr(parent, 'ca', None)
            slots = getattr(parent_ca, 'items', {}).get(key) if parent_ca is not None else None
            if slots and len(slots) > 3:
                slots[3] = None
        except Exception:
            # Root sequences have no parent key; clearing seq.ca above is enough.
            pass
    for idx in reversed(indices): del seq[idx]
    return ctx.document

@registry.register("move_item")
def op_move_item(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    seq = get_node(ctx.document, spec["path"])
    indices = find_indices(seq, spec.get("match", {}))
    _require_matches(indices, spec)
    if len(indices) != 1: raise MatchError("move_item requires exactly one source")
    item = seq.pop(indices[0])
    seq.insert(_resolve_position(seq, spec.get("position")), item)
    return ctx.document

@registry.register("copy_item")
def op_copy_item(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    seq = get_node(ctx.document, spec["path"])
    source = spec.get("source", {})
    if "index" in source:
        src_idx = source["index"]
    else:
        indices = find_indices(seq, source.get("match", {}))
        if not indices and _missing_policy(spec, allow_create=False) == "skip":
            return ctx.document
        _require_matches(indices, source, "source")
        if len(indices) != 1: raise MatchError("copy_item requires exactly one source")
        src_idx = indices[0]
    item = deepcopy(seq[src_idx])
    if spec.get('copy_leading_comments', True) is False:
        ca = getattr(item, 'ca', None)
        if ca is not None:
            ca.comment = None
    for path, value in spec.get("set", spec.get("overrides", {})).items():
        target_path = path if ("." in path or "[" in path or str(path).startswith(("$", "/"))) else "$/{0}".format(str(path).replace("~", "~0").replace("/", "~1"))
        set_node(item, target_path, deepcopy(value), True)
    for path in spec.get("remove", []): remove_node(item, path)
    if isinstance(spec.get('merge'), dict):
        _merge(item, deepcopy(spec['merge']), spec.get('merge_strategy', 'overwrite'))
    # Apply operations to the copied item before insertion. This supports
    # dynamic-key rename/copy/move without repeating the full source item.
    if spec.get("item_operations"):
        nested = OperationContext(document=item, original=deepcopy(item), variables=ctx.variables, captures=ctx.captures, skipped_operations=ctx.skipped_operations)
        for nested_spec in spec["item_operations"]:
            if not isinstance(nested_spec, dict) or "op" not in nested_spec:
                raise OperationError("copy_item.item_operations entries require op")
            registry.execute(nested_spec["op"], nested, nested_spec)
        item = nested.document
    unique_by = spec.get("duplicate", {}).get("unique_by", [])
    if unique_by:
        dup = any(isinstance(x, dict) and all(x.get(k) == item.get(k) for k in unique_by) for x in seq)
        if dup:
            policy = spec.get("duplicate", {}).get("policy", "error")
            if policy == "skip": return ctx.document
            if policy == "skip_if_equal":
                duplicate_item = next(x for x in seq if isinstance(x, dict) and all(x.get(k) == item.get(k) for k in unique_by))
                if strict_equal(duplicate_item, item):
                    return ctx.document
                raise OperationError(f"Duplicate copied item differs by {unique_by}")
            if policy == "error": raise OperationError(f"Duplicate copied item by {unique_by}")
    seq.insert(_resolve_position(seq, spec.get("position"), src_idx + 1), item)
    return ctx.document

@registry.register("capture")
def op_capture(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    source_root = ctx.original if spec.get("source", "original") == "original" else ctx.document
    node = get_node(source_root, spec.get("path", "$"))
    if "match" in spec:
        indices = find_indices(node, spec["match"])
        _require_matches(indices, spec)
        if len(indices) != 1: raise MatchError("capture requires exactly one match")
        node = node[indices[0]]
    ctx.captures[spec.get("as", spec.get("id"))] = deepcopy(node)
    return ctx.document


def _standalone_comment_tokens_on_last_chain(node: Any) -> list[tuple[list[Any], int, Any]]:
    """Locate newline-prefixed comments visually following a subtree.

    ruamel commonly stores a comment that appears *between* siblings on the
    deepest final key/item of the previous sibling. When a new mapping key is
    inserted between those siblings, that boundary comment must move with the
    boundary or it appears inside the newly extended previous subtree.
    """
    found: list[tuple[list[Any], int, Any]] = []
    if isinstance(node, dict) and node:
        last_key = next(reversed(node))
        found.extend(_standalone_comment_tokens_on_last_chain(node[last_key]))
        ca = getattr(node, 'ca', None)
        slots = getattr(ca, 'items', {}).get(last_key) if ca is not None else None
        if slots:
            for index, token in enumerate(slots):
                text = getattr(token, 'value', '') if token is not None else ''
                if text.startswith(('\n', '\r')) and text.strip():
                    found.append((slots, index, token))
    elif isinstance(node, list) and node:
        last_index = len(node) - 1
        found.extend(_standalone_comment_tokens_on_last_chain(node[last_index]))
        ca = getattr(node, 'ca', None)
        slots = getattr(ca, 'items', {}).get(last_index) if ca is not None else None
        if slots:
            for index, token in enumerate(slots):
                text = getattr(token, 'value', '') if token is not None else ''
                if text.startswith(('\n', '\r')) and text.strip():
                    found.append((slots, index, token))
    return found


def _attach_trailing_comment_token(node: Any, token: Any) -> bool:
    """Attach a boundary comment to the deepest final child of a subtree."""
    if isinstance(node, dict) and node:
        last_key = next(reversed(node))
        if _attach_trailing_comment_token(node[last_key], token):
            return True
        ca = getattr(node, 'ca', None)
        if ca is not None:
            slots = ca.items.setdefault(last_key, [None, None, None, None])
            for index in (2, 3, 0, 1):
                if slots[index] is None:
                    slots[index] = deepcopy(token)
                    return True
    elif isinstance(node, list) and node:
        last_index = len(node) - 1
        if _attach_trailing_comment_token(node[last_index], token):
            return True
        ca = getattr(node, 'ca', None)
        if ca is not None:
            slots = ca.items.setdefault(last_index, [None, None, None, None])
            for index in (0, 1, 2, 3):
                if slots[index] is None:
                    slots[index] = deepcopy(token)
                    return True
    return False


def _relocate_boundary_comments(previous_value: Any, inserted_value: Any) -> None:
    previous = _standalone_comment_tokens_on_last_chain(previous_value)
    if not previous:
        return
    inserted_texts = {
        getattr(token, 'value', '')
        for _, _, token in _standalone_comment_tokens_on_last_chain(inserted_value)
    }
    for slots, index, token in previous:
        text = getattr(token, 'value', '')
        if text in inserted_texts:
            slots[index] = None
            continue
        if _attach_trailing_comment_token(inserted_value, token):
            slots[index] = None
            inserted_texts.add(text)


def _mapping_insert(parent: Any, key: str, value: Any, position: dict[str, Any] | None) -> None:
    """Insert a mapping key at an arbitrary readable position."""
    position = position or {}
    keys = list(parent.keys())
    key_existed = key in parent
    if position.get('first') is True:
        idx = 0
    elif position.get('last') is True:
        idx = len(keys)
    elif 'before_key' in position:
        if position['before_key'] not in parent:
            raise OperationError(f"position.before_key not found: {position['before_key']}")
        idx = keys.index(position['before_key'])
    elif 'after_key' in position:
        if position['after_key'] not in parent:
            raise OperationError(f"position.after_key not found: {position['after_key']}")
        idx = keys.index(position['after_key']) + 1
    elif 'index' in position:
        idx = position['index']
        if not isinstance(idx, int):
            raise OperationError('mapping position.index must be integer')
        if idx < 0:
            idx = max(0, len(keys) + idx + 1)
        idx = min(idx, len(keys))
    else:
        idx = len(keys)
    previous_value = parent[keys[idx - 1]] if (not key_existed and idx > 0 and idx <= len(keys)) else None
    if key in parent:
        del parent[key]
        keys = list(parent.keys())
        idx = min(idx, len(keys))
    if previous_value is not None:
        _relocate_boundary_comments(previous_value, value)
    if hasattr(parent, 'insert'):
        parent.insert(idx, key, value)
    else:
        items = list(parent.items()); items.insert(idx, (key, value)); parent.clear(); parent.update(items)


def _direct_root_mapping_key(path: str) -> str | None:
    """Return a direct root key for dot or JSON-Pointer generated paths."""
    if path.startswith('$.') and '.' not in path[2:] and '[' not in path:
        return path[2:]
    if path.startswith('$/') and '/' not in path[2:]:
        return path[2:].replace('~1', '/').replace('~0', '~')
    return None


@registry.register('copy_item_to_node')
def op_copy_item_to_node(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    """Deep-copy one matched list item into a mapping key/path."""
    source_path = spec.get('from_path') or spec.get('path')
    target_path = spec.get('to_path') or spec.get('target_path')
    if not source_path or not target_path:
        raise OperationError('copy_item_to_node requires from_path/path and to_path')
    seq = get_node(ctx.document, source_path)
    source = spec.get('source', {})
    if 'index' in source:
        idx = source['index']
    else:
        indices = find_indices(seq, source.get('match', {})); _require_matches(indices, source, 'source')
        if len(indices) != 1:
            raise MatchError('copy_item_to_node requires exactly one source')
        idx = indices[0]
    value = deepcopy(seq[idx])
    if spec.get('copy_leading_comments', True) is False:
        ca = getattr(value, 'ca', None)
        if ca is not None:
            ca.comment = None
    for path, item_value in spec.get('set', {}).items():
        item_target_path = path if ('.' in path or '[' in path or str(path).startswith(('$', '/'))) else '$/{0}'.format(str(path).replace('~', '~0').replace('/', '~1'))
        set_node(value, item_target_path, deepcopy(item_value), True)
    for path in spec.get('remove', []): remove_node(value, path)
    if spec.get('item_operations'):
        nested = OperationContext(document=value, original=deepcopy(value), variables=ctx.variables, captures=ctx.captures, skipped_operations=ctx.skipped_operations)
        for nested_spec in spec['item_operations']:
            registry.execute(nested_spec['op'], nested, nested_spec)
        value = nested.document
    # For a direct root mapping child, preserve requested mapping order.
    root_key = _direct_root_mapping_key(target_path)
    if root_key is not None:
        _mapping_insert(ctx.document, root_key, value, spec.get('position'))
    else:
        ctx.document = set_node(ctx.document, target_path, value, spec.get('create_missing', True))
    return ctx.document

# Override earlier mapping operations with the unified placement implementation.
@registry.register('insert_key')
def op_insert_key_v2(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    path = spec.get('path', '$')
    policy = _missing_policy(spec)
    try:
        parent = get_node(ctx.document, path)
    except Exception:
        if policy == 'skip':
            return ctx.document
        if policy == 'create':
            ctx.document = set_node(ctx.document, path, {}, True)
            parent = get_node(ctx.document, path)
        else:
            raise OperationError(f'Path not found: {path!r}')
    if not isinstance(parent, dict):
        raise OperationError('insert_key path must select a mapping')
    _mapping_insert(parent, spec['key'], deepcopy(spec.get('value')), spec.get('position'))
    return ctx.document

@registry.register('copy_key', 'move_key')
def op_copy_move_key_v2(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    path = spec.get('path', '$')
    policy = _missing_policy(spec, allow_create=False)
    try:
        parent = get_node(ctx.document, path)
    except Exception:
        if policy == 'skip': return ctx.document
        raise OperationError(f'Path not found: {path!r}')
    source_key = spec['source_key']; target_key = spec.get('target_key', source_key)
    if source_key not in parent:
        if policy == 'skip': return ctx.document
        raise OperationError(f'Missing key: {source_key}')
    value = deepcopy(parent[source_key])
    if spec['op'] == 'move_key': del parent[source_key]
    if target_key in parent and spec.get('on_conflict', 'error') == 'error' and target_key != source_key:
        raise OperationError(f'Target key already exists: {target_key}')
    _mapping_insert(parent, target_key, value, spec.get('position'))
    return ctx.document

@registry.register('copy_node', 'move_node')
def op_copy_move_node_v2(ctx: OperationContext, spec: dict[str, Any]) -> Any:
    source_path = spec.get('from_path') or spec.get('source')
    target_path = spec.get('to_path') or spec.get('path')
    if not isinstance(source_path, str) or not isinstance(target_path, str):
        raise OperationError('copy_node/move_node require from_path and to_path')
    policy = _missing_policy(spec)
    try:
        value = deepcopy(get_node(ctx.document, source_path))
    except Exception:
        if policy == 'skip': return ctx.document
        raise OperationError(f'Path not found: {source_path!r}')
    root_key = _direct_root_mapping_key(target_path)
    if root_key is not None:
        _mapping_insert(ctx.document, root_key, value, spec.get('position'))
    else:
        ctx.document = set_node(ctx.document, target_path, value, spec.get('create_missing', True))
    if spec['op'] == 'move_node': ctx.document = remove_node(ctx.document, source_path)
    return ctx.document
