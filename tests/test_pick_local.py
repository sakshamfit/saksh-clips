"""Clipping a video that is already in a folder on this computer: it is found in the folder, it is used where it
is (never downloaded or uploaded again), and nothing outside the folders on offer can be reached.
Runs against the code directly, with its own temporary folders; nothing else is touched."""
import json, os, shutil, sys, tempfile, time
from pathlib import Path

base = Path(tempfile.mkdtemp(prefix="pc-pick-"))
videos = base / "Recordings"
(videos / "last week").mkdir(parents=True)
big = videos / "SakshFit - Full Stream.mkv"
big.write_bytes(b"v" * 5000)
short = videos / "short.mp4"
short.write_bytes(b"s" * 1200)
(videos / "still going.mp4.part").write_bytes(b"p" * 100)      # a download that has not finished
(videos / "notes.txt").write_text("not a video", encoding="utf-8")
(videos / ".hidden.mp4").write_bytes(b"h" * 10)
(videos / "empty.mp4").write_bytes(b"")
(videos / "last week" / "older.mp4").write_bytes(b"o" * 800)
private = base / "private"                                     # not a folder Anhy Clips was offered
private.mkdir()
(private / "diary.mp4").write_bytes(b"x" * 10)
now = time.time()
os.utime(big, (now - 86400, now - 86400))                      # downloaded yesterday, not still arriving
os.utime(short, (now - 3600, now - 3600))                      # and this one an hour ago

os.environ["ANHY_DATA"] = str(base / "data")
os.environ.update(DATABASE_URL="", ANHY_ACCOUNTS="0", ANHY_PUBLIC_URL="http://127.0.0.1:8765",
                  ANHY_FETCH_MODEL="0", ANHY_UPDATE_CHECK="0", ANHY_AI_KEY="", ANHY_AI_KEY_FILE="",
                  ANHY_VERIFY_EMAIL="0", ANHY_LOCAL_PICK="1", ANHY_LOCAL_FOLDERS=str(videos))
sys.path.insert(0, ".")
from fastapi.testclient import TestClient  # noqa: E402

from anhy import config, db, jobs, local, server, tools  # noqa: E402

db.init()
tools._seen.update(at=time.time(), missing=[])                 # picking a file up needs no ffmpeg
fails = []
BASE = "http://127.0.0.1:8765"
ORIGIN = {"Origin": BASE}
c = TestClient(server.app, base_url=BASE, follow_redirects=False)


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    ok or fails.append(name)


browse = lambda p="": c.get("/api/local/browse", params={"path": str(p)})  # noqa: E731

# ---- the picker is offered to the owner of this computer
check("the page is told it may pick from folders here", c.get("/api/status").json()["local_pick"], True)
r = c.get("/api/local").json()
check("the folder from .env is on offer", str(videos) in [x["path"] for x in r["roots"]], True)
check("a folder window is offered only to whoever is sitting at that computer", r["folder_window"], False)
check("the video endings it looks for are said", ".mkv" in r["kinds"] and ".exe" not in r["kinds"], True)
check("the page is told the same endings: what it may upload and what may be picked are one list",
      c.get("/api/status").json()["video_kinds"], r["kinds"])

# ---- what a folder shows
d = browse(videos).json()
check("the videos in it are listed", sorted(v["name"] for v in d["videos"]), ["SakshFit - Full Stream.mkv", "empty.mp4", "short.mp4"])
check("a download still in progress is not offered as a video", any(".part" in v["name"] for v in d["videos"]), False)
check("nor is a document, or a file nobody can see", any(v["name"] in ("notes.txt", ".hidden.mp4") for v in d["videos"]), False)
check("its own folders are listed, so a recording in a subfolder can be reached", [f["name"] for f in d["folders"]], ["last week"])
check("the newest video is first, which is the one just downloaded", d["videos"][0]["name"], "empty.mp4")
check("...then the ones before it, newest first", [v["name"] for v in d["videos"]], ["empty.mp4", "short.mp4", "SakshFit - Full Stream.mkv"])
check("nothing is at the top of the list but the folders on offer", (browse("").json()["videos"], browse("").json()["roots"] != []), ([], True))
sub = browse(videos / "last week").json()
check("a subfolder opens, and says how to come back up", ([v["name"] for v in sub["videos"]], sub["parent"]), (["older.mp4"], str(videos)))
pasted = browse(big).json()
check("a whole path to a video opens its folder with that one marked", (pasted["path"], pasted["chosen"]), (str(videos), big.name))

# ---- nowhere else on the machine
check("a folder that was not offered is refused", browse(private).status_code, 400)
check("...and so is anything behind it", browse(private / "diary.mp4").status_code, 400)
check("the way out of a folder with .. is refused", browse(str(videos) + "/../private").status_code, 400)
check("a system folder is refused", browse("/etc").status_code, 400)
check("an empty path is not followed anywhere", browse("   ").json()["roots"] != [], True)
check("a folder that has gone is said plainly", "isn't there" in c.get("/api/local/info", params={"path": str(videos / "gone.mp4")}).json().get("detail", ""), True)

# ---- keeping a folder in the list
r = c.post("/api/local/folders", json={"path": str(private)}, headers=ORIGIN)
check("a folder of your own can be added", (r.status_code, any(x["saved"] and x["path"] == str(private) for x in r.json()["roots"])), (200, True))
check("it is kept in the data folder", json.loads((config.DATA_DIR / "local.json").read_text())["folders"], [str(private)])
check("...and its videos can be picked from then on", [v["name"] for v in browse(private).json()["videos"]], ["diary.mp4"])
check("adding it again changes nothing", len(c.post("/api/local/folders", json={"path": str(private)}, headers=ORIGIN).json()["roots"]), len(r.json()["roots"]))
check("a file is not a folder", c.post("/api/local/folders", json={"path": str(big)}, headers=ORIGIN).status_code, 400)
check("half a path is refused", c.post("/api/local/folders", json={"path": "Recordings"}, headers=ORIGIN).status_code, 400)
check("nothing to add is refused", c.post("/api/local/folders", json={"path": ""}, headers=ORIGIN).status_code, 400)
check("another website cannot add one", c.post("/api/local/folders", json={"path": str(private)}, headers={"Origin": "https://evil.example"}).status_code, 403)
r = c.request("DELETE", "/api/local/folders", json={"path": str(private)}, headers=ORIGIN)
check("taking it out of the list leaves the folder itself alone",
      (r.status_code, browse(private).status_code, private.is_dir(), json.loads((config.DATA_DIR / "local.json").read_text())["folders"]),
      (200, 400, True, []))

# ---- one video, looked at before it is taken
i = c.get("/api/local/info", params={"path": str(big)}).json()
check("its name, size and when it arrived are known", (i["name"], i["size"], i["fresh"]), (big.name, 5000, False))
just = videos / "just arrived.mp4"
just.write_bytes(b"j" * 300)                                   # written while this test runs, so: seconds ago
check("one written to seconds ago is marked, in case it is still downloading",
      c.get("/api/local/info", params={"path": str(just)}).json()["fresh"], True)
check("a document is refused", c.get("/api/local/info", params={"path": str(videos / "notes.txt")}).status_code, 400)
check("a picture of it is asked for politely, whether or not ffmpeg can draw one",
      c.get("/api/local/thumb", params={"path": str(big)}).status_code in (200, 404), True)
check("...but never of a file somewhere else", c.get("/api/local/thumb", params={"path": str(private / "diary.mp4")}).status_code, 404)

# ---- taking the video in: linked into the job's folder, never copied, never downloaded
held = base / "held"
held.mkdir()
got, linked = local.take(str(big), held)
check("the video is in the job's own folder", (got.name, got.parent), ("source.mkv", held))
check("it was linked, not copied: one file, two names, so no room is used twice",
      (linked, got.stat().st_ino == big.stat().st_ino, got.stat().st_nlink, got.stat().st_size), (True, True, 2, 5000))
check("taking it in again finds it already there", local.take(str(big), held), (got, True))

# ---- a drive that cannot be linked to: the video is worked on where it is
real = os.link
os.link = lambda a, b: (_ for _ in ()).throw(OSError(18, "Invalid cross-device link"))
try:
    d2 = base / "jobdir"
    d2.mkdir()
    got, linked = local.take(str(short), d2)
    check("the video is left where it is, and the job will say so", (got, linked, list(d2.iterdir())), (short, False, []))
finally:
    os.link = real

# ---- starting the clipping of it. Reading the file needs ffmpeg, so where there is none the reason is given
# at once and no half-made video is left behind; where there is one, the video is queued straight away.
before = len(c.get("/api/jobs").json())
r = c.post("/api/jobs/local", json={"path": str(big), "options": {"max_clips": 3, "layout": "zoom"}, "creator": ""}, headers=ORIGIN)
if i["problem"]:
    check("a video that cannot be read is refused with the reason", (r.status_code, i["problem"][:20] in r.text), (422, True))
    check("...and no video is left half made", len(c.get("/api/jobs").json()), before)
else:
    job = r.json()
    check("clipping starts on the file itself", (r.status_code, job.get("status"), job.get("title")), (200, "queued", big.name))
    check("the job says where the video came from", (job["source"]["local"], job["source"]["from"], job["source"]["linked"]), (True, "Recordings", True))
    check("the folder it came from is not given to the page", str(videos) in r.text or str(base) in r.text, False)
    check("nothing was downloaded or uploaded to get it", job["source"]["type"], "file")
    check("the choices made for it were kept", (job["options"]["max_clips"], job["options"]["layout"]), (3, "zoom"))
    src = list(config.DATA_DIR.glob(f"users/*/jobs/{job['id']}/source.mkv"))
    check("the job works on its own copy of the file, linked to the one in the folder",
          (len(src), src and src[0].stat().st_ino == big.stat().st_ino), (1, True))
    check("the video itself is untouched", big.read_bytes(), b"v" * 5000)
    check("deleting the job leaves the video where it was",
          (c.delete(f"/api/jobs/{job['id']}", headers=ORIGIN).status_code, big.is_file(), big.read_bytes()), (200, True, b"v" * 5000))

# ---- what is refused
check("a file that is not a video", c.post("/api/jobs/local", json={"path": str(videos / "notes.txt")}, headers=ORIGIN).status_code, 400)
check("a folder, rather than a video", c.post("/api/jobs/local", json={"path": str(videos)}, headers=ORIGIN).status_code, 400)
check("an empty file: probably a download that never finished",
      "empty" in c.post("/api/jobs/local", json={"path": str(videos / "empty.mp4")}, headers=ORIGIN).json()["detail"], True)
check("a file somewhere else on the machine", c.post("/api/jobs/local", json={"path": str(private / "diary.mp4")}, headers=ORIGIN).status_code, 400)
check("nonsense options are refused before the file is even looked at",
      c.post("/api/jobs/local", json={"path": str(short), "options": {"max_clips": "lots"}}, headers=ORIGIN).status_code, 400)
check("another website cannot start one", c.post("/api/jobs/local", json={"path": str(short)}, headers={"Origin": "https://evil.example"}).status_code, 403)
check("a creator that does not exist", c.post("/api/jobs/local", json={"path": str(short), "creator": "nope"}, headers=ORIGIN).status_code, 404)

# A video that would not fit in the storage left over is refused before anything is copied, with the reason in
# plain words: "your storage is full" on its own would send someone looking in the wrong place.
was = len(c.get("/api/jobs").json())
real_gb, real_disk, real_info = config.MAX_USER_GB, server._disk, local.info
config.MAX_USER_GB, server._disk = 0, {}
local.info = lambda p: dict(real_info(p), problem="", duration=60.0, size=1200)   # a video that reads fine here
try:
    r = c.post("/api/jobs/local", json={"path": str(short)}, headers=ORIGIN)
    check("a video that will not fit in the storage left says so", (r.status_code, "room" in r.json()["detail"]), (507, True))
    check("...and nothing was copied or queued for it", len(c.get("/api/jobs").json()), was)
finally:
    config.MAX_USER_GB, server._disk, local.info = real_gb, real_disk, real_info

# ---- who may look through the folders
class Person:
    def __init__(self, admin): self.is_admin, self.disabled = admin, False

check("on your own computer you may", local.enabled(Person(False)), True)
check("nobody asking at all may not", local.enabled(None), False)
config.ACCOUNTS = True
check("on a server with sign-in, only the admin may", (local.enabled(Person(True)), local.enabled(Person(False))), (True, False))
config.ACCOUNTS = False
config.LOCAL_PICK = False
check("and ANHY_LOCAL_PICK=0 turns the whole thing off",
      (local.enabled(Person(True)), c.get("/api/local").status_code, c.get("/api/status").json()["local_pick"]), (False, 404, False))
config.LOCAL_PICK = True
check("turned back on, it all works again", c.get("/api/local").status_code, 200)

# ---- an empty folder, and a folder that cannot be opened
empty_dir = videos / "nothing here"
empty_dir.mkdir()
check("an empty folder is said to be empty", browse(empty_dir).json()["videos"], [])
nope = videos / "locked"
nope.mkdir()
os.chmod(nope, 0o000)
check("a folder that cannot be opened says so rather than failing", browse(nope).status_code, 400)
os.chmod(nope, 0o755)

print("ALL PASSED" if not fails else f"{len(fails)} failed")
db.engine.dispose()
shutil.rmtree(base, ignore_errors=True)
sys.exit(1 if fails else 0)
