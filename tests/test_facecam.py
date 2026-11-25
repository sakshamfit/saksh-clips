"""Where the webcam is in a gaming stream.

The webcam is found by the one thing a webcam always has and a game almost never has: a straight
border that stays in the same place in every frame.

Runs against the code directly, with its own empty data folder."""
import os, shutil, subprocess, sys, tempfile
os.environ["ANHY_DATA"] = tempfile.mkdtemp(prefix="pc-facecam-")
sys.path.insert(0, ".")
from anhy import facecam, render
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

work = tempfile.mkdtemp(prefix="pc-facecam-work-")
FRAMES = "100"          # four seconds at 25 frames. Noise does not compress, so the picture stays small.
NOISE = "nullsrc=s=1280x720:r=25,noise=alls=24:allf=t"


def make(name, extra, where="0:0"):
    """A video with a moving background and one still box in it.

    The background is noise, which never repeats, so the only straight line that stays in the
    same place is the edge of the box."""
    path = os.path.join(work, name)
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", NOISE,
                    "-f", "lavfi", "-i", extra,
                    "-filter_complex", f"[0:v][1:v]overlay={where}[v]", "-map", "[v]",
                    "-frames:v", FRAMES,
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", path], check=True)
    return path


try:
    # ---- a webcam in the top-left: a still, lit box
    cam = make("cam.mp4", "color=c=0xC08050:size=300x170:rate=25")
    box = facecam.detect(cam)
    check("a webcam in the top-left is found", (box or {}).get("corner"), "top-left")
    # the border lies between two pixels, so the box is a pixel short of the drawn one
    check("its size is right", abs((box or {}).get("w", 0) - 300) <= 1, True)
    check("its height is right", abs((box or {}).get("h", 0) - 170) <= 1, True)
    check("it is sure of it", (box or {}).get("conf"), "high")

    # ---- the same box in another corner
    cam_br = make("cam_br.mp4", "color=c=0xC08050:size=300x170:rate=25", where="980:500")
    box = facecam.detect(cam_br)
    check("a webcam in the bottom-right is found too", (box or {}).get("corner"), "bottom-right")
    check("its position is right", abs((box or {}).get("x", 0) - 980) <= 1, True)

    # ---- a video with no webcam in it: nothing that stays still, so nothing is found
    plain = os.path.join(work, "plain.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", NOISE, "-frames:v", FRAMES,
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", plain], check=True)
    check("a video with nothing still in it finds no webcam", (facecam.detect(plain) or {}).get("conf", "none"), "none")

    # ---- a file that cannot be read must not stop a job
    check("a file that cannot be read gives nothing, not an error",
          facecam.detect(os.path.join(work, "nope.mp4")), None)

    # ---- the job hands the webcam on to the render
    from anhy import jobs
    job = {"source_path": cam}
    box = jobs._facecam(job)
    check("the job finds the webcam for its clips", bool(box), True)
    check("the job keeps it on the job", "facecam" in job, True)
    check("the box is the right size", abs(box[2] - 300) <= 1 and abs(box[3] - 170) <= 1, True)
    check("a job with no video finds no webcam", jobs._facecam({"source_path": os.path.join(work, "nope.mp4")}), None)

    # ---- a webcam that is square and runs along one edge, not into a corner
    sq = make("cam_sq.mp4", "color=c=0xC08050:size=360x355:rate=25", where="920:165")
    box = facecam.detect(sq)
    check("a square webcam along an edge is found", (box or {}).get("corner"), "right")
    check("it starts where the picture starts", abs((box or {}).get("x", 0) - 920) <= 3, True)
    check("it is as tall as the picture", abs((box or {}).get("h", 0) - 355) <= 6, True)

    # ---- one frame goes to the AI, and its answer is measured again before it is used
    def say(looked, box=None):
        return lambda jpeg: {"looked": looked, "box": box}

    box = facecam.detect(sq, ask=say(True, (0.71, 0.22, 0.29, 0.44)))      # rough, as a model gives it
    check("the box the AI suggests is put onto the borders", abs((box or {}).get("x", 0) - 920) <= 3, True)
    check("a rough box is corrected to the picture's real size", abs((box or {}).get("h", 0) - 355) <= 6, True)
    check("it is still sure of it", (box or {}).get("conf"), "high")
    check("the AI saying there is no webcam is believed", facecam.detect(sq, ask=say(True, None)), None)
    box = facecam.detect(sq, ask=say(True, (0.02, 0.02, 0.06, 0.06)))      # a box on nothing at all
    check("a box that lands on nothing is thrown away and the search carries on",
          abs((box or {}).get("x", 0) - 920) <= 3, True)
    box = facecam.detect(sq, ask=say(False))                              # a model that only reads text
    check("a model that cannot look at pictures changes nothing", abs((box or {}).get("x", 0) - 920) <= 3, True)

    # ---- what the model's one line is turned into
    from unittest.mock import patch
    from anhy import brain, ai as _ai
    with patch.object(_ai, "see", return_value=("CAME x=71% y=20% w=29% h=38%", 0, 0)):
        got = brain.look_for_facecam(b"jpeg")
    check("the box in the model's answer is read", [round(v, 2) for v in (got["box"] or ())],
          [0.71, 0.20, 0.29, 0.38])
    with patch.object(_ai, "see", return_value=("NO CAM", 0, 0)):
        check("'no cam' is read as no webcam", brain.look_for_facecam(b"jpeg"), {"looked": True, "box": None})
    with patch.object(_ai, "see", return_value=("I am a text model.", 0, 0)):
        check("an answer that is neither is not believed", brain.look_for_facecam(b"jpeg")["looked"], False)

    # ---- the layout: the game fills the screen and the webcam sits in the corner
    f = render._filter("gameplay", 1920, [0, 0, 300, 170])
    check("the game fills the screen", "scale=1080:1920" in f and "crop=1080:1920" in f, True)
    check("the webcam is cropped out of the source", "crop=300:170:0:0" in f, True)
    check("the webcam is put in the corner", "overlay=" in f, True)
    check("without a webcam the game simply fills the screen",
          "scale=1080:1920" in render._filter("gameplay", 1920, None) and "overlay=" not in render._filter("gameplay", 1920, None), True)
    check("the layout is offered to the user", "gameplay" in render.LAYOUTS, True)
    # the heading must not run under the webcam picture
    pip_h = 380 * 376 / 363
    check("the heading goes below the webcam picture", render._hook_y("gameplay", 300, [0, 0, 363, 376]) > 26 + pip_h, True)
    check("another picture moves the heading with it",
          render._hook_y("gameplay", 300, [0, 0, 300, 170]) < render._hook_y("gameplay", 300, [0, 0, 363, 376]), True)
    check("without a webcam the heading stays at the top", render._hook_y("crop", 300, None), 300)

    # ---- the split layout: half streamer on top, half game underneath
    check("the split layout is offered to the user", "split" in render.LAYOUTS, True)
    check("the split layout is one of the layouts that use a webcam", "split" in render.FACECAM_LAYOUTS, True)
    f = render._filter("split", 1920, [960, 0, 320, 240], src_w=1280, src_h=720)
    check("the webcam is cropped out of the source", "crop=320:240:960:0" in f, True)
    check("the webcam is scaled to the top half", "scale=1080:960" in f, True)
    check("the game is stacked under the webcam", "vstack=inputs=2" in f and "[p][b]vstack" in f, True)
    check("the two halves are divided by a line", "drawbox=x=0:y=958:w=1080:h=4" in f, True)
    check("the game half is not the whole picture (the webcam is not shown twice)",
          "crop=960:720:0:0" in f, True)
    check("the game half is cut from the picture before it is fitted to the half",
          "crop=960:720:0:0,scale=1080:960" in f, True)
    check("a webcam at the bottom-left of a picture takes the game from the right of it",
          render._gameplay_region([0, 480, 320, 240], 1280, 720), (320, 0, 960, 720))
    check("a webcam across the bottom takes the game from above it",
          render._gameplay_region([0, 540, 1920, 540], 1920, 1080), (0, 0, 1920, 540))
    check("a webcam across the top takes the game from below it",
          render._gameplay_region([0, 0, 1920, 270], 1920, 1080), (0, 270, 1920, 810))
    check("a webcam in the middle of the picture leaves the whole picture",
          render._gameplay_region([810, 390, 300, 300], 1920, 1080), (0, 0, 810, 1080))
    check("a webcam too small to matter leaves the whole picture",
          render._gameplay_region([0, 0, 4, 4], 1280, 720), (0, 0, 1280, 720))
    check("no webcam at all leaves the whole picture",
          render._gameplay_region(None, 1280, 720), (0, 0, 1280, 720))
    check("without a webcam the split layout fills the screen like crop",
          "vstack" not in render._filter("split", 1920, None, src_w=1280, src_h=720)
          and "crop=1080:1920" in render._filter("split", 1920, None, src_w=1280, src_h=720), True)
    lay, fg_h, hook_y, cap_y = render.geometry(1280, 720, "split")
    check("the split layout keeps the whole frame", (lay, fg_h), ("split", 1920))
    check("the hook goes just under the seam, in the game half", 960 < hook_y < 1400, True)
    check("the captions go in the game half, below the hook", cap_y > hook_y + 200, True)
    check("the heading is not moved by the webcam in the split layout (the webcam is not in a corner)",
          render._hook_y("split", hook_y, [960, 0, 320, 240]), hook_y)
    check("a vertical picture has no room for a split and is filled instead",
          render.geometry(720, 1280, "split")[0], "crop")
finally:
    shutil.rmtree(work, ignore_errors=True)      # the pictures are big; they must not be left behind

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
