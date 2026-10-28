"""
Verticlip hosted — backend.

Pipeline per job:
  1. yt-dlp downloads the source (user-supplied URL or a trending pick)
  2. faster-whisper transcribes locally (word timestamps)
  3. LM Studio (OpenAI-compatible, localhost) scores transcript windows for clippable moments
  4. ffmpeg cuts each moment to 9:16, burns captions (ASS), overlays the logo and text watermark
  5. Clips are stored; /api/publish uploads selected clips to every connected account

Run:  uvicorn server:app --reload --port 33214
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import store

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
WEB = ROOT / "web"
DATA = ROOT / "data"
JOBS = DATA / "jobs"
UPLOADS = DATA / "uploads"
ACCOUNTS_FILE = DATA / "accounts.json"
for p in (JOBS, UPLOADS):
    p.mkdir(parents=True, exist_ok=True)

YT_KEY = os.getenv("YOUTUBE_API_KEY", "")
GOOGLE_CLIENT_SECRETS = os.getenv("GOOGLE_CLIENT_SECRETS", str(ROOT / "client_secret.json"))
TIKTOK_CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "")
PUBLIC_BASE = os.getenv("PUBLIC_BASE", "http://localhost:33214")
LMSTUDIO_URL = os.getenv("LMSTUDIO_URL", "http://localhost:1234/v1")
LMSTUDIO_MODEL = os.getenv("LMSTUDIO_MODEL", "local-model")
LMSTUDIO_API_KEY = os.getenv("LMSTUDIO_API_KEY", "")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
YOUTUBE_PRIVACY = os.getenv("YOUTUBE_PRIVACY", "private").strip().lower()
if YOUTUBE_PRIVACY not in ("private", "unlisted", "public"):
    YOUTUBE_PRIVACY = "private"

CLIP_LEN_RANGE = (15, 90)   # seconds
MAX_CLIPS_RANGE = (1, 15)
CAPTION_STYLES = ("bold", "clean", "pop")
PLATFORMS = ("youtube_shorts", "tiktok")
STAGES = ("download", "transcribe", "score", "render")
DEFAULT_FONTS = ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf")
MAX_URL_LEN = 2048

app = FastAPI(title="Verticlip")
app.mount("/static", StaticFiles(directory=WEB), name="static")
app.mount("/media", StaticFiles(directory=JOBS), name="media")
app.mount("/uploads", StaticFiles(directory=UPLOADS), name="uploads")


# ---------- helpers ----------

def load_accounts() -> dict:
    if ACCOUNTS_FILE.exists():
        return json.loads(ACCOUNTS_FILE.read_text())
    return {"youtube": [], "tiktok": []}


def save_accounts(data: dict) -> None:
    ACCOUNTS_FILE.write_text(json.dumps(data, indent=2))


def clamp(value: int, bounds: tuple[int, int]) -> int:
    lo, hi = bounds
    return max(lo, min(hi, int(value)))


def trim_stderr(text: str, limit: int = 600) -> str:
    """Keep the last few non-empty lines of a tool's stderr; that is where the error is."""
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return "\n".join(lines[-8:])[-limit:]


def run_tool(cmd: list[str], label: str, cwd: Optional[Path] = None) -> None:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise RuntimeError(f"{cmd[0]} not found on PATH. See README troubleshooting.")
    if proc.returncode != 0:
        raise RuntimeError(f"{label} failed: {trim_stderr(proc.stderr)}")


def resolve_font() -> Optional[Path]:
    """Font for the text watermark: FONT_PATH from .env, then Windows defaults."""
    for candidate in [os.getenv("FONT_PATH", "").strip(), *DEFAULT_FONTS]:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def ff_path(path: Path) -> str:
    """Escape a path for use inside an ffmpeg filter option (the drive colon needs a backslash)."""
    return path.as_posix().replace(":", r"\:")


# ---------- pages ----------

@app.get("/", response_class=HTMLResponse)
def index():
    return (WEB / "index.html").read_text(encoding="utf-8")


# ---------- trending ----------

@app.get("/api/trending")
def trending(region: str = "US", limit: int = 50):
    """Most-viewed videos published in the last 7 days, via the YouTube Data API."""
    if not YT_KEY:
        raise HTTPException(500, "Set YOUTUBE_API_KEY to load trending videos.")
    published_after = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7 * 86400))
    with httpx.Client(timeout=20) as c:
        s = c.get("https://www.googleapis.com/youtube/v3/search", params={
            "part": "snippet", "type": "video", "order": "viewCount",
            "regionCode": region, "publishedAfter": published_after,
            "videoDuration": "long", "maxResults": min(limit, 50), "key": YT_KEY,
        }).json()
        ids = [i["id"]["videoId"] for i in s.get("items", [])]
        if not ids:
            return {"items": []}
        v = c.get("https://www.googleapis.com/youtube/v3/videos", params={
            "part": "snippet,statistics", "id": ",".join(ids), "key": YT_KEY,
        }).json()
    items = []
    for it in v.get("items", []):
        items.append({
            "id": it["id"],
            "title": it["snippet"]["title"],
            "channel": it["snippet"]["channelTitle"],
            "thumbnail": it["snippet"]["thumbnails"].get("medium", {}).get("url", ""),
            "views": int(it.get("statistics", {}).get("viewCount", 0)),
        })
    items.sort(key=lambda x: x["views"], reverse=True)
    return {"items": items}


# ---------- watermark ----------

@app.post("/api/logo")
async def upload_logo(file: UploadFile = File(...)):
    ext = Path(file.filename or "logo.png").suffix or ".png"
    name = f"logo_{uuid.uuid4().hex[:8]}{ext}"
    (UPLOADS / name).write_bytes(await file.read())
    return {"url": f"/uploads/{name}", "path": str(UPLOADS / name)}


# ---------- analysis pipeline ----------

class ClipRequest(BaseModel):
    url: str
    clip_len: int = 30
    max_clips: int = 6
    caption_style: str = "bold"
    auto_caption: bool = True
    logo_url: Optional[str] = None


def validate_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if len(url) > MAX_URL_LEN or parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(400, "Enter a full http:// or https:// video URL.")
    return url


@app.post("/api/clips")
def create_clips(req: ClipRequest, bg: BackgroundTasks):
    req = ClipRequest(
        url=validate_url(req.url),
        clip_len=clamp(req.clip_len, CLIP_LEN_RANGE),
        max_clips=clamp(req.max_clips, MAX_CLIPS_RANGE),
        caption_style=req.caption_style if req.caption_style in CAPTION_STYLES else "bold",
        auto_caption=req.auto_caption,
        logo_url=req.logo_url,
    )
    job_id = uuid.uuid4().hex[:10]
    store.create_job(job_id, status="queued", stage="", progress=0, message="Queued",
                     url=req.url)
    bg.add_task(run_job, job_id, req)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job")
    return job


def download(url: str, out_dir: Path) -> Path:
    """Download the source video with yt-dlp. Only use on content you have rights to clip."""
    out = out_dir / "source.mp4"
    run_tool([
        sys.executable, "-m", "yt_dlp", "-f", "bv*[height<=1080]+ba/b",
        "--merge-output-format", "mp4", "--no-playlist", "-o", str(out), url,
    ], label="yt-dlp")
    if not out.exists():
        raise RuntimeError("yt-dlp finished but produced no video file.")
    return out


def transcribe(video: Path) -> list[dict]:
    """Local word-level transcription with faster-whisper."""
    from faster_whisper import WhisperModel
    model = WhisperModel(WHISPER_MODEL, device="auto", compute_type="int8")
    segments, _ = model.transcribe(str(video), word_timestamps=True, vad_filter=True)
    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append({"start": w.start, "end": w.end, "word": w.word.strip()})
    return words


def parse_clips(content: str) -> list[dict]:
    """Pull the JSON object out of a model reply and keep only well-formed clip entries."""
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model reply")
    data = json.loads(content[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("model reply is not a JSON object")
    clips = []
    for c in data.get("clips", []):
        if not isinstance(c, dict) or "start" not in c or "end" not in c:
            continue
        try:
            float(c["start"]), float(c["end"])
        except (TypeError, ValueError):
            continue
        clips.append(c)
    return clips


def _chat(messages: list[dict]) -> str:
    """One chat completion against LM Studio. Returns "" for a malformed reply so the caller retries."""
    try:
        r = httpx.post(f"{LMSTUDIO_URL}/chat/completions", json={
            "model": LMSTUDIO_MODEL, "messages": messages, "temperature": 0.2,
        }, headers={"Authorization": f"Bearer {LMSTUDIO_API_KEY}"} if LMSTUDIO_API_KEY else None,
            timeout=300)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"LM Studio not reachable at {LMSTUDIO_URL} ({type(exc).__name__}). "
            "Start its local server and load a chat model."
        ) from exc
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        return ""


def pick_moments(candidates: list[dict], clip_len: int, max_clips: int) -> list[dict]:
    """Best-scoring, non-overlapping moments, each clamped to clip_len and at least 8s long."""
    candidates = sorted(candidates, key=lambda x: float(x.get("score", 0)), reverse=True)
    picked: list[dict] = []
    for cand in candidates:
        s = float(cand["start"])
        e = min(float(cand["end"]), s + clip_len)
        if e - s < 8:
            continue
        if any(not (e <= p["start"] or s >= p["end"]) for p in picked):
            continue
        picked.append({**cand, "start": s, "end": e})
        if len(picked) >= max_clips:
            break
    return picked


def score_windows(words: list[dict], clip_len: int, max_clips: int,
                  chat: Callable[[list[dict]], str] = _chat) -> list[dict]:
    """Ask the local model to find clippable moments in 10-minute transcript windows.
    Each window gets two attempts; a window that still returns invalid JSON is skipped."""
    if not words:
        return []
    window_s = 600  # 10-minute windows keep prompts small for local models
    candidates: list[dict] = []
    t_end = words[-1]["end"]
    start = words[0]["start"]
    while start < t_end:
        end = start + window_s
        chunk = [w for w in words if start <= w["start"] < end]
        if chunk:
            text = " ".join(f"[{w['start']:.1f}]{w['word']}" for w in chunk)[:24000]
            prompt = (
                f"Below is a timestamped transcript. Find up to 4 moments that would make a "
                f"standalone viral vertical short of about {clip_len} seconds: a strong hook, "
                f"a surprising claim, emotion, humor, or a complete mini-story. "
                f"Reply with JSON only, shaped as "
                f'{{"clips": [{{"start": <seconds>, "end": <seconds>, "score": <0-100>, '
                f'"title": "<short title>", "caption": "<3-7 words for on-screen caption>", '
                f'"reason": "<one sentence>"}}]}}\n\nTRANSCRIPT:\n{text}'
            )
            messages = [
                {"role": "system", "content": "You are an expert short-form video editor. Output valid JSON only."},
                {"role": "user", "content": prompt},
            ]
            for attempt in range(2):
                try:
                    candidates += parse_clips(chat(messages))
                    break
                except ValueError:
                    if attempt == 1:
                        print(f"Skipping window at {start:.0f}s: model returned invalid JSON twice")
        start = end
    return pick_moments(candidates, clip_len, max_clips)


def ass_for(words: list[dict], clip_start: float, clip_end: float, style: str) -> str:
    """Build an ASS subtitle file with captions timed to the clip."""
    if style == "pop":
        primary, outline, box = "&H00FFFFFF", "&H00000000", 0
        chunk_n = 1
    elif style == "clean":
        primary, outline, box = "&H00FFFFFF", "&H00000000", 0
        chunk_n = 4
    else:  # bold highlight
        primary, outline, box = "&H00000000", "&H0000B8F5", 3  # amber box
        chunk_n = 3
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV
Style: Cap,Arial Black,72,{primary},&H000000FF,{outline},&H64000000,1,0,{box},6,0,2,60,60,380

[Events]
Format: Layer, Start, End, Style, Text
"""
    def ts(t: float) -> str:
        h, rem = divmod(t, 3600); m, s = divmod(rem, 60)
        return f"{int(h)}:{int(m):02d}:{s:05.2f}"

    def ass_word(w: str) -> str:
        # Braces start ASS override tags and backslashes escape; strip them from spoken text.
        return re.sub(r"[{}\\\r\n]", "", w).strip()

    inside = [w for w in words if clip_start <= w["start"] < clip_end]
    lines = []
    for i in range(0, len(inside), chunk_n):
        group = inside[i:i + chunk_n]
        s = group[0]["start"] - clip_start
        e = group[-1]["end"] - clip_start
        text = " ".join(ass_word(w["word"]) for w in group).upper()
        lines.append(f"Dialogue: 0,{ts(max(s, 0))},{ts(e)},Cap,{text}")
    return header + "\n".join(lines) + "\n"


def build_filters(logo_path: Optional[Path], font: Optional[Path]) -> tuple[list[str], str]:
    """filter_complex chain: 9:16 crop, logo top-right, text watermark, captions.
    Returns the filter parts and the final video label."""
    filters = ["[0:v]scale=-2:1920,crop=1080:1920,setsar=1[v0]"]
    last = "[v0]"
    if logo_path:
        filters.append("[1:v]scale=170:-1[lg]")
        filters.append(f"{last}[lg]overlay=W-w-40:40[v1]")
        last = "[v1]"
    if font:
        # Beside the logo when there is one (vertically centred on it), otherwise top-right alone.
        x = "W-text_w-230" if logo_path else "W-text_w-40"
        y = "40+(170-text_h)/2" if logo_path else "40"
        filters.append(
            f"{last}drawtext=fontfile='{ff_path(font)}':text=Verticlip:fontsize=44"
            f":fontcolor=white:borderw=4:bordercolor=black@0.8:x={x}:y={y}[vt]"
        )
        last = "[vt]"
    return filters, last


def render_clip(source: Path, job_dir: Path, idx: int, moment: dict, words: list[dict],
                req: ClipRequest) -> dict:
    """Cut to 9:16, burn captions, overlay logo and text watermark."""
    s, e = moment["start"], moment["end"]
    out = job_dir / f"clip_{idx}.mp4"
    logo_path = None
    if req.logo_url:
        # logo_url is "/uploads/<name>"; resolve the name inside UPLOADS only.
        candidate = UPLOADS / Path(req.logo_url).name
        logo_path = candidate if candidate.is_file() else None
    font = resolve_font()
    if font is None:
        print("No watermark font found; set FONT_PATH in .env. Rendering without text watermark.")
    cmd = ["ffmpeg", "-y", "-ss", f"{s:.2f}", "-t", f"{e - s:.2f}", "-i", str(source)]
    if logo_path:
        cmd += ["-i", str(logo_path)]
    if req.auto_caption:
        (job_dir / f"cap_{idx}.ass").write_text(ass_for(words, s, e, req.caption_style), encoding="utf-8")
    filters, last = build_filters(logo_path, font)
    if req.auto_caption:
        filters.append(f"{last}subtitles=cap_{idx}.ass[v2]")
        last = "[v2]"
    cmd += ["-filter_complex", ";".join(filters), "-map", last, "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac",
            "-movflags", "+faststart", out.name]
    run_tool(cmd, label="ffmpeg", cwd=job_dir)
    return {
        "id": uuid.uuid4().hex[:10],
        "job_id": job_dir.name,
        "file": str(out),
        "preview_url": f"/media/{job_dir.name}/{out.name}",
        "start": s, "end": e,
        "score": float(moment.get("score", 0)),
        "title": moment.get("title", f"Clip {idx}"),
        "caption": moment.get("caption", "") if req.auto_caption else "",
        "reason": moment.get("reason", ""),
        "watermark": "Verticlip",
        "publish": [],
    }


def run_job(job_id: str, req: ClipRequest) -> None:
    job_dir = JOBS / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        store.update_job(job_id, status="running", stage="download", progress=10,
                         message="Downloading source…")
        source = download(req.url, job_dir)

        store.update_job(job_id, stage="transcribe", progress=30,
                         message="Transcribing locally (Whisper)…")
        words = transcribe(source)

        store.update_job(job_id, stage="score", progress=55,
                         message="Scoring moments with LM Studio…")
        moments = score_windows(words, req.clip_len, req.max_clips, chat=_chat)
        if not moments:
            raise RuntimeError("The model found no clippable moments.")

        clips = []
        for i, m in enumerate(moments, 1):
            store.update_job(job_id, stage="render", progress=55 + int(40 * i / len(moments)),
                             message=f"Rendering clip {i} of {len(moments)}…")
            clips.append(render_clip(source, job_dir, i, m, words, req))
        store.add_clips(job_id, clips)
        store.update_job(job_id, status="done", stage="done", progress=100, message="Done")
    except Exception as exc:  # surface any failure to the UI
        store.update_job(job_id, status="error", message=str(exc))


# ---------- accounts & OAuth ----------

@app.get("/api/accounts")
def list_accounts():
    return load_accounts()


@app.delete("/api/accounts/{account_id}")
def remove_account(account_id: str):
    data = load_accounts()
    for plat in ("youtube", "tiktok"):
        data[plat] = [a for a in data[plat] if a["id"] != account_id]
    save_accounts(data)
    return {"ok": True}


YT_PENDING: dict[str, object] = {}
TT_PENDING: dict[str, str] = {}


@app.get("/auth/youtube/start")
def youtube_start():
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_secrets_file(
        GOOGLE_CLIENT_SECRETS,
        scopes=["https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly"],
        redirect_uri=f"{PUBLIC_BASE}/auth/youtube/callback",
    )
    url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    YT_PENDING[state] = flow
    return RedirectResponse(url)


@app.get("/auth/youtube/callback")
def youtube_callback(request: Request, state: str, code: str):
    from googleapiclient.discovery import build
    flow = YT_PENDING.pop(state, None)
    if flow is None:
        raise HTTPException(400, "Unknown OAuth state. Start the YouTube connect again from the app.")
    flow.fetch_token(code=code)
    creds = flow.credentials
    yt = build("youtube", "v3", credentials=creds)
    me = yt.channels().list(part="snippet", mine=True).execute()["items"][0]
    data = load_accounts()
    entry = {
        "id": me["id"], "name": me["snippet"]["title"],
        "refresh_token": creds.refresh_token,
    }
    data["youtube"] = [a for a in data["youtube"] if a["id"] != me["id"]] + [entry]
    save_accounts(data)
    return RedirectResponse("/#accounts")


@app.get("/auth/tiktok/start")
def tiktok_start():
    state = uuid.uuid4().hex
    TT_PENDING[state] = state
    qs = httpx.QueryParams({
        "client_key": TIKTOK_CLIENT_KEY,
        "scope": "user.info.basic,video.upload",
        "response_type": "code",
        "redirect_uri": f"{PUBLIC_BASE}/auth/tiktok/callback",
        "state": state,
    })
    return RedirectResponse(f"https://www.tiktok.com/v2/auth/authorize/?{qs}")


@app.get("/auth/tiktok/callback")
def tiktok_callback(code: str, state: str):
    if TT_PENDING.pop(state, None) is None:
        raise HTTPException(400, "Bad state")
    with httpx.Client(timeout=20) as c:
        tok = c.post("https://open.tiktokapis.com/v2/oauth/token/", data={
            "client_key": TIKTOK_CLIENT_KEY, "client_secret": TIKTOK_CLIENT_SECRET,
            "code": code, "grant_type": "authorization_code",
            "redirect_uri": f"{PUBLIC_BASE}/auth/tiktok/callback",
        }).json()
        info = c.get("https://open.tiktokapis.com/v2/user/info/",
                     params={"fields": "open_id,display_name"},
                     headers={"Authorization": f"Bearer {tok['access_token']}"}).json()
    user = info["data"]["user"]
    data = load_accounts()
    entry = {
        "id": user["open_id"], "name": user["display_name"],
        "access_token": tok["access_token"], "refresh_token": tok["refresh_token"],
        "expires_at": time.time() + tok["expires_in"],
    }
    data["tiktok"] = [a for a in data["tiktok"] if a["id"] != user["open_id"]] + [entry]
    save_accounts(data)
    return RedirectResponse("/#accounts")


# ---------- publishing ----------
# Uploads only start from this endpoint, which the UI calls on an explicit click.

class PublishRequest(BaseModel):
    clip_ids: list[str]
    platforms: list[str] = ["youtube_shorts", "tiktok"]


@app.post("/api/publish")
def publish(req: PublishRequest, bg: BackgroundTasks):
    clips = [c for c in (store.get_clip(cid) for cid in req.clip_ids) if c]
    platforms = [p for p in req.platforms if p in PLATFORMS]
    if not clips or not platforms:
        raise HTTPException(400, "No valid clips or platforms selected.")
    accounts = load_accounts()
    queued = 0
    for clip in clips:
        if "youtube_shorts" in platforms:
            for acct in accounts["youtube"]:
                _queue(bg, clip, "youtube_shorts", acct, upload_youtube_short)
                queued += 1
        if "tiktok" in platforms:
            for acct in accounts["tiktok"]:
                _queue(bg, clip, "tiktok", acct, upload_tiktok)
                queued += 1
    return {"message": f"Queued {queued} upload(s) across {len(accounts['youtube']) + len(accounts['tiktok'])} account(s).",
            "queued": queued}


def _queue(bg: BackgroundTasks, clip: dict, platform: str, acct: dict, worker) -> None:
    store.set_publish_result(clip["id"], platform, acct["id"],
                             account_name=acct.get("name", ""), status="queued", detail="")
    bg.add_task(_run_upload, worker, clip["id"], platform, acct)


def _run_upload(worker, clip_id: str, platform: str, acct: dict) -> None:
    """Run one upload and record its outcome on the clip. Never raises."""
    clip = store.get_clip(clip_id)
    try:
        detail = worker(clip, acct)
        store.set_publish_result(clip_id, platform, acct["id"], status="uploaded", detail=detail)
    except Exception as exc:
        store.set_publish_result(clip_id, platform, acct["id"], status="failed",
                                 detail=trim_stderr(str(exc), 300))


def upload_youtube_short(clip: dict, acct: dict) -> str:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GRequest
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    secrets = json.loads(Path(GOOGLE_CLIENT_SECRETS).read_text())["web"]
    creds = Credentials(None, refresh_token=acct["refresh_token"], token_uri=secrets["token_uri"],
                        client_id=secrets["client_id"], client_secret=secrets["client_secret"])
    creds.refresh(GRequest())
    yt = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {"title": (clip["title"] + " #Shorts")[:100], "description": clip["reason"] + "\n\n#Shorts",
                    "categoryId": "22"},
        "status": {"privacyStatus": YOUTUBE_PRIVACY, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(clip["file"], chunksize=-1, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return f"video {resp['id']} ({YOUTUBE_PRIVACY})"


def upload_tiktok(clip: dict, acct: dict) -> str:
    """Content Posting API, inbox mode: the video lands in the creator's TikTok inbox
    to finish and post. Direct posting needs TikTok's app audit."""
    if acct.get("expires_at", 0) < time.time():
        raise RuntimeError("TikTok access token expired. Reconnect this account.")
    size = Path(clip["file"]).stat().st_size
    with httpx.Client(timeout=120) as c:
        init = c.post("https://open.tiktokapis.com/v2/post/publish/inbox/video/init/",
                      headers={"Authorization": f"Bearer {acct['access_token']}",
                               "Content-Type": "application/json; charset=UTF-8"},
                      json={"source_info": {"source": "FILE_UPLOAD", "video_size": size,
                                            "chunk_size": size, "total_chunk_count": 1}}).json()
        err = init.get("error", {})
        if err.get("code") not in (None, "ok"):
            raise RuntimeError(f"TikTok rejected the upload: {err.get('message') or err.get('code')}")
        upload_url = init["data"]["upload_url"]
        with open(clip["file"], "rb") as f:
            put = c.put(upload_url, content=f.read(), headers={
                "Content-Type": "video/mp4",
                "Content-Range": f"bytes 0-{size - 1}/{size}",
                "Content-Length": str(size),
            })
        if not 200 <= put.status_code < 300:
            raise RuntimeError(f"TikTok upload failed with HTTP {put.status_code}.")
    return f"sent to inbox ({init['data'].get('publish_id', '')})"
