from server import ass_for

WORDS = [
    {"start": 0.0, "end": 0.4, "word": "hello"},
    {"start": 0.5, "end": 0.9, "word": "there"},
    {"start": 1.0, "end": 1.4, "word": "friend"},
    {"start": 1.5, "end": 1.9, "word": "again"},
    {"start": 20.0, "end": 20.4, "word": "outside"},
]


def dialogue(ass: str) -> list[str]:
    return [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]


def test_header_is_vertical_1080x1920():
    ass = ass_for(WORDS, 0, 10, "bold")
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Style: Cap," in ass


def test_only_words_inside_clip_window_are_captioned():
    lines = dialogue(ass_for(WORDS, 0, 10, "pop"))
    assert len(lines) == 4
    assert all("OUTSIDE" not in ln for ln in lines)


def test_times_are_relative_to_clip_start():
    lines = dialogue(ass_for(WORDS, 1.0, 10, "pop"))
    assert lines[0] == "Dialogue: 0,0:00:00.00,0:00:00.40,Cap,FRIEND"


def test_chunk_size_follows_caption_style():
    assert len(dialogue(ass_for(WORDS, 0, 10, "pop"))) == 4      # one word per line
    assert len(dialogue(ass_for(WORDS, 0, 10, "clean"))) == 1    # four words per line
    assert len(dialogue(ass_for(WORDS, 0, 10, "bold"))) == 2     # three words per line


def test_caption_text_is_uppercase():
    texts = [ln.split(",")[-1] for ln in dialogue(ass_for(WORDS, 0, 10, "clean"))]
    assert texts == ["HELLO THERE FRIEND AGAIN"]


def test_override_tag_characters_are_stripped_from_spoken_text():
    evil = [{"start": 0.0, "end": 0.5, "word": "{\\an8}hi\\N"}]
    line = dialogue(ass_for(evil, 0, 5, "pop"))[0]
    assert line.endswith(",Cap,AN8HIN")


def test_empty_window_has_header_only():
    ass = ass_for(WORDS, 50, 60, "bold")
    assert dialogue(ass) == []
    assert "[Events]" in ass
