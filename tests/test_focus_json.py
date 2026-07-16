"""Deterministic FOCUS JSON serializer: numeric-literal rules and JsonNumber."""

from __future__ import annotations

import pytest

from focus_data_toolkit.focus_json import JsonNumber, dumps_object, is_json_number_literal


@pytest.mark.parametrize("text", ["0", "-3", "1.234", "35.2E-7", "35.2E7", "10", "-100.2"])
def test_valid_json_number_literals(text):
    assert is_json_number_literal(text)


@pytest.mark.parametrize(
    "text",
    ["01", "00.5", "007", "1.5E+3", "+3", "3,432", "1.", ".5", "abc", ""],
)
def test_invalid_json_number_literals(text):
    assert not is_json_number_literal(text)


def test_dumps_object_emits_numeric_keys_unquoted():
    out = dumps_object({"Elements": [{"Ratio": "0.5"}]}, numeric_keys=frozenset({"Ratio"}))
    assert out == '{"Elements":[{"Ratio":0.5}]}'


def test_dumps_object_rejects_leading_zero_number():
    with pytest.raises(ValueError, match="not a JSON number"):
        dumps_object({"Ratio": "01"}, numeric_keys=frozenset({"Ratio"}))


def test_jsonnumber_emitted_as_raw_number_regardless_of_key():
    out = dumps_object({"x_Qty": JsonNumber("1.0000"), "Name": "s"})
    assert out == '{"x_Qty":1.0000,"Name":"s"}'
