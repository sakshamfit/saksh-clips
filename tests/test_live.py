"""Live clipping. A recording turns what it hears into clips; these checks make sure a clip that the judges
approved really is added to the recording. Runs against the code directly: no stream, no speech model, no AI key."""
import os, sys, tempfile, threading, time
os.environ["ANHY_DATA"] = tempfile.mkdtemp(prefix="pc-live-"); os.environ["DATABASE_URL"] = ""
os.environ["ANHY_ACCOUNTS"] = "0"; os.environ["ANHY_AI_KEY"] = ""
sys.path.insert(0, ".")
from anhy import alerts, brain, creators, jobs, live, media, transcribe
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

# ---- the outside world, replaced: nothing here is downloaded or called
live.resolve = lambda url: {"urls": ["http://stub/stream"], "title": "Test Stream", "uploader": "Tester"}
media.run = lambda *a, **k: ""
media.probe = lambda p: {"width": 1920, "height": 1080, "duration": 10.0}
jobs.render_one = lambda *a, **k: None
jobs.submit_live = lambda fn: None
jobs.save = lambda job: None
creators.log = lambda *a, **k: None
creators.get = lambda cid: dict(creators.DEFAULTS)
alerts.notify = lambda *a, **k: None
brain.make_lines = lambda words, **k: [{"s": w["s"], "text": w["w"]} for w in words]
transcribe.transcribe = lambda audio, on_progress=None: [
    {"w": f"word{i}", "s": float(i), "e": i + 0.6} for i in range(max(1, len(audio) // 16000))]
brain.judge_transcript = lambda words, start, now, *a, **k: (
    [{"start": start + 2, "end": min(now, start + 40), "score": 95, "reason": "stub", "hook": "h",
      "title": "t", "description": "", "hashtags": [], "highlight_words": [], "edit_beats": [],
      "category": "other"}], 10)
brain.judge_signals = lambda z, chat, start, now, *a, **k: (
    [{"start": start + 2, "end": min(now, start + 40), "score": 80, "reason": "stub"}], 5)


def session(seconds=400):
    d = os.path.join(os.environ["ANHY_DATA"], "recording")
    os.makedirs(os.path.join(d, "buffer"), exist_ok=True)
    os.makedirs(os.path.join(d, "clips"), exist_ok=True)
    job = {"id": "recording", "user": None, "kind": "live", "creator": "c1", "dir": d, "created": time.time(),
           "source": {"url": "https://twitch.tv/x", "name": "X"}, "options": dict(creators.DEFAULTS),
           "status": "live", "stage": "Recording", "progress": 0.0, "title": "Test Stream", "uploader": "Tester",
           "duration": 0, "clips": [], "error": None, "ai": {}}
    s = live.Session(job, dict(creators.DEFAULTS))
    with s.lock:
        s.pending = bytearray(b"\x00\x01" * (16000 * seconds))
        s.db = [-30.0] * seconds
        s.samples = 16000 * seconds
    return s


# ---- a clip the judges approved is added to the recording
s = session()
before = len(s.job["clips"])
s._add_clip({"start": 10.0, "end": 40.0, "score": 95, "reason": "stub", "hook": "h", "title": "t",
             "description": "", "hashtags": [], "highlight_words": [], "edit_beats": [], "category": "other"})
check("a clip the judges approved is added to the recording", len(s.job["clips"]), before + 1)
c = s.job["clips"][-1]
check("...and nothing went wrong while adding it", s.job["ai"].get("last_error"), None)
check("...with the creator's caption style", (c["style"], c["layout"]), ("bounce", "blur"))
check("...queued to be made, not left hanging", c["status"], "queued")
check("...and the moment it covers", (c["start"], c["end"]), (10.0, 40.0))

# ---- the whole loop: a moment the judges approve becomes a clip
s = session()
done = threading.Event()
threading.Thread(target=lambda: (s._analyze(), done.set()), daemon=True).start()
time.sleep(6)
s.user_stopped = True                      # wrap up
done.wait(25)
check("the loop turns an approved moment into a clip", len(s.job["clips"]) >= 1, True)
check("...and the recording itself is still healthy", s.job.get("error"), None)
check("...the transcript was read", len(s.words) > 0, True)

print("\nFAILED: " + ", ".join(fails) if fails else "\nAll tests passed.")
sys.exit(1 if fails else 0)
