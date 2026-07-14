from __future__ import annotations

from dataclasses import dataclass, field
import html
import re
from typing import Any, Iterable


class XmlFormatError(ValueError):
    pass


_NAME = r"[A-Za-z_][\w.:-]*"
_ATTR_RE = re.compile(rf"({_NAME})(\s*=\s*)(['\"])(.*?)\3", re.S)
_PRED_ATTR_RE = re.compile(rf"^@({_NAME})\s*=\s*(['\"])(.*?)\2$")
_PRED_CHILD_RE = re.compile(rf"^({_NAME})\s*=\s*(['\"])(.*?)\2$")


@dataclass
class AttrSpan:
    name: str
    name_start: int
    name_end: int
    value_start: int
    value_end: int
    quote: str


@dataclass
class XmlNode:
    name: str
    start: int
    start_tag_end: int
    open_name_start: int
    open_name_end: int
    parent: "XmlNode | None" = None
    attrs: dict[str, AttrSpan] = field(default_factory=dict)
    children: list["XmlNode"] = field(default_factory=list)
    end_tag_start: int | None = None
    end: int | None = None
    close_name_start: int | None = None
    close_name_end: int | None = None
    self_closing: bool = False

    @property
    def content_start(self) -> int:
        return self.start_tag_end

    @property
    def content_end(self) -> int:
        return self.end_tag_start if self.end_tag_start is not None else self.start_tag_end

    def direct_children(self, name: str | None = None) -> list["XmlNode"]:
        if name in (None, "*"):
            return list(self.children)
        return [x for x in self.children if x.name == name]


@dataclass(frozen=True)
class XmlTarget:
    kind: str  # element, attribute, text
    node: XmlNode
    attr: AttrSpan | None = None


@dataclass(frozen=True)
class Patch:
    start: int
    end: int
    replacement: str
    label: str = ""


def _scan_markup_end(text: str, start: int) -> int:
    quote: str | None = None
    i = start
    while i < len(text):
        c = text[i]
        if quote:
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == '>':
            return i + 1
        i += 1
    raise XmlFormatError(f"Unterminated XML markup at offset {start}")


def _scan_doctype_end(text: str, start: int) -> int:
    quote: str | None = None
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if quote:
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == '[':
            depth += 1
        elif c == ']':
            depth = max(0, depth - 1)
        elif c == '>' and depth == 0:
            return i + 1
        i += 1
    raise XmlFormatError("Unterminated DOCTYPE")


def parse_xml_spans(text: str) -> tuple[XmlNode, list[XmlNode]]:
    roots: list[XmlNode] = []
    all_nodes: list[XmlNode] = []
    stack: list[XmlNode] = []
    i = 0
    while i < len(text):
        lt = text.find('<', i)
        if lt < 0:
            break
        if text.startswith('<!--', lt):
            end = text.find('-->', lt + 4)
            if end < 0:
                raise XmlFormatError("Unterminated XML comment")
            i = end + 3; continue
        if text.startswith('<![CDATA[', lt):
            end = text.find(']]>', lt + 9)
            if end < 0:
                raise XmlFormatError("Unterminated CDATA")
            i = end + 3; continue
        if text.startswith('<?', lt):
            end = text.find('?>', lt + 2)
            if end < 0:
                raise XmlFormatError("Unterminated processing instruction")
            i = end + 2; continue
        if text[lt:lt+9].upper() == '<!DOCTYPE':
            i = _scan_doctype_end(text, lt + 9); continue
        if text.startswith('<!', lt):
            i = _scan_markup_end(text, lt + 2); continue
        if text.startswith('</', lt):
            tag_end = _scan_markup_end(text, lt + 2)
            m = re.match(rf"</\s*({_NAME})", text[lt:tag_end])
            if not m:
                raise XmlFormatError(f"Invalid closing tag at offset {lt}")
            name = m.group(1)
            if not stack or stack[-1].name != name:
                expected = stack[-1].name if stack else None
                raise XmlFormatError(f"Mismatched closing tag {name!r}; expected {expected!r}")
            node = stack.pop()
            node.end_tag_start = lt
            node.end = tag_end
            node.close_name_start = lt + m.start(1)
            node.close_name_end = lt + m.end(1)
            i = tag_end; continue
        tag_end = _scan_markup_end(text, lt + 1)
        raw = text[lt:tag_end]
        m = re.match(rf"<\s*({_NAME})", raw)
        if not m:
            raise XmlFormatError(f"Invalid opening tag at offset {lt}")
        name = m.group(1)
        self_closing = bool(re.search(r"/\s*>$", raw))
        parent = stack[-1] if stack else None
        node = XmlNode(
            name=name, start=lt, start_tag_end=tag_end,
            open_name_start=lt + m.start(1), open_name_end=lt + m.end(1),
            parent=parent, self_closing=self_closing,
        )
        attrs_text_start = m.end(1)
        for am in _ATTR_RE.finditer(raw, attrs_text_start):
            attr_name = am.group(1)
            node.attrs[attr_name] = AttrSpan(
                name=attr_name,
                name_start=lt + am.start(1), name_end=lt + am.end(1),
                value_start=lt + am.start(4), value_end=lt + am.end(4), quote=am.group(3),
            )
        if parent:
            parent.children.append(node)
        else:
            roots.append(node)
        all_nodes.append(node)
        if self_closing:
            node.end_tag_start = tag_end - 2
            node.end = tag_end
        else:
            stack.append(node)
        i = tag_end
    if stack:
        raise XmlFormatError(f"Unclosed tag: {stack[-1].name}")
    if len(roots) != 1:
        raise XmlFormatError(f"Expected one XML root element, found {len(roots)}")
    return roots[0], all_nodes


def xml_unescape(value: str) -> str:
    return html.unescape(value)


def xml_escape_text(value: Any) -> str:
    return html.escape(str(value), quote=False)


def xml_escape_attr(value: Any, quote: str = '"') -> str:
    escaped = html.escape(str(value), quote=True)
    if quote == "'":
        escaped = escaped.replace('&#x27;', '&apos;')
    return escaped


def node_text(text: str, node: XmlNode) -> str:
    chunks: list[str] = []
    cursor = node.content_start
    for child in node.children:
        if child.start > cursor:
            chunks.append(text[cursor:child.start])
        cursor = child.end or child.start_tag_end
    if cursor < node.content_end:
        chunks.append(text[cursor:node.content_end])
    raw = ''.join(chunks)
    raw = re.sub(r'<!--.*?-->', '', raw, flags=re.S)
    raw = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', raw, flags=re.S)
    return xml_unescape(raw).strip()


def _split_path(path: str) -> tuple[bool, list[str]]:
    p = (path or '').strip()
    descendant = p.startswith('//')
    if p.startswith('$.'):
        p = '/' + p[2:].replace('.', '/')
    elif p == '$':
        p = '/'
    if p.startswith('//'):
        p = p[2:]
    elif p.startswith('/'):
        p = p[1:]
    parts: list[str] = []
    buf = ''
    bracket = 0
    quote: str | None = None
    for c in p:
        if quote:
            buf += c
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c; buf += c
        elif c == '[':
            bracket += 1; buf += c
        elif c == ']':
            bracket -= 1; buf += c
        elif c == '/' and bracket == 0:
            if buf: parts.append(buf); buf = ''
        else:
            buf += c
    if buf: parts.append(buf)
    return descendant, parts


def _descendants(node: XmlNode) -> Iterable[XmlNode]:
    for child in node.children:
        yield child
        yield from _descendants(child)


def _parse_segment(segment: str) -> tuple[str, str | None]:
    m = re.fullmatch(rf"({_NAME}|\*)(?:\[(.*)\])?", segment.strip())
    if not m:
        raise XmlFormatError(f"Unsupported XML path segment: {segment!r}")
    return m.group(1), m.group(2)


def _filter_predicate(text: str, nodes: list[XmlNode], predicate: str | None) -> list[XmlNode]:
    if not predicate or predicate.strip() == '*':
        return nodes
    pred = predicate.strip()
    if pred.isdigit():
        idx = int(pred) - 1
        return [nodes[idx]] if 0 <= idx < len(nodes) else []
    am = _PRED_ATTR_RE.match(pred)
    if am:
        name, expected = am.group(1), am.group(3)
        return [n for n in nodes if name in n.attrs and xml_unescape(text[n.attrs[name].value_start:n.attrs[name].value_end]) == expected]
    cm = _PRED_CHILD_RE.match(pred)
    if cm:
        name, expected = cm.group(1), cm.group(3)
        return [n for n in nodes if any(node_text(text, c) == expected for c in n.direct_children(name))]
    raise XmlFormatError(f"Unsupported XML predicate: [{predicate}]")


def select(text: str, root: XmlNode, path: str) -> list[XmlTarget]:
    descendant, parts = _split_path(path)
    if not parts:
        return [XmlTarget('element', root)]
    terminal = None
    if parts[-1].startswith('@'):
        terminal = ('attribute', parts.pop()[1:])
    elif parts[-1] == 'text()':
        terminal = ('text', None); parts.pop()

    current: list[XmlNode]
    first_name, first_pred = _parse_segment(parts[0]) if parts else ('*', None)
    if descendant:
        candidates = [root, *_descendants(root)]
        current = _filter_predicate(text, [n for n in candidates if first_name == '*' or n.name == first_name], first_pred)
        parts = parts[1:]
    else:
        if first_name == '*' or root.name == first_name:
            current = _filter_predicate(text, [root], first_pred)
            parts = parts[1:]
        else:
            current = _filter_predicate(text, root.direct_children(first_name), first_pred)
            parts = parts[1:]
    for part in parts:
        name, pred = _parse_segment(part)
        nxt: list[XmlNode] = []
        for n in current:
            nxt.extend(_filter_predicate(text, n.direct_children(name), pred))
        current = nxt
    if terminal is None:
        return [XmlTarget('element', n) for n in current]
    kind, attr_name = terminal
    if kind == 'text':
        return [XmlTarget('text', n) for n in current]
    targets = []
    for n in current:
        attr = n.attrs.get(attr_name or '')
        if attr:
            targets.append(XmlTarget('attribute', n, attr))
    return targets


def detect_newline(text: str) -> str:
    return '\r\n' if '\r\n' in text else '\n'


def line_indent(text: str, offset: int) -> str:
    line_start = max(text.rfind('\n', 0, offset), text.rfind('\r', 0, offset)) + 1
    return re.match(r'[ \t]*', text[line_start:offset]).group(0)


def child_indent(text: str, node: XmlNode) -> str:
    if node.children:
        return line_indent(text, node.children[0].start)
    return line_indent(text, node.start) + '  '


def serialize_element(name: str, value: Any, indent: str, newline: str) -> str:
    if isinstance(value, dict):
        attrs = value.get('@attributes', {}) if isinstance(value.get('@attributes', {}), dict) else {}
        attr_text = ''.join(f' {k}="{xml_escape_attr(v)}"' for k, v in attrs.items())
        child_items = [(k, v) for k, v in value.items() if k not in {'@attributes', '#text'}]
        text_value = value.get('#text')
        if not child_items and text_value is None:
            return f'<{name}{attr_text}/>'
        if not child_items:
            return f'<{name}{attr_text}>{xml_escape_text(text_value)}</{name}>'
        pieces = [f'<{name}{attr_text}>']
        if text_value not in (None, ''):
            pieces.append(xml_escape_text(text_value))
        child_line_indent = indent + '  '
        for child_name, child_value in child_items:
            values = child_value if isinstance(child_value, list) else [child_value]
            for v in values:
                # `serialize_element` already emits nested lines using the
                # absolute indentation passed to it. Re-indenting the returned
                # string a second time made a newly-created section drift on
                # the second idempotency run.
                rendered = serialize_element(child_name, v, child_line_indent, newline)
                pieces.append(newline + child_line_indent + rendered)
        pieces.append(newline + indent + f'</{name}>')
        return ''.join(pieces)
    if value is None:
        return f'<{name}/>'
    return f'<{name}>{xml_escape_text(value)}</{name}>'


def apply_patches(text: str, patches: list[Patch]) -> str:
    if not patches:
        return text
    ordered = sorted(patches, key=lambda p: (p.start, p.end))
    for left, right in zip(ordered, ordered[1:]):
        if right.start < left.end:
            raise XmlFormatError(f"Overlapping XML patches: {left.label!r} and {right.label!r}")
    out = text
    for patch in reversed(ordered):
        out = out[:patch.start] + patch.replacement + out[patch.end:]
    return out
