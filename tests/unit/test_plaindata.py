"""The wire format for replies from untrusted children."""

import math
import random
import struct
import time
from collections.abc import Callable

import pytest

from photoproof.core.plaindata import MAX_DEPTH, MAX_NODES, PlainDataError, decode, encode


def nested(depth: int) -> object:
    value: object = []
    for _ in range(depth):
        value = [value]
    return value


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        0,
        -1,
        255,
        -256,
        2**200,
        -(2**200),
        0.5,
        float("inf"),
        "",
        "名前",
        "lone \udcff surrogate",
        b"",
        b"\x00\xff",
        [],
        (),
        set(),
        {},
        [1, [2, [3]]],
        (1, "a", b"b"),
        {"x", b"y"},
        {"a": {"b": [1, 2, b"x"]}, b"k": None},
        ("ok", {"pixels": b"x" * 1000}),
    ],
)
def test_plain_data_round_trips_with_its_type(value: object) -> None:
    result = decode(encode(value))
    assert result == value and type(result) is type(value)


def test_nan_round_trips() -> None:
    result = decode(encode(float("nan")))
    assert isinstance(result, float) and math.isnan(result)


def test_bool_is_not_confused_with_int() -> None:
    assert decode(encode(True)) is True
    assert decode(encode(1)) == 1 and decode(encode(1)) is not True


def test_bytearray_becomes_bytes() -> None:
    assert decode(encode(bytearray(b"ab"))) == b"ab"


class _Subclass(int):
    pass


@pytest.mark.parametrize(
    "value",
    [object(), ValueError("x"), _Subclass(1), frozenset({1}), print, [object()], {"a": object()}],
)
def test_anything_else_cannot_be_encoded(value: object) -> None:
    with pytest.raises(PlainDataError, match="not plain data"):
        encode(value)


def test_depth_limit_applies_when_encoding() -> None:
    assert decode(encode(nested(MAX_DEPTH))) == nested(MAX_DEPTH)
    with pytest.raises(PlainDataError, match="deeper"):
        encode(nested(MAX_DEPTH + 1))


def test_depth_limit_applies_when_decoding() -> None:
    forged = (b"L" + struct.pack(">Q", 1)) * (MAX_DEPTH + 5) + b"N"
    with pytest.raises(PlainDataError, match="deeper"):
        decode(forged)


def test_object_count_is_capped_when_decoding() -> None:
    forged = b"L" + struct.pack(">Q", MAX_NODES + 1) + b"N" * (MAX_NODES + 1)
    with pytest.raises(PlainDataError, match="more than"):
        decode(forged)


@pytest.mark.parametrize(
    "forged",
    [
        b"",
        b"?",
        b"S",
        b"S" + struct.pack(">Q", 2**62) + b"abc",  # claims far more than is present
        b"B" + struct.pack(">Q", 2**63) + b"abc",
        b"I" + struct.pack(">Q", 2**63),
        b"L" + struct.pack(">Q", 2**62),  # a huge list with no items behind it
        b"M" + struct.pack(">Q", 2**62) + b"N",
        b"E" + struct.pack(">Q", 2**40) + b"NN",
        b"D\x00\x00",
        b"NN",  # trailing bytes
    ],
)
def test_malformed_input_raises_without_allocating(forged: bytes) -> None:
    start = time.monotonic()
    with pytest.raises(PlainDataError):
        decode(forged)
    assert time.monotonic() - start < 1


def test_invalid_utf8_is_rejected() -> None:
    with pytest.raises(ValueError):
        decode(b"S" + struct.pack(">Q", 1) + b"\xff")


@pytest.mark.parametrize(
    "build",
    [
        lambda: b"E" + struct.pack(">Q", 1) + b"L" + struct.pack(">Q", 0),  # a list in a set
        lambda: b"M" + struct.pack(">Q", 1) + b"L" + struct.pack(">Q", 0) + b"N",  # a list as a key
    ],
)
def test_unhashable_items_are_rejected(build: Callable[[], bytes]) -> None:
    with pytest.raises(PlainDataError, match="must be strings or bytes"):
        decode(build())


@pytest.mark.parametrize("value", [{1, 2}, {(1, 2)}, {None}, {1.5}, {1: "a"}, {(1,): "a"}])
def test_collidable_keys_cannot_be_encoded(value: object) -> None:
    with pytest.raises(PlainDataError, match="must be strings or bytes"):
        encode(value)


def _integers_with_colliding_hashes(count: int) -> bytes:
    """A set of integers that all land in one hash bucket, as a hostile child would send."""
    modulus = (1 << 61) - 1
    items = b"".join(
        b"I" + struct.pack(">Q", 16) + (index * modulus).to_bytes(16, "big", signed=True)
        for index in range(count)
    )
    return b"E" + struct.pack(">Q", count) + items


def test_colliding_integers_are_refused_before_any_container_is_built() -> None:
    start = time.monotonic()
    with pytest.raises(PlainDataError, match="must be strings or bytes"):
        decode(_integers_with_colliding_hashes(50_000))
    assert time.monotonic() - start < 2  # building this set would take minutes


def test_random_bytes_only_ever_raise_value_errors() -> None:
    generator = random.Random(20261030)  # noqa: S311 - reproducible test data
    tags = b"NTFIDSBLUEM"
    for _ in range(20000):
        data = bytes(
            generator.choice(tags) if generator.random() < 0.4 else generator.randrange(256)
            for _ in range(generator.randint(0, 40))
        )
        try:
            decode(data)
        except ValueError:
            continue


def test_corrupting_a_valid_encoding_only_ever_raises_value_errors() -> None:
    generator = random.Random(20261031)  # noqa: S311 - reproducible test data
    valid = encode({"a": [1, 2.5, "text", b"bytes", (None, True)], "b": {"x", "y"}})
    for _ in range(5000):
        data = bytearray(valid)
        data[generator.randrange(len(data))] = generator.randrange(256)
        try:
            decode(bytes(data))
        except ValueError:
            continue
