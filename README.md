# Verticlip

Turn long videos into vertical Shorts. Verticlip finds the most clippable moments with a local AI model, adds captions and your logo watermark, and uploads clips to every connected YouTube and TikTok account.

## Policy pages (for TikTok app review)

- Terms of Service: https://emphaticfir25.github.io/verticlip/terms.html
- Privacy Policy: https://emphaticfir25.github.io/verticlip/privacy.html

These are served from `docs/` through GitHub Pages.

## Quick start (Windows)

1. Install Python 3.10+ (check "Add to PATH") and ffmpeg (`ffmpeg -version` must work in a new terminal).
2. Start LM Studio's local server with a chat model loaded.
3. Double-click `run.bat`. On first run it creates `.env` from `.env.example`. Fill in your keys, then run it again.
4. The app opens at http://localhost:33214. Paste a video URL and click **Find clips**.

Nothing publishes automatically. Uploads start only when you select clips and click **Publish to all connected accounts**.

## Setup (manual)

Requirements: Python 3.10+, ffmpeg on PATH, LM Studio running its local server (default `http://localhost:1234/v1`) with a chat model loaded.

```bash
pip install -r requirements.txt
```

Create a `.env` file (never commit it). `.env.example` lists every key:

```
YOUTUBE_API_KEY=...
GOOGLE_CLIENT_SECRETS=client_secret.json
TIKTOK_CLIENT_KEY=...
TIKTOK_CLIENT_SECRET=...
PUBLIC_BASE=http://localhost:33214
LMSTUDIO_URL=http://localhost:1234/v1
LMSTUDIO_MODEL=your-model-name
WHISPER_MODEL=small
YOUTUBE_PRIVACY=private
FONT_PATH=
```

- `YOUTUBE_PRIVACY`: `private` (default), `unlisted`, or `public`. Keep it `private` until you trust the output.
- `FONT_PATH`: font for the "Verticlip" text watermark. Empty uses `C:\Windows\Fonts\arialbd.ttf`, then `arial.ttf`.

Run:

```bash
uvicorn server:app --host 127.0.0.1 --port 33214
```

Open http://localhost:33214.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests mock LM Studio, ffmpeg's job pipeline, and the YouTube and TikTok uploaders. They never call a real account. They use a temporary SQLite file, not `data/verticlip.db`.

## Project layout

- `server.py`: FastAPI backend (trending, analysis, rendering, OAuth, publishing)
- `store.py`: SQLite store for jobs, clips, and publish results (`data/verticlip.db`)
- `web/index.html`: front end
- `tests/`: pytest suite
- `docs/`: Terms and Privacy pages for GitHub Pages
- `assets/`: logo files
- `data/`: jobs, uploads, database, and connected accounts (created at runtime, git-ignored)

## Troubleshooting (Windows)

### `ffmpeg not found on PATH`

The render step cannot find `ffmpeg.exe`.

1. Install it: `winget install Gyan.FFmpeg`.
2. Close and reopen the terminal or `run.bat` window. PATH changes do not reach open windows.
3. Check it in a new terminal: `ffmpeg -version`.

### `LM Studio not reachable at http://localhost:1234/v1`

The job stops at **Scoring moments**.

1. In LM Studio, open the **Developer** tab and click **Start Server**.
2. Load a chat model. The server only answers once a model is loaded.
3. Check it in a browser: http://localhost:1234/v1/models should list your model.
4. If LM Studio uses another port, set `LMSTUDIO_URL` in `.env` to match, and keep the `/v1` suffix.

### `yt-dlp failed: ... ERROR ...` on a URL

The stderr tail in the job message names the cause.

- `Unsupported URL` or `Unable to download webpage: HTTP Error 404`: the link is wrong, private, or deleted. Test the link in a browser first.
- `Sign in to confirm your age` or `Video unavailable`: the video needs a login or is blocked in your region. Verticlip cannot download it.
- `HTTP Error 403` or a "nsig" or "signature" error: yt-dlp is out of date. Run `.venv\Scripts\python -m pip install -U yt-dlp`, then restart the app.
- Playlist links download only the one video, because playlists are off.

### OAuth redirect mismatch (`redirect_uri_mismatch` or "Unknown OAuth state")

- **Google (YouTube):** in Google Cloud Console, add this exact Authorized redirect URI to your OAuth client: `http://localhost:33214/auth/youtube/callback`. It must match `PUBLIC_BASE` in `.env` plus `/auth/youtube/callback`, character for character.
- **TikTok:** register `http://localhost:33214/auth/tiktok/callback` on your TikTok app.
- Open the app at the same host as `PUBLIC_BASE`. Use `localhost` in both places, not `127.0.0.1` in one and `localhost` in the other.
- "Unknown OAuth state": the connect step was started in one server session and the callback arrived after a restart, or the page was reloaded. Click **Connect** again from the app.

### Text watermark missing

The server log says `No watermark font found`. Set `FONT_PATH` in `.env` to a `.ttf` file, for example `C:\Windows\Fonts\arial.ttf`. The logo still renders without it.

### Whisper or `av` error during transcription

`faster-whisper` needs `av` below version 14. Reinstall the pinned requirements: `pip install -r requirements.txt`.

### TikTok shows "sent to inbox" but nothing appears

Inbox uploads wait in the creator's TikTok inbox. Open the TikTok app and finish the post there.

### TikTok "access token expired"

Click **Remove** on the TikTok account in the app, then connect it again.

## Notes

- Only clip content you own or have permission to use.
- TikTok uploads go to the creator's inbox until the app passes TikTok's review for direct posting.
- Each clip's publish result (uploaded, failed, and why) is shown on the clip card.
