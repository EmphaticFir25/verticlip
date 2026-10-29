import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
import store

client = TestClient(server.app)
WORDS = [{"start": 0.0, "end": 0.5, "word": "hi"}]


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    """Accounts file with one YouTube and one TikTok account. Uploaders are patched per test."""
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({
        "youtube": [{"id": "yt1", "name": "Channel", "refresh_token": "x"}],
        "tiktok": [{"id": "tt1", "name": "Creator", "access_token": "x", "expires_at": 9e12}],
    }))
    monkeypatch.setattr(server, "ACCOUNTS_FILE", path)
    return path


def seed_clip(clip_id="clip1", job_id="job1"):
    store.create_job(job_id, status="done")
    store.add_clips(job_id, [{"id": clip_id, "job_id": job_id, "score": 80, "title": "T",
                              "reason": "r", "file": "x.mp4", "publish": []}])


# ---------- /api/clips validation and caps ----------

def test_rejects_non_http_urls(db):
    for bad in ["file:///C:/secret.txt", "ftp://example.com/v", "--exec=calc", "javascript:alert(1)", ""]:
        r = client.post("/api/clips", json={"url": bad})
        assert r.status_code == 400, bad


def test_caps_clip_len_and_max_clips_on_the_server(db, monkeypatch):
    seen = {}
    monkeypatch.setattr(server, "run_job", lambda job_id, req: seen.update(req=req))
    r = client.post("/api/clips", json={"url": "https://example.com/v", "clip_len": 999, "max_clips": 500})
    assert r.status_code == 200
    assert seen["req"].clip_len == 90
    assert seen["req"].max_clips == 15


def test_floors_clip_len_and_max_clips(db, monkeypatch):
    seen = {}
    monkeypatch.setattr(server, "run_job", lambda job_id, req: seen.update(req=req))
    client.post("/api/clips", json={"url": "https://example.com/v", "clip_len": 1, "max_clips": 0})
    assert seen["req"].clip_len == 15
    assert seen["req"].max_clips == 1


def test_unknown_caption_style_falls_back_to_bold(db, monkeypatch):
    seen = {}
    monkeypatch.setattr(server, "run_job", lambda job_id, req: seen.update(req=req))
    client.post("/api/clips", json={"url": "https://example.com/v", "caption_style": "<script>"})
    assert seen["req"].caption_style == "bold"


def test_job_is_created_and_readable(db, monkeypatch):
    monkeypatch.setattr(server, "run_job", lambda job_id, req: None)
    job_id = client.post("/api/clips", json={"url": "https://example.com/v"}).json()["job_id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "queued"
    assert job["url"] == "https://example.com/v"
    assert client.get("/api/jobs/missing").status_code == 404


# ---------- errors surface stderr, trimmed ----------

def test_missing_ffmpeg_gives_a_clear_message(monkeypatch):
    def not_found(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(server.subprocess, "run", not_found)
    with pytest.raises(RuntimeError, match="ffmpeg not found on PATH"):
        server.run_tool(["ffmpeg", "-version"], label="ffmpeg")


def test_tool_failure_reports_only_the_tail_of_stderr(monkeypatch):
    noisy = "\n".join(f"banner line {i}" for i in range(50)) + "\nERROR: real cause here"
    proc = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=noisy)
    monkeypatch.setattr(server.subprocess, "run", lambda *a, **k: proc)
    with pytest.raises(RuntimeError) as exc:
        server.run_tool(["yt-dlp"], label="yt-dlp")
    msg = str(exc.value)
    assert msg.startswith("yt-dlp failed:")
    assert "ERROR: real cause here" in msg
    assert "banner line 0" not in msg
    assert len(msg) < 700


# ---------- watermark rendering (ffmpeg mocked) ----------

def fake_font(tmp_path):
    font = tmp_path / "arialbd.ttf"
    font.write_bytes(b"not a real font; ffmpeg is mocked")
    return font


def capture_render(monkeypatch, tmp_path, font, logo_url):
    calls = []
    monkeypatch.setattr(server, "resolve_font", lambda: font)
    monkeypatch.setattr(server, "run_tool", lambda cmd, label, cwd=None: calls.append(cmd))
    req = server.ClipRequest(url="https://example.com/v", logo_url=logo_url)
    job_dir = tmp_path / "job"
    job_dir.mkdir(exist_ok=True)
    server.render_clip(Path("src.mp4"), job_dir, 1, {"start": 0, "end": 10, "score": 70}, WORDS, req)
    return calls[0]


def filter_arg(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


def test_text_watermark_with_logo_sits_left_of_the_logo(monkeypatch, tmp_path):
    (server.UPLOADS / "logo_t.png").write_bytes(b"png")
    try:
        cmd = capture_render(monkeypatch, tmp_path, fake_font(tmp_path), "/uploads/logo_t.png")
    finally:
        (server.UPLOADS / "logo_t.png").unlink()
    f = filter_arg(cmd)
    assert "overlay=W-w-40:40" in f
    assert "drawtext=fontfile=" in f and "text=Verticlip" in f
    assert "x=W-text_w-230" in f


def test_text_only_watermark_when_no_logo(monkeypatch, tmp_path):
    cmd = capture_render(monkeypatch, tmp_path, fake_font(tmp_path), None)
    f = filter_arg(cmd)
    assert "overlay" not in f
    assert "x=W-text_w-40:y=40" in f


def test_font_path_escapes_the_drive_colon(monkeypatch, tmp_path):
    cmd = capture_render(monkeypatch, tmp_path, Path("C:/Windows/Fonts/arialbd.ttf"), None)
    assert "fontfile='C\\:/Windows/Fonts/arialbd.ttf'" in filter_arg(cmd)


def test_no_font_renders_without_text_watermark(monkeypatch, tmp_path):
    cmd = capture_render(monkeypatch, tmp_path, None, None)
    assert "drawtext" not in filter_arg(cmd)


def test_resolve_font_prefers_env_then_windows_default(monkeypatch, tmp_path):
    custom = fake_font(tmp_path)
    monkeypatch.setenv("FONT_PATH", str(custom))
    assert server.resolve_font() == custom
    monkeypatch.setenv("FONT_PATH", str(tmp_path / "missing.ttf"))
    assert server.resolve_font() in (None, Path("C:/Windows/Fonts/arialbd.ttf"), Path("C:/Windows/Fonts/arial.ttf"))


# ---------- publishing (uploaders mocked, never a real account) ----------

def test_publish_with_no_accounts_queues_nothing(db, accounts):
    accounts.write_text(json.dumps({"youtube": [], "tiktok": []}))
    seed_clip()
    r = client.post("/api/publish", json={"clip_ids": ["clip1"], "platforms": ["tiktok"]})
    assert r.json()["queued"] == 0


def test_publish_records_each_clip_and_account(db, accounts, monkeypatch):
    seed_clip()
    monkeypatch.setattr(server, "upload_tiktok", lambda clip, acct: "sent to inbox (p1)")
    monkeypatch.setattr(server, "upload_youtube_short", lambda clip, acct: "video v1 (private)")
    r = client.post("/api/publish", json={"clip_ids": ["clip1"]})
    assert r.json()["queued"] == 2
    results = {(p["platform"], p["account_id"]): p for p in store.get_clip("clip1")["publish"]}
    assert results[("tiktok", "tt1")]["status"] == "uploaded"
    assert results[("youtube_shorts", "yt1")]["detail"] == "video v1 (private)"


def test_failed_upload_is_recorded_not_raised(db, accounts, monkeypatch):
    seed_clip()

    def boom(clip, acct):
        raise RuntimeError("TikTok rejected the upload: scope_not_authorized")

    monkeypatch.setattr(server, "upload_tiktok", boom)
    r = client.post("/api/publish", json={"clip_ids": ["clip1"], "platforms": ["tiktok"]})
    assert r.status_code == 200
    result = store.get_clip("clip1")["publish"][0]
    assert result["status"] == "failed"
    assert "scope_not_authorized" in result["detail"]


def test_unknown_platform_is_ignored(db, accounts, monkeypatch):
    seed_clip()
    r = client.post("/api/publish", json={"clip_ids": ["clip1"], "platforms": ["myspace"]})
    assert r.status_code == 400


def test_publish_with_unknown_clip_ids_is_rejected(db, accounts):
    r = client.post("/api/publish", json={"clip_ids": ["nope"]})
    assert r.status_code == 400



# ---------- saved progress: library, delete, pages ----------

def test_jobs_endpoint_lists_saved_jobs(db):
    seed_clip("c9", "job9")
    jobs = client.get("/api/jobs").json()["jobs"]
    assert [j["id"] for j in jobs] == ["job9"]
    assert jobs[0]["clips"][0]["id"] == "c9"


def test_title_and_settings_are_saved_with_the_job(db, monkeypatch):
    monkeypatch.setattr(server, "run_job", lambda job_id, req: None)
    job_id = client.post("/api/clips", json={"url": "https://example.com/v", "title": "  Big stream  ",
                                             "clip_len": 45}).json()["job_id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["title"] == "Big stream"
    assert job["settings"]["clip_len"] == 45


def test_cannot_delete_a_running_job(db):
    store.create_job("a1b2c3d4e5", status="running")
    assert client.delete("/api/jobs/a1b2c3d4e5").status_code == 409
    assert client.delete("/api/jobs/missing").status_code == 404


def test_delete_finished_job(db):
    seed_clip("c1", "a1b2c3d4e5")
    assert client.delete("/api/jobs/a1b2c3d4e5").json() == {"ok": True}
    assert client.get("/api/jobs/a1b2c3d4e5").status_code == 404


def test_every_page_route_serves_html(db):
    for route in server.PAGES:
        r = client.get(route)
        assert r.status_code == 200, route
        assert "text/html" in r.headers["content-type"]
