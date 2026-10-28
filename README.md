# Verticlip

Turn long videos into vertical Shorts. Verticlip finds the most clippable moments with a local AI model, adds captions and your logo watermark, and uploads clips to every connected YouTube and TikTok account.

## Policy pages (for TikTok app review)

- Terms of Service: https://emphaticfir25.github.io/verticlip/terms.html
- Privacy Policy: https://emphaticfir25.github.io/verticlip/privacy.html

These are served from `docs/` through GitHub Pages.

## Setup

Requirements: Python 3.10+, ffmpeg on PATH, LM Studio running its local server (default `http://localhost:1234/v1`) with a chat model loaded.

```bash
pip install -r requirements.txt
```

Create a `.env` file (never commit it) with:

```
YOUTUBE_API_KEY=...
GOOGLE_CLIENT_SECRETS=client_secret.json
TIKTOK_CLIENT_KEY=...
TIKTOK_CLIENT_SECRET=...
PUBLIC_BASE=http://localhost:8000
LMSTUDIO_URL=http://localhost:1234/v1
LMSTUDIO_MODEL=your-model-name
WHISPER_MODEL=small
```

Run:

```bash
uvicorn server:app --port 8000
```

Open http://localhost:8000.

## Project layout

- `server.py`: FastAPI backend (trending, analysis, rendering, OAuth, publishing)
- `web/index.html`: front end
- `docs/`: Terms and Privacy pages for GitHub Pages
- `assets/`: logo files
- `data/`: jobs, uploads, and connected accounts (created at runtime, git-ignored)

## Notes

- Only clip content you own or have permission to use.
- TikTok uploads go to the creator's inbox until the app passes TikTok's review for direct posting.
