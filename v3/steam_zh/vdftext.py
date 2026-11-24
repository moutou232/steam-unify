"""Small, dependency-free parser for Steam's text VDF files.

Steam text VDF is a tree of string keys and string values:

    "root" { "key" "value" "nested" { ... } }

It may contain // comments, quoted or bare tokens, and backslash escapes.
We keep insertion order (normal dicts do) and return nested dicts.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class VDFTextError(Exception):
    pass


def _unquote(s: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n == "n":
                out.append("\n")
            elif n == "t":
                out.append("\t")
            elif n == "r":
                out.append("\r")
            elif n in ('"', "\\"):
                out.append(n)
            else:
                out.append(n)
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[dict[str, Any]] = [root]
    pending_key: str | None = None
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == "{":
            if pending_key is None:
                raise VDFTextError("opening brace without a key")
            node: dict[str, Any] = {}
            stack[-1][pending_key] = node
            stack.append(node)
            pending_key = None
            i += 1
            continue
        if c == "}":
            if len(stack) <= 1:
                raise VDFTextError("unexpected closing brace")
            stack.pop()
            pending_key = None
            i += 1
            continue
        if c == '"':
            i += 1
            start = i
            buf: list[str] = []
            while i < n:
                ch = text[i]
                if ch == "\\" and i + 1 < n:
                    buf.append(ch)
                    buf.append(text[i + 1])
                    i += 2
                    continue
                if ch == '"':
                    i += 1
                    break
                buf.append(ch)
                i += 1
            else:
                raise VDFTextError("unterminated quoted string")
            token = _unquote("".join(buf))
        else:
            start = i
            while i < n and text[i] not in ' \t\r\n{}"':
                i += 1
            token = text[start:i]
            if not token:
                i += 1
                continue
        if pending_key is None:
            pending_key = token
        else:
            stack[-1][pending_key] = token
            pending_key = None
    if len(stack) != 1:
        raise VDFTextError("unclosed braces")
    return root


def load(path: str | Path) -> dict[str, Any]:
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return parse(data.decode(enc))
        except UnicodeDecodeError:
            continue
    raise VDFTextError(f"cannot decode {path}")


def get_ci(root: dict[str, Any], key: str, default: Any = None) -> Any:
    """Case-insensitive lookup in a VDF dict."""
    if key in root:
        return root[key]
    low = key.lower()
    for k, v in root.items():
        if k.lower() == low:
            return v
    return default


def dump(root: dict[str, Any], indent: str = "\t") -> str:
    lines: list[str] = []

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    def walk(node: dict[str, Any], depth: int) -> None:
        pad = indent * depth
        for k, v in node.items():
            if isinstance(v, dict):
                lines.append(f'{pad}"{esc(k)}"')
                lines.append(f"{pad}{{")
                walk(v, depth + 1)
                lines.append(f"{pad}}}")
            else:
                lines.append(f'{pad}"{esc(k)}"\t\t"{esc(str(v))}"')

    walk(root, 0)
    return "\n".join(lines) + "\n"


def appmanifest_name(raw_text: str) -> str:
    """Return the top-level AppState.name without touching other fields."""
    import re
    m = re.search(r'"name"\s+"((?:\\.|[^"])*)"', raw_text)
    return m.group(1).replace('\\"', '"').replace('\\\\', '\\') if m else ""


def replace_appmanifest_name(raw_text: str, new_name: str) -> str:
    """Replace only the top-level AppState.name line, preserving formatting."""
    esc = new_name.replace("\\", "\\\\").replace('"', '\\"')
    pattern = re.compile(r'^(\s*"name"\s*)"(?:[^"\\]|\\.)*"', re.MULTILINE)
    replacement = r'\1"' + esc + '"'
    new, count = pattern.subn(replacement, raw_text, count=1)
    if count != 1:
        raise VDFTextError("AppState has no top-level name field")
    return new
