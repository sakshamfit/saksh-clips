"""Sparse, transcript-anchored edit planning and ffmpeg filter regression tests."""
import math
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS {name}")


def main():
    from anhy import brain, jobs, render

    clip = {"start": 10.0, "end": 21.0, "highlight_words": ["WOW", "later"],
            "edit_beats": [11.05, 11.2, 17.1, 19.9]}
    words = [{"w": "wow!", "s": 11.0, "e": 11.4}, {"w": "nothing", "s": 13.0, "e": 13.3},
             {"w": "later", "s": 17.0, "e": 17.4}]
    check("accents snap to spoken words, spaced and clip relative", render.edit_points(clip, words) == [1.0, 7.0])
    check("highlight words alone can supply beats", render.edit_points({**clip, "edit_beats": []}, words) == [1.0, 7.0])
    check("no spoken moment produces no arbitrary edit", render.edit_points({**clip, "highlight_words": []}, []) == [])
    check("invalid and hallucinated beats ignored", render.edit_points({**clip, "edit_beats": [math.nan, 15, 999],
                                                                          "highlight_words": []}, words) == [])
    check("opt out disables effects", render.edit_points({**clip, "auto_edit": False}, words) == [])
    check("opt out rejects wrong types", all(_bad_option(k) for k in ("auto_edit", "sound_fx")))
    cleaned = brain._tidy({"edit_beats": [10.1, 12, 13, 17.2, 19, "nope"], "highlight_words": []}, 10, 21)
    check("AI edit hints are bounded, sparse, and within clip", cleaned["edit_beats"] == [12.0, 17.2])
    with tempfile.TemporaryDirectory() as d:
        with patch.object(render, "_render_in", return_value={"file": "out.mp4"}) as encode:
            result = render.render_clip(Path(d) / "in.mp4", Path(d) / "out", "clip", clip, words, 1920, 1080)
        check("render passes its validated plan and sound option", encode.call_args.kwargs["beats"] == [1, 7]
              and encode.call_args.kwargs["sound_fx"] and result["edit_points"] == [1, 7])
    check("video punch-in stays bounded", "0.11*" in render._edit_video([1, 7]) and "eq=contrast" in render._edit_video([1, 7]))
    check("sound accent is short, quiet and keeps source voice", "volume=0.025" in render._edit_audio([1])
          and "[0:a:0]" in render._edit_audio([1]) and "duration=first" in render._edit_audio([1]))
    check("no edit retains steady video", render._edit_video([]) == "[v0]null[v1];")


def _bad_option(k):
    from anhy import jobs
    try:
        jobs.clean_options({k: "false"})
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    os.environ.setdefault("ANHY_DATA", tempfile.mkdtemp(prefix="pc-edit-"))
    main()
