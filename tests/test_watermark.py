"""Channel banner settings, clip inheritance, and the rendered sticker overlay."""
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, ".")


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS {name}")


def rejected(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def frame(path: Path):
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(path), "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    return np.frombuffer(p.stdout, dtype=np.uint8).reshape((1920, 1080, 3))


def main(data):
    os.environ.update(ANHY_DATA=data, DATABASE_URL="", ANHY_ACCOUNTS="0")
    from anhy import auth, branding, creators, ctx, db, jobs, live, render

    db.init()
    render.install_fonts()
    try:
        check("banner text is trimmed to one line and the supported length",
              branding.clean_watermark_text("  @Kai   Clips\nHighlights ") == "@Kai Clips Highlights"
              and len(branding.clean_watermark_text("x" * 80)) == branding.MAX_WATERMARK_TEXT)
        check("only the four safe corner positions are accepted",
              branding.clean_watermark_position("bottom-right") == "bottom-right"
              and rejected(lambda: branding.clean_watermark_position("center")))
        check("clip options validate banner toggles, text, and position",
              jobs.clean_options({"watermark_enabled": True, "watermark_text": " @Kai ",
                                  "watermark_position": "top-right"}) == {
                                      "watermark_enabled": True, "watermark_text": "@Kai",
                                      "watermark_position": "top-right"})
        check("bad banner settings are refused",
              rejected(lambda: jobs.clean_options({"watermark_enabled": "yes"}))
              and rejected(lambda: jobs.clean_options({"watermark_position": "center"}))
              and rejected(lambda: creators._clean({"watermark_enabled": 1})))

        user = auth.local_user().id
        with ctx.as_user(user):
            creator = creators.create({"name": "Kai Clips", "watermark_enabled": True,
                                      "watermark_text": "@KaiClips", "watermark_position": "bottom-right"})
            job = jobs.create({"type": "file", "name": "Banner test"}, {}, enqueue=False, creator=creator["id"])
            check("creator banner defaults carry into new video jobs",
                  job["options"]["watermark_enabled"] is True
                  and job["options"]["watermark_text"] == "@KaiClips"
                  and job["options"]["watermark_position"] == "bottom-right")

            session = live.Session.__new__(live.Session)
            session.n = 0
            session.settings = creator
            session.job = {"id": job["id"], "options": {}, "clips": []}
            session.lock = threading.RLock()
            with patch.object(jobs, "save"), patch.object(jobs, "submit_live"):
                session._add_clip({"start": 0.0, "end": 10.0})
            live_clip = session.job["clips"][0]
            check("live clips receive the creator's channel banner settings",
                  live_clip["watermark_enabled"] is True and live_clip["watermark_text"] == "@KaiClips"
                  and live_clip["watermark_position"] == "bottom-right")

            clip = {"id": "clip01", "start": 0.0, "end": 5.0, "status": "done", "version": 1,
                    "layout": "blur", "style": "bounce", "hook": "", "captions": False}
            job["clips"] = [clip]
            with patch.object(jobs._q, "put") as queued:
                jobs.request_render(job["id"], clip["id"], {"watermark_enabled": True,
                                                               "watermark_text": "@Custom", "watermark_position": "top-left"})
            check("the clip editor can save banner changes for a re-make",
                  clip["watermark_enabled"] is True and clip["watermark_text"] == "@Custom"
                  and clip["watermark_position"] == "top-left" and queued.called)

        with tempfile.TemporaryDirectory(prefix="anhy-watermark-render-") as temp:
            root = Path(temp)
            src = root / "source.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                            "color=c=0x405870:s=640x360:r=30:d=2.5", "-an", "-c:v", "libx264",
                            "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(src)], check=True)
            out = root / "clips"
            base = {"start": 0.0, "end": 2.5, "hook": "", "layout": "crop", "captions": False,
                    "edit_style": "off"}
            plain = render.render_clip(src, out, "plain", base, [], 640, 360)
            branded = render.render_clip(src, out, "branded", {**base, "watermark_enabled": True,
                                                                 "watermark_text": "@KaiClips",
                                                                 "watermark_position": "top-left"}, [], 640, 360)
            plain_frame = frame(out / plain["file"])
            branded_frame = frame(out / branded["file"])
            changed = np.any(plain_frame[:200, :600] != branded_frame[:200, :600], axis=2)
            check("the sticker is composited into the exported video frames", int(changed.sum()) > 1000)
    finally:
        db.engine.dispose()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="pc-watermark-") as data:
        main(data)
