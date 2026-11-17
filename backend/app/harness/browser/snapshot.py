"""Compact AX outlines and snapshot-scoped, non-reusable element references."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class StaleReferenceError(ValueError):
    pass


@dataclass(frozen=True)
class ElementReference:
    backend_node_id: int
    ax_node_id: str
    role: str
    password: bool = False


_INTERACTIVE = {"button", "link", "textbox", "searchbox", "combobox", "listbox", "option",
                "checkbox", "radio", "slider", "spinbutton", "menuitem", "tab", "switch"}
_SKIP = {"none", "generic", "InlineTextBox"}
_STATES = {"checked", "selected", "expanded", "disabled", "required", "focused", "level"}


def _value(node: dict, key: str, default: Any = "") -> Any:
    return (node.get(key) or {}).get("value", default)


def _quoted(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value).split())
    if len(text) > limit:
        text = text[:limit - 1] + "…"
    return json.dumps(text, ensure_ascii=False)


class SnapshotReferences:
    def __init__(self, *, counter_start: int = 0):
        self._counter = counter_start
        self._refs: dict[str, ElementReference] = {}

    def invalidate(self):
        # Never reset the counter: an old e1 must not point to a new page's e1.
        self._refs.clear()

    def resolve(self, ref: str) -> ElementReference:
        reference = self._refs.get(str(ref).strip("[]"))
        if reference is None:
            raise StaleReferenceError("Unknown or expired element ref; call browser_snapshot again")
        return reference

    def render(self, nodes: list[dict], *, urls: dict[int, str] | None = None,
               ref: str | None = None, find: str | None = None, max_chars: int = 12000) -> dict:
        if not 200 <= max_chars <= 100000:
            raise ValueError("Snapshot limit must be between 200 and 100000 characters")
        root_id = self.resolve(ref).ax_node_id if ref else None
        # Every new snapshot replaces references, including a search/subtree view.
        self.invalidate()
        by_id = {str(node["nodeId"]): node for node in nodes}
        children = {str(child) for node in nodes for child in node.get("childIds", [])}
        roots = [root_id] if root_id else [key for key in by_id if key not in children]
        lines: list[str] = []
        seen: set[str] = set()
        size = 0
        truncated = False
        # Reserve space for an explicit truncation message.
        note = "\n[Snapshot truncated; call browser_snapshot with ref or find to continue.]"
        budget = max_chars - len(note)
        stack = [(key, 0) for key in reversed(roots)]
        while stack:
            key, depth = stack.pop()
            if key in seen or key not in by_id:
                continue
            seen.add(key)
            node = by_id[key]
            role = str(_value(node, "role"))
            name = str(_value(node, "name"))
            value = _value(node, "value")
            properties = {prop["name"]: _value(prop, "value") for prop in node.get("properties", [])}
            password = bool(properties.get("protected")) or str(properties.get("type", "")).lower() == "password"
            meaningful = not node.get("ignored") and role not in _SKIP
            matches = not find or find.casefold() in f"{role} {name} {'' if password else value}".casefold()
            emitted = meaningful and matches
            if emitted:
                backend_id = node.get("backendDOMNodeId")
                token = None
                if isinstance(backend_id, int) and (name or value or role in _INTERACTIVE):
                    self._counter += 1
                    token = f"e{self._counter}"
                pieces = ["  " * min(depth, 12) + (f"[{token}] " if token else "") + role]
                if name:
                    pieces.append(_quoted(name))
                if password:
                    pieces.append("value=[protected]")
                elif value != "" and value is not None:
                    pieces.append("value=" + _quoted(value))
                for state in sorted(_STATES):
                    if state in properties and properties[state] is not False:
                        pieces.append(f"{state}={_quoted(properties[state], 40)}")
                if backend_id in (urls or {}) and role == "link":
                    pieces.append("url=" + _quoted(urls[backend_id], 400))
                line = " ".join(pieces)
                if size + len(line) + 1 > budget:
                    truncated = True
                    break
                lines.append(line)
                size += len(line) + 1
                if token:
                    self._refs[token] = ElementReference(backend_id, key, role, password)
            child_depth = depth + (1 if emitted else 0)
            stack.extend((str(child), child_depth) for child in reversed(node.get("childIds", [])))
        text = "\n".join(lines)
        if truncated:
            text += note
        return {"text": text, "truncated": truncated, "refs": len(self._refs)}
