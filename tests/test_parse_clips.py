import pytest

from server import parse_clips


def test_extracts_json_surrounded_by_prose():
    reply = 'Sure! Here you go:\n{"clips": [{"start": 1, "end": 9, "score": 70}]}\nHope it helps.'
    assert parse_clips(reply) == [{"start": 1, "end": 9, "score": 70}]


def test_drops_entries_without_start_or_end():
    reply = '{"clips": [{"start": 1}, {"end": 5}, {"start": 2, "end": 8}]}'
    assert [c["start"] for c in parse_clips(reply)] == [2]


def test_drops_entries_with_non_numeric_times():
    reply = '{"clips": [{"start": "soon", "end": 8}, {"start": "3.5", "end": 9}]}'
    assert [c["start"] for c in parse_clips(reply)] == ["3.5"]


def test_missing_clips_key_returns_empty_list():
    assert parse_clips('{"moments": []}') == []


def test_no_json_object_raises():
    with pytest.raises(ValueError):
        parse_clips("I could not find any moments.")


def test_bare_clip_object_without_clips_wrapper_yields_nothing():
    assert parse_clips('[{"start": 1, "end": 2}]') == []


def test_invalid_json_raises_value_error():
    with pytest.raises(ValueError):
        parse_clips('{"clips": [}')
