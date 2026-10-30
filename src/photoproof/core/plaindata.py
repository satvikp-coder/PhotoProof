"""A small wire format for plain data sent by a process we do not trust.

Pickle cannot be used here, even with class lookups refused: a few bytes of crafted pickle
can make the reader allocate gigabytes (a huge memo index, for example). In this format
decoding is linear in the input. Every length is checked against the bytes actually present
before anything is allocated, and the number of objects and the nesting depth are capped.
"""

import struct

MAX_NODES = 1_000_000
MAX_DEPTH = 64


class PlainDataError(ValueError):
    """The value is not plain data, or the bytes are not a valid encoding of it."""


def encode(value: object) -> bytes:
    """Encode ``None``, numbers, strings, bytes and lists, tuples, dicts and sets of those."""
    parts: list[bytes] = []
    _encode(value, parts, depth=0)
    return b"".join(parts)


def decode(data: bytes) -> object:
    """Decode ``data``. Raises ``PlainDataError`` for anything malformed or over a limit."""
    reader = _Reader(data)
    value = reader.read_value(depth=0)
    if reader.offset != len(data):
        raise PlainDataError("trailing bytes after the value")
    return value


def _encode(value: object, parts: list[bytes], *, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise PlainDataError(f"nested deeper than {MAX_DEPTH} levels")
    if value is None:
        parts.append(b"N")
    elif value is True:
        parts.append(b"T")
    elif value is False:
        parts.append(b"F")
    elif type(value) is int:
        size = (value.bit_length() + 8) // 8
        parts += (b"I", struct.pack(">Q", size), value.to_bytes(size, "big", signed=True))
    elif type(value) is float:
        parts += (b"D", struct.pack(">d", value))
    elif type(value) is str:
        data = value.encode("utf-8", "surrogatepass")
        parts += (b"S", struct.pack(">Q", len(data)), data)
    elif type(value) is bytes or type(value) is bytearray:
        parts += (b"B", struct.pack(">Q", len(value)), bytes(value))
    elif type(value) is list or type(value) is tuple or type(value) is set:
        tag = b"L" if type(value) is list else b"U" if type(value) is tuple else b"E"
        parts += (tag, struct.pack(">Q", len(value)))
        for item in value:
            _encode(item, parts, depth=depth + 1)
    elif type(value) is dict:
        parts += (b"M", struct.pack(">Q", len(value)))
        for key, item in value.items():
            _encode(key, parts, depth=depth + 1)
            _encode(item, parts, depth=depth + 1)
    else:
        raise PlainDataError(f"{type(value).__name__} is not plain data")


class _Reader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.offset = 0
        self._nodes = 0

    def _take(self, size: int) -> bytes:
        if size > len(self._data) - self.offset:
            raise PlainDataError("the encoding is truncated")
        chunk = self._data[self.offset : self.offset + size]
        self.offset += size
        return chunk

    def _length(self) -> int:
        return int(struct.unpack(">Q", self._take(8))[0])

    def read_value(self, *, depth: int) -> object:
        if depth > MAX_DEPTH:
            raise PlainDataError(f"nested deeper than {MAX_DEPTH} levels")
        self._nodes += 1
        if self._nodes > MAX_NODES:
            raise PlainDataError(f"more than {MAX_NODES} objects")
        tag = self._take(1)
        if tag == b"N":
            return None
        if tag == b"T":
            return True
        if tag == b"F":
            return False
        if tag == b"I":
            return int.from_bytes(self._take(self._length()), "big", signed=True)
        if tag == b"D":
            return float(struct.unpack(">d", self._take(8))[0])
        if tag == b"S":
            return self._take(self._length()).decode("utf-8", "surrogatepass")
        if tag == b"B":
            return self._take(self._length())
        if tag in (b"L", b"U", b"E"):
            return self._read_sequence(tag, depth)
        if tag == b"M":
            return self._read_mapping(depth)
        raise PlainDataError(f"unknown tag {tag!r}")

    def _read_sequence(self, tag: bytes, depth: int) -> object:
        count = self._length()  # no preallocation: a false count runs out of bytes first
        items = [self.read_value(depth=depth + 1) for _ in range(self._bounded(count))]
        if tag == b"L":
            return items
        if tag == b"U":
            return tuple(items)
        return self._hashable(set, items)

    def _read_mapping(self, depth: int) -> object:
        count = self._length()
        pairs = [
            (self.read_value(depth=depth + 1), self.read_value(depth=depth + 1))
            for _ in range(self._bounded(count))
        ]
        return self._hashable(dict, pairs)

    def _bounded(self, count: int) -> int:
        if count > len(self._data) - self.offset:  # every item takes at least one byte
            raise PlainDataError("the encoding is truncated")
        return count

    @staticmethod
    def _hashable(kind: type, items: list[object]) -> object:
        try:
            return kind(items)
        except TypeError as error:
            raise PlainDataError(f"unhashable item: {error}") from error
