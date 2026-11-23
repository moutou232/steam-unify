"""appinfo.vdf (Steam v29) reader / writer.

The format (v29, magic 0x07564429) is:

    header:
        magic       u32
        universe    u32
        string_tbl  u64   (absolute file offset)
    entries (until string_tbl):
        appid            u32
        size             u32   (= 60 + len(vdf_data))
        info_state       u32
        last_updated     u32
        token            u8[8]
        appinfo_sha1     u8[20]
        change_number    u32
        vdf_sha1         u8[20]
        vdf_data         size-60 bytes (binary VDF, keys are u32 string-table indices)

All strings inside the binary VDF are normal byte strings; only *keys* are
indices into the string table located at the end of the file.
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


MAGIC_V29 = 0x07564429
HEADER_SIZE = 16
ENTRY_HEADER_SIZE = 68
ENTRY_META_SIZE = 60  # bytes after the 8-byte (appid,size) prefix

T_OBJECT = 0x00
T_STRING = 0x01
T_INT32 = 0x02
T_FLOAT32 = 0x03
T_POINTER = 0x04
T_WSTRING = 0x05
T_COLOR = 0x06
T_UINT64 = 0x07
T_END = 0x08
T_INT64 = 0x0A


class AppInfoError(Exception):
    """Raised when appinfo.vdf is not in the expected format."""


@dataclass
class Node:
    """A binary-VDF value.

    type    one of T_*
    key     string-table index (None only for synthetic roots)
    value   object -> list[Node], string/wstring/color -> bytes,
            int32/pointer/uint64/int64 -> int, float32 -> float
    """

    type: int
    key: int | None
    value: Any = None

    def children(self) -> list["Node"]:
        if self.type != T_OBJECT:
            raise AppInfoError("node is not an object")
        return self.value  # type: ignore[return-value]


@dataclass
class Entry:
    appid: int
    size: int
    info_state: int
    last_updated: int
    token: bytes
    appinfo_sha1: bytes
    change_number: int
    vdf_sha1: bytes
    raw_vdf: bytes
    root: Node
    tail: bytes = b"\x08"
    dirty: bool = False

    def vdf_bytes(self) -> bytes:
        if self.dirty:
            return serialize_node(self.root) + self.tail
        return self.raw_vdf


class _Parser:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def u32(self) -> int:
        if self.pos + 4 > len(self.data):
            raise AppInfoError("unexpected end of VDF data")
        v = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def parse_node(self, type_byte: int) -> Node:
        start = self.pos
        if type_byte == T_OBJECT:
            key = self.u32()
            children: list[Node] = []
            while True:
                if self.pos >= len(self.data):
                    raise AppInfoError("unterminated VDF object")
                t = self.data[self.pos]
                if t == T_END:
                    self.pos += 1
                    break
                self.pos += 1
                children.append(self.parse_node(t))
            return Node(T_OBJECT, key, children)

        key = self.u32()
        if type_byte == T_STRING:
            end = self.data.find(b"\x00", self.pos)
            if end < 0:
                raise AppInfoError("unterminated VDF string")
            value = self.data[self.pos:end]
            self.pos = end + 1
            return Node(T_STRING, key, value)
        if type_byte in (T_INT32, T_POINTER):
            if self.pos + 4 > len(self.data):
                raise AppInfoError("truncated VDF int32")
            value = struct.unpack_from("<i", self.data, self.pos)[0]
            self.pos += 4
            return Node(type_byte, key, value)
        if type_byte == T_FLOAT32:
            if self.pos + 4 > len(self.data):
                raise AppInfoError("truncated VDF float32")
            value = struct.unpack_from("<f", self.data, self.pos)[0]
            self.pos += 4
            return Node(T_FLOAT32, key, value)
        if type_byte == T_UINT64:
            if self.pos + 8 > len(self.data):
                raise AppInfoError("truncated VDF uint64")
            value = struct.unpack_from("<Q", self.data, self.pos)[0]
            self.pos += 8
            return Node(T_UINT64, key, value)
        if type_byte == T_INT64:
            if self.pos + 8 > len(self.data):
                raise AppInfoError("truncated VDF int64")
            value = struct.unpack_from("<q", self.data, self.pos)[0]
            self.pos += 8
            return Node(T_INT64, key, value)
        if type_byte == T_WSTRING:
            begin = self.pos
            while self.pos + 1 < len(self.data):
                if self.data[self.pos] == 0 and self.data[self.pos + 1] == 0:
                    value = self.data[begin:self.pos]
                    self.pos += 2
                    return Node(T_WSTRING, key, value)
                self.pos += 2
            raise AppInfoError("unterminated VDF wstring")
        if type_byte == T_COLOR:
            if self.pos + 4 > len(self.data):
                raise AppInfoError("truncated VDF color")
            value = self.data[self.pos:self.pos + 4]
            self.pos += 4
            return Node(T_COLOR, key, value)
        raise AppInfoError(
            f"unknown VDF type 0x{type_byte:02x} at offset {start}"
        )


def parse_node(data: bytes) -> tuple[Node, int]:
    p = _Parser(data)
    if not data:
        raise AppInfoError("empty VDF data")
    t = data[0]
    p.pos = 1
    node = p.parse_node(t)
    return node, p.pos


def serialize_node(node: Node) -> bytes:
    out = bytearray()
    out.append(node.type)
    if node.type == T_OBJECT:
        if node.key is None:
            raise AppInfoError("object without key index")
        out += struct.pack("<I", node.key)
        for child in node.value:
            out += serialize_node(child)
        out.append(T_END)
        return bytes(out)

    if node.key is None:
        raise AppInfoError("value without key index")
    out += struct.pack("<I", node.key)
    if node.type == T_STRING:
        out += bytes(node.value) + b"\x00"
    elif node.type in (T_INT32, T_POINTER):
        out += struct.pack("<i", int(node.value))
    elif node.type == T_FLOAT32:
        out += struct.pack("<f", float(node.value))
    elif node.type == T_UINT64:
        out += struct.pack("<Q", int(node.value))
    elif node.type == T_INT64:
        out += struct.pack("<q", int(node.value))
    elif node.type == T_WSTRING:
        out += bytes(node.value) + b"\x00\x00"
    elif node.type == T_COLOR:
        raw = bytes(node.value)
        if len(raw) != 4:
            raise AppInfoError("color value must be 4 bytes")
        out += raw
    else:
        raise AppInfoError(f"cannot serialize VDF type 0x{node.type:02x}")
    return bytes(out)


def _walk(node: Node) -> Iterable[Node]:
    yield node
    if node.type == T_OBJECT:
        for child in node.value:
            yield from _walk(child)


class AppInfo:
    """In-memory representation of appinfo.vdf."""

    def __init__(
        self,
        path: Path,
        magic: int,
        universe: int,
        entries: list[Entry],
        string_table_raw: bytes,
        keys: list[str],
    ):
        self.path = Path(path)
        self.magic = magic
        self.universe = universe
        self.entries = entries
        self.string_table_raw = string_table_raw
        self.keys = keys
        self._key_index: dict[str, int] = {}
        for i, k in enumerate(keys):
            self._key_index.setdefault(k, i)
        self._by_appid: dict[int, Entry] = {}
        for e in entries:
            self._by_appid[e.appid] = e

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: str | os.PathLike[str], verify: bool = True) -> "AppInfo":
        path = Path(path)
        data = path.read_bytes()
        if len(data) < HEADER_SIZE:
            raise AppInfoError(f"{path} is too small to be appinfo.vdf")
        magic, universe, string_table_off = struct.unpack_from("<IIQ", data, 0)
        if magic != MAGIC_V29:
            raise AppInfoError(
                f"unsupported appinfo.vdf version 0x{magic:08x} "
                f"(only 0x{MAGIC_V29:08x} / v29 is supported)"
            )
        if not (HEADER_SIZE <= string_table_off <= len(data)):
            raise AppInfoError("invalid string table offset")

        entries: list[Entry] = []
        off = HEADER_SIZE
        while off + 8 <= string_table_off:
            appid, size = struct.unpack_from("<II", data, off)
            if appid == 0 and size == 0:
                break
            if size < ENTRY_META_SIZE:
                raise AppInfoError(
                    f"invalid entry size {size} for app {appid} at 0x{off:x}"
                )
            if off + ENTRY_HEADER_SIZE + (size - ENTRY_META_SIZE) > string_table_off:
                raise AppInfoError(f"entry {appid} overruns string table")
            info_state, last_updated = struct.unpack_from("<II", data, off + 8)
            token = data[off + 16:off + 24]
            app_sha = data[off + 24:off + 44]
            change_number = struct.unpack_from("<I", data, off + 44)[0]
            vdf_sha = data[off + 48:off + 68]
            vdf_len = size - ENTRY_META_SIZE
            vdf = data[off + 68:off + 68 + vdf_len]
            root, used = parse_node(vdf)
            tail = vdf[used:]
            if tail != b"\x08":
                raise AppInfoError(
                    f"app {appid}: unexpected VDF trailer {tail.hex()!r} "
                    f"(consumed {used} of {vdf_len})"
                )
            entries.append(
                Entry(
                    appid=appid,
                    size=size,
                    info_state=info_state,
                    last_updated=last_updated,
                    token=token,
                    appinfo_sha1=app_sha,
                    change_number=change_number,
                    vdf_sha1=vdf_sha,
                    raw_vdf=vdf,
                    root=root,
                    tail=tail,
                )
            )
            off += ENTRY_HEADER_SIZE + vdf_len

        if off != string_table_off:
            # Steam pads the entry area with zeroes in some builds.
            pad = data[off:string_table_off]
            if pad.strip(b"\x00"):
                raise AppInfoError("entry area does not end at string table")

        table = data[string_table_off:]
        if len(table) < 4:
            raise AppInfoError("truncated string table")
        count = struct.unpack_from("<I", table, 0)[0]
        keys: list[str] = []
        pos = 4
        for _ in range(count):
            end = table.find(b"\x00", pos)
            if end < 0:
                raise AppInfoError("unterminated string table entry")
            keys.append(table[pos:end].decode("utf-8", "replace"))
            pos = end + 1

        obj = cls(path, magic, universe, entries, table, keys)
        if verify:
            obj.validate_roundtrip()
        return obj

    def validate_roundtrip(self) -> None:
        for e in self.entries:
            if serialize_node(e.root) + e.tail != e.raw_vdf:
                raise AppInfoError(
                    f"round-trip mismatch for app {e.appid} "
                    f"(vdf {len(e.raw_vdf)} bytes)"
                )

    # --------------------------------------------------------------- helpers
    def key_index(self, name: str) -> int:
        try:
            return self._key_index[name]
        except KeyError as exc:
            raise AppInfoError(f"string table has no key {name!r}") from exc

    def key_name(self, index: int) -> str:
        if 0 <= index < len(self.keys):
            return self.keys[index]
        return f"<bad-key-{index}>"

    def key_text(self, node: Node) -> str:
        return self.key_name(node.key) if node.key is not None else ""

    def entry(self, appid: int) -> Entry | None:
        return self._by_appid.get(appid)

    def common(self, entry: Entry) -> Node | None:
        # The root node itself is the "appinfo" object; its direct children
        # include "appid", "common", "config", ...
        for sub in entry.root.children():
            if sub.type == T_OBJECT and self.key_text(sub) == "common":
                return sub
        return None

    def _child(self, obj: Node, name: str) -> Node | None:
        if obj.type != T_OBJECT:
            return None
        for child in obj.children():
            if self.key_text(child) == name:
                return child
        return None

    # ------------------------------------------------------------ name logic
    def get_name(self, appid: int) -> str | None:
        e = self.entry(appid)
        if not e:
            return None
        common = self.common(e)
        if common is None:
            return None
        node = self._child(common, "name")
        if node is None or node.type != T_STRING:
            return None
        return node.value.decode("utf-8", "replace")

    def get_localized(self, appid: int) -> dict[str, str] | None:
        e = self.entry(appid)
        if not e:
            return None
        common = self.common(e)
        if common is None:
            return None
        node = self._child(common, "name_localized")
        if node is None or node.type != T_OBJECT:
            return None
        result: dict[str, str] = {}
        for child in node.children():
            if child.type == T_STRING:
                result[self.key_text(child)] = child.value.decode("utf-8", "replace")
        return result

    def set_name(self, appid: int, name: str) -> bool:
        e = self.entry(appid)
        if not e:
            raise AppInfoError(f"app {appid} not present in appinfo.vdf")
        common = self.common(e)
        if common is None:
            raise AppInfoError(f"app {appid} has no common section")
        node = self._child(common, "name")
        if node is None or node.type != T_STRING:
            raise AppInfoError(f"app {appid} has no common.name string")
        node.value = name.encode("utf-8")
        e.dirty = True
        return True

    def remove_localized(self, appid: int) -> dict[str, str] | None:
        """Remove common.name_localized so the custom name is used everywhere.

        Returns the removed mapping (for undo), or None if it did not exist.
        """
        e = self.entry(appid)
        if not e:
            return None
        common = self.common(e)
        if common is None:
            return None
        node = self._child(common, "name_localized")
        if node is None:
            return None
        old = self.get_localized(appid) or {}
        common.value.remove(node)
        e.dirty = True
        return old

    def restore_localized(self, appid: int, mapping: dict[str, str] | None) -> None:
        if not mapping:
            return
        e = self.entry(appid)
        if not e:
            return
        common = self.common(e)
        if common is None:
            return
        if self._child(common, "name_localized") is not None:
            return
        lang_nodes: list[Node] = []
        for lang, text in mapping.items():
            idx = self.key_index(lang)
            lang_nodes.append(Node(T_STRING, idx, text.encode("utf-8")))
        node = Node(T_OBJECT, self.key_index("name_localized"), lang_nodes)
        common.value.append(node)
        e.dirty = True

    # --------------------------------------------------------------- writing
    def build(self) -> bytes:
        entry_blobs: list[bytes] = []
        for e in self.entries:
            vdf = e.vdf_bytes()
            size = ENTRY_META_SIZE + len(vdf)
            sha = hashlib.sha1(vdf).digest() if e.dirty else e.vdf_sha1
            blob = (
                struct.pack("<IIII", e.appid, size, e.info_state, e.last_updated)
                + e.token
                + e.appinfo_sha1
                + struct.pack("<I", e.change_number)
                + sha
                + vdf
            )
            entry_blobs.append(blob)
        new_table_off = HEADER_SIZE + sum(len(b) for b in entry_blobs)
        out = (
            struct.pack("<IIQ", self.magic, self.universe, new_table_off)
            + b"".join(entry_blobs)
            + self.string_table_raw
        )
        # Extra safety: the result must parse and every appid must be unchanged.
        check = AppInfo.from_bytes(out, self.path)
        if [e.appid for e in check.entries] != [e.appid for e in self.entries]:
            raise AppInfoError("entry list changed during serialization")
        return out

    @classmethod
    def from_bytes(cls, data: bytes, path: Path) -> "AppInfo":
        import tempfile

        # Reuse load() without touching the original file.
        tmp = Path(tempfile.mkdtemp(prefix="appinfo-")) / "appinfo.vdf"
        try:
            tmp.write_bytes(data)
            return cls.load(tmp, verify=False)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    def save(self, path: str | os.PathLike[str] | None = None) -> bytes:
        path = Path(path) if path is not None else self.path
        data = self.build()
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return data
