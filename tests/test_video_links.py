"""JioHotstar is accepted end-to-end through preview and job creation, while yt-dlp gets its supported Hotstar URL.

No Hotstar account, external request or real video is needed: the extractor is replaced with a local fake, and the
real media, URL allowlist, API validation and job-creation route are exercised."""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

base = Path(tempfile.mkdtemp(prefix="pc-links-"))
cookie_file = base / "hotstar-cookies.txt"
cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
os.environ["ANHY_DATA"] = str(base / "data")
os.environ.update(DATABASE_URL="", ANHY_ACCOUNTS="0", ANHY_VERIFY_EMAIL="0",
                  ANHY_PUBLIC_URL="http://127.0.0.1:8765", ANHY_FETCH_MODEL="0",
                  ANHY_AI_KEY="", ANHY_AI_KEY_FILE="", ANHY_HOTSTAR_COOKIES_FILE=str(cookie_file))
for name in ("ANHY_ALLOWED_SITES", "PEAKCUT_ALLOWED_SITES"):
    os.environ.pop(name, None)
sys.path.insert(0, ".")

from fastapi.testclient import TestClient  # noqa: E402
from yt_dlp.extractor.hotstar import HotStarIE  # noqa: E402
from anhy import config, db, jobs, media, server, tools  # noqa: E402

db.init()
tools._seen.update(at=time.time(), missing=[])
BASE = "http://127.0.0.1:8765"
client = TestClient(server.app, base_url=BASE, follow_redirects=False)
fails = []


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    ok or fails.append(name)


url = "https://www.jiohotstar.com/in/movies/sample-title/1234567890"
canonical = "https://www.hotstar.com/in/movies/sample-title/1234567890"
extract_calls = []
download_calls = []
download_options = []
queued = []
created = []
real_ydl, real_put = media._ydl, jobs._q.put


class FakeYDL:
    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def extract_info(self, got_url, download=False):
        extract_calls.append((got_url, download, self.options.copy()))
        return {"title": "Sample JioHotstar video", "uploader": "Sample channel", "duration": 42,
                "is_live": False, "thumbnail": "", "_type": "video"}

    def download(self, urls):
        download_calls.extend(urls)
        download_options.append(self.options.copy())
        template = self.options["outtmpl"]
        ext = "m4a" if "audio_src" in template else "mp4"
        Path(template.replace("%(ext)s", ext)).write_bytes(b"synthetic download")


try:
    media._ydl = lambda opts: FakeYDL(opts)
    jobs._q.put = lambda *args, **kwargs: queued.append((args, kwargs))

    check("the default allowlist includes Hotstar and JioHotstar",
          {"hotstar.com", "jiohotstar.com"}.issubset(set(config.ALLOWED_SITES)))
    check("JioHotstar links and their subdomains are accepted",
          media.site_allowed(url) and media.site_allowed("https://ott.jiohotstar.com/in/movies/x/1234567890"), True)
    check("legacy Hotstar links are accepted", media.site_allowed("https://www.hotstar.com/in/movies/x/1234567890"), True)
    check("lookalike domains and non-web schemes are still refused",
          (media.site_allowed("https://jiohotstar.com.attacker.example/video"),
           media.site_allowed("ftp://jiohotstar.com/video")), (False, False))
    check("JioHotstar URLs map to yt-dlp's Hotstar extractor without changing the path or query",
          media._extractor_url(url + "?ref=share"), canonical + "?ref=share")
    check("ordinary Hotstar URLs are left unchanged",
          media._extractor_url("https://www.hotstar.com/in/movies/x/1234567890"),
          "https://www.hotstar.com/in/movies/x/1234567890")
    check("the installed yt-dlp Hotstar extractor recognizes the mapped URL",
          HotStarIE.suitable(canonical), True)
    page = Path("web/index.html").read_text(encoding="utf-8")
    check("the browser composer recognizes and advertises JioHotstar video links",
          "jiohotstar" in page and "Paste a YouTube, Twitch, Kick or JioHotstar video link" in page
          and "(?:hotstar|jiohotstar)\\.com" in page, True)

    # A browser's normal link-preview request uses the actual API route and the real media.preview/info chain.
    preview = client.get("/api/preview", params={"url": url})
    check("the JioHotstar preview endpoint returns video metadata", (preview.status_code, preview.json().get("title")),
          (200, "Sample JioHotstar video"))
    check("preview sends the mapped address and configured cookies only to yt-dlp",
          (extract_calls[0][0], extract_calls[0][1], extract_calls[0][2].get("cookiefile")),
          (canonical, False, str(cookie_file)))
    refused = client.get("/api/preview", params={"url": "https://jiohotstar.com.attacker.example/video"})
    check("the preview endpoint rejects a JioHotstar lookalike host", refused.status_code, 400)
    channel = client.get("/api/channel-preview", params={"url": url})
    check("video-only JioHotstar links are not treated as live creator channels", channel.status_code, 400)

    # The same link can proceed through the API to a queued clipping job without losing the selected settings.
    response = client.post("/api/jobs", json={"url": url, "options": {"layout": "split"}}, headers={"Origin": BASE})
    job = response.json() if response.status_code == 200 else {}
    if job.get("id"):
        created.append(job["id"])
    check("a valid JioHotstar link creates a clipping job", response.status_code, 200)
    check("job creation preserves the original link and chosen framing",
          (job.get("source", {}).get("url"), (job.get("options") or {}).get("layout")), (url, "split"))
    check("the end-to-end API queues that job once", len(queued), 1)

    # Audio and video downloads use the same URL mapping, not just the metadata check.
    download_dir = base / "download"
    download_dir.mkdir()
    audio = media.download_audio(url, download_dir)
    video = media.download(url, download_dir)
    section_dir = download_dir / "clips"
    section = media.download_section(url, section_dir, "section.src", 3, 9)
    check("audio download calls yt-dlp with the mapped Hotstar URL", download_calls[0], canonical)
    check("whole-video download calls yt-dlp with the mapped Hotstar URL", download_calls[1], canonical)
    check("clip-section download calls yt-dlp with the mapped Hotstar URL", download_calls[2], canonical)
    check("the download helpers return their generated files",
          (audio.name, video.name, section.name), ("audio_src.m4a", "source.mp4", "section.src.mp4"))
    media.info("https://www.youtube.com/watch?v=abcdefghijk")
    check("the configured cookie file is passed to JioHotstar only",
          (all(x[2].get("cookiefile") == str(cookie_file) for x in extract_calls[:-1]),
           "cookiefile" not in extract_calls[-1][2],
           len(download_options) == 3 and all(x.get("cookiefile") == str(cookie_file) for x in download_options)),
          (True, True, True))

    # Verify the common logged-out failure produces actionable guidance, not a vague preview error.
    class LoginRequiredYDL(FakeYDL):
        def extract_info(self, got_url, download=False):
            raise RuntimeError("This video is only available for registered users. Use --cookies for authentication")

    media._PREVIEWS.pop("https://www.jiohotstar.com/in/movies/login-required/1234567891", None)
    media._ydl = lambda opts: LoginRequiredYDL(opts)
    login_url = "https://www.jiohotstar.com/in/movies/login-required/1234567891"
    login = client.get("/api/preview", params={"url": login_url})
    check("a logged-out JioHotstar link gets a clear cookie/DRM explanation",
          (login.status_code, "ANHY_HOTSTAR_COOKIES_FILE" in login.json().get("detail", ""),
           "DRM-protected" in login.json().get("detail", "")), (422, True, True))

    class DRMProtectedYDL(FakeYDL):
        def extract_info(self, got_url, download=False):
            raise RuntimeError("This video is DRM protected and cannot be downloaded")

    drm_url = "https://www.jiohotstar.com/in/movies/drm-title/1234567892"
    media._ydl = lambda opts: DRMProtectedYDL(opts)
    drm = client.get("/api/preview", params={"url": drm_url})
    check("a DRM-protected title is refused with no-bypass guidance",
          (drm.status_code, "cannot bypass" in drm.json().get("detail", "")), (422, True))
finally:
    media._ydl = real_ydl
    jobs._q.put = real_put
    for jid in created:
        jobs.delete(jid)
    client.close()
    db.engine.dispose()
    shutil.rmtree(base, ignore_errors=True)

print("ALL PASSED" if not fails else f"{len(fails)} checks failed")
sys.exit(1 if fails else 0)
