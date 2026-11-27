"""Live clipping. A recording turns what it hears into clips; these checks make sure a clip that the judges
approved really is added to the recording. Runs against the code directly: no stream, no speech model, no AI key."""
import io, os, sys, tempfile, threading, time
from pathlib import Path
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


def session(seconds=400, name="recording"):
    d = os.path.join(os.environ["ANHY_DATA"], name)
    os.makedirs(os.path.join(d, "buffer"), exist_ok=True)
    os.makedirs(os.path.join(d, "clips"), exist_ok=True)
    job = {"id": name, "user": None, "kind": "live", "creator": "c1", "dir": d, "created": time.time(),
           "source": {"url": "https://twitch.tv/x", "name": "X"}, "options": dict(creators.DEFAULTS),
           "status": "live", "stage": "Recording", "progress": 0.0, "title": "Test Stream", "uploader": "Tester",
           "duration": 0, "clips": [], "error": None, "ai": {}}
    s = live.Session(job, dict(creators.DEFAULTS))
    with s.lock:
        s.pending = bytearray(b"\x00\x01" * (16000 * seconds))
        s.db = [-30.0] * seconds
        s.samples = 16000 * seconds
    return s


def seed_video(s, duration):
    (Path(s.buf) / "p01_00000.ts").write_bytes(b"fake transport stream")
    (Path(s.buf) / "p01.csv").write_text(f"p01_00000.ts,0.000000,{duration:.6f}\n", encoding="utf-8")
    s.part = 1


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
seed_video(s, 400)
done = threading.Event()
threading.Thread(target=lambda: (s._analyze(), done.set()), daemon=True).start()
time.sleep(6)
s.user_stopped = True                      # wrap up
done.wait(25)
check("the loop turns an approved moment into a clip", len(s.job["clips"]) >= 1, True)
check("...and the recording itself is still healthy", s.job.get("error"), None)
check("...the transcript was read", len(s.words) > 0, True)

# ---- an ffmpeg child that exits without audio must not leave a false "recording" behind
class StopAfterWaits:
    def __init__(self, count): self.count, self.waits = count, 0
    def is_set(self): return self.waits >= self.count
    def wait(self, seconds=None):
        self.waits += 1
        return self.is_set()

class FakeProcess:
    def __init__(self, payload=b"", returncode=0):
        self.stdout = io.BytesIO(payload)
        self.returncode = returncode
    def wait(self): return self.returncode
    def poll(self): return self.returncode
    def terminate(self): pass

real_popen = live.subprocess.Popen
real_transcribe, real_describe = transcribe.transcribe, brain.describe
real_facecam_detect = live.facecam.detect
def fake_popen(payload=b"", returncode=0, log="", segment=False):
    def start(cmd, cwd, stdout, stderr, **kwargs):
        if log:
            stderr.write(log.encode("utf-8")); stderr.flush()
        if segment:
            Path(cwd, "p01_00000.ts").write_bytes(b"fake transport stream")
            Path(cwd, "p01.csv").write_text("p01_00000.ts,0.000000,6.000000\n", encoding="utf-8")
        return FakeProcess(payload, returncode)
    return start

try:
    s = session(0, "ffmpeg-fails")
    check("a new session is not called recording before media arrives", s.recording_state(), "connecting")
    s.stop_flag = StopAfterWaits(99)
    live.subprocess.Popen = fake_popen(log="HTTP 403 https://media.example/live?token=do-not-show", returncode=1)
    s._ingest_loop()
    check("ffmpeg exiting without bytes is reported as a recording failure", s.job.get("error"),
          lambda x: "exit code 1" in x and "403" in x)
    check("the ffmpeg diagnostic never exposes a signed stream URL", "do-not-show" not in s.job.get("error", ""), True)
    check("a failed ingest is not presented as recording", s.recording_state(), "error")

    # Samples alone are insufficient: a manual clip needs a saved video segment as well.
    s = session(10, "audio-only")
    check("audio alone is not reported as a working live recording", s.recording_state(), "connecting")
    try:
        s.clip_last(30)
        refused = False
    except ValueError as e:
        refused = "video buffer" in str(e)
    check("audio-only data cannot queue an unrenderable live clip", refused, True)

    class StillRunning:
        def __init__(self): self.terminated = False
        def poll(self): return None
        def terminate(self): self.terminated = True
    s = session(0, "stalled-ingest")
    s.proc = StillRunning()
    s.ffmpeg_started_at = time.time() - live.STARTUP_TIMEOUT - 1
    s._check_capture_timeout()
    check("an ffmpeg process with no media cannot hang forever in Connecting", s.recording_state(), "error")
    check("the stalled process is stopped and the failure is explained", (s.stop_flag.is_set(), s.proc.terminated,
          "ffmpeg.log" in s.job.get("error", "")), (True, True, True))

    # A speech-model failure must not silently block clips or retry the same audio forever.
    s = session(10, "asr-fails")
    seed_video(s, 10)
    s._scan_segments()
    asr_calls = [0]
    def fail_asr(*args, **kwargs):
        asr_calls[0] += 1
        raise RuntimeError("speech model unavailable")
    transcribe.transcribe = fail_asr
    brain.describe = lambda *a, **k: {"score": 0, "category": "other", "hook": "", "title": "Manual clip",
                                      "description": "", "hashtags": [], "highlight_words": [], "edit_beats": []}
    s._transcribe_step()
    s._transcribe_step()
    s.clip_last(30)
    s._manual_step()
    s._publish()
    check("speech-to-text failure is visible and not retried in a tight loop",
          (s.asr_failed, asr_calls[0], bool(s.job["live"].get("transcribe_error"))), (True, 1, True))
    check("a hand clip still queues from saved video without a transcript",
          (len(s.job["clips"]), s.job["clips"][0]["status"]), (1, "queued"))

    # A completed FFmpeg segment plus real PCM is the point at which capture becomes usable.
    s = session(0, "good-ingest")
    s.stop_flag = StopAfterWaits(1)
    live.subprocess.Popen = fake_popen(payload=b"\x00\x00" * (live.SR * 21), segment=True)
    s._ingest_loop()
    s._scan_segments()
    s.ended = False  # the process produced a segment; the stream itself is still active
    check("audio and a saved transport-stream segment mark capture as recording", s.recording_state(), "recording")
    s.clip_last(30)
    check("manual clipping is accepted once the video buffer exists", len(s.manual), 1)

    # If PCM flows but no video segment is ever produced, don't burn AI calls or leave a fake live session.
    s = session(0, "missing-video")
    s.stop_flag = StopAfterWaits(1)
    live.subprocess.Popen = fake_popen(payload=b"\x00\x00" * (live.SR * 21))
    s._ingest_loop()
    s._analyze()
    check("audio without video ends with an actionable capture error", (s.job["status"], s.job.get("error")),
          lambda x: x[0] == "error" and "did not save a video segment" in x[1])

    # A transient miss on a starting-soon or camera-hidden clip must not lock every later live
    # clip into the facecam-less fallback. Only the first try calls the AI.
    s = session(0, "facecam-retry")
    s.settings["layout"] = "split"
    seed_video(s, 40)
    s._scan_segments()
    s.ended = True
    s._align = lambda src, guess, dur: guess
    asks = []
    def find_on_second_clip(src, ask=None):
        asks.append(ask)
        if len(asks) == 1:
            return None
        return {"x": 100, "y": 30, "w": 300, "h": 200, "conf": "high"}
    live.facecam.detect = find_on_second_clip
    first = {"id": "clip001", "start": 5.0, "end": 15.0, "layout": "split"}
    second = {"id": "clip002", "start": 20.0, "end": 30.0, "layout": "split"}
    s._render(first)
    s._render(second)
    check("live retries facecam detection when its first clip has no camera", len(asks), 2)
    check("the live retry avoids a second AI call and attaches the found webcam",
          (asks[0] is brain.look_for_facecam, asks[1], second.get("facecam")),
          (True, None, [100, 30, 300, 200]))
    check("live facecam retries stop once the camera is found", s.facecam_attempts, 2)
finally:
    live.subprocess.Popen = real_popen
    live.facecam.detect = real_facecam_detect
    transcribe.transcribe, brain.describe = real_transcribe, real_describe

print("\nFAILED: " + ", ".join(fails) if fails else "\nAll tests passed.")
sys.exit(1 if fails else 0)
