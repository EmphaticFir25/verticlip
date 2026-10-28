import httpx
import pytest

import server
from server import pick_moments, score_windows


def cand(start, end, score=50, **extra):
    return {"start": start, "end": end, "score": score, "title": f"t{start}", **extra}


def test_clamps_each_moment_to_clip_len():
    picked = pick_moments([cand(10, 100)], clip_len=30, max_clips=5)
    assert (picked[0]["start"], picked[0]["end"]) == (10, 40)


def test_drops_moments_shorter_than_eight_seconds():
    assert pick_moments([cand(10, 15)], clip_len=30, max_clips=5) == []


def test_keeps_best_scoring_of_overlapping_moments():
    picked = pick_moments([cand(10, 30, score=40), cand(20, 40, score=90)], clip_len=30, max_clips=5)
    assert [p["score"] for p in picked] == [90]


def test_non_overlapping_moments_are_both_kept_in_score_order():
    picked = pick_moments([cand(0, 20, score=60), cand(100, 120, score=80)], clip_len=30, max_clips=5)
    assert [p["score"] for p in picked] == [80, 60]


def test_max_clips_caps_the_result():
    many = [cand(i * 100, i * 100 + 20, score=i) for i in range(10)]
    assert len(pick_moments(many, clip_len=30, max_clips=3)) == 3


def test_empty_transcript_returns_no_moments():
    assert score_windows([], 30, 6, chat=lambda m: "") == []


def words_over(seconds: float) -> list[dict]:
    """One word every 5 seconds across the given span."""
    return [{"start": t, "end": t + 0.5, "word": "w"} for t in range(0, int(seconds), 5)]


def test_transcript_is_split_into_ten_minute_windows():
    calls = []

    def chat(messages):
        calls.append(messages[-1]["content"])
        return '{"clips": []}'

    score_windows(words_over(1500), 30, 6, chat=chat)  # 25 minutes -> 3 windows
    assert len(calls) == 3


def test_invalid_json_is_retried_once_then_window_skipped():
    calls = []

    def chat(messages):
        calls.append(1)
        return "not json at all"

    result = score_windows(words_over(300), 30, 6, chat=chat)
    assert result == []
    assert len(calls) == 2


def test_retry_recovers_a_window_on_second_attempt():
    replies = iter(["oops", '{"clips": [{"start": 5, "end": 25, "score": 77}]}'])
    picked = score_windows(words_over(300), 30, 6, chat=lambda m: next(replies))
    assert [(p["start"], p["end"], p["score"]) for p in picked] == [(5, 25, 77)]


def test_lm_studio_unreachable_is_reported_not_swallowed(monkeypatch):
    def refuse(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(server.httpx, "post", refuse)
    with pytest.raises(RuntimeError, match="LM Studio not reachable"):
        score_windows(words_over(300), 30, 6)
