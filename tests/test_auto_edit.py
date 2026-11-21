"""Signal-aware clip editing, presets, facecam safety, non-AI fallback, and real ffmpeg render tests."""
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")


def check(name, condition, detail=""):
    if not condition:
        raise AssertionError(f"{name}" + (f" ({detail})" if detail else ""))
    print(f"PASS {name}")


def _make_video(path: Path, dur: float, mode: str = "speech"):
    """Synthesize real test MP4 videos with deterministic visual patterns and audio envelopes."""
    if mode == "quiet":
        # Static dark scene + very quiet flat room noise (~-58 dB)
        vsrc = f"color=c=0x1e2228:s=1280x720:r=30:d={dur}"
        asrc = f"anoisesrc=c=pink:r=48000:d={dur},volume=0.001"
    elif mode == "facecam":
        # Moving colorful gameplay background + bright cyan/magenta facecam box in top-right corner
        vsrc = (
            f"testsrc2=s=1280x720:r=30:d={dur}[bg];"
            f"color=c=0x00ffaa:s=320x240:r=30:d={dur},drawbox=x=16:y=16:w=288:h=208:color=0xff2266:t=fill[cam];"
            f"[bg][cam]overlay=960:0"
        )
        asrc = (
            f"aevalsrc=exprs='0.10*sin(2*PI*220*t)"
            f"+0.42*between(t,3.8,4.6)*sin(2*PI*330*t)"
            f"+0.48*between(t,8.8,9.6)*sin(2*PI*440*t)':s=48000:d={dur}:c=stereo"
        )
    elif mode == "gameplay":
        # High-motion scene with distinct visual cut at 5s and loudness burst at 5s & 10s
        vsrc = (
            f"testsrc2=s=1280x720:r=30:d={dur},"
            f"drawbox=x=0:y=0:w=1280:h=720:color=0x1122aa:t=fill:enable='between(t,5.0,5.6)'"
        )
        asrc = (
            f"aevalsrc=exprs='0.07*sin(2*PI*180*t)"
            f"+0.45*between(t,5.0,5.5)*sin(2*PI*390*t)"
            f"+0.42*between(t,9.8,10.4)*sin(2*PI*460*t)':s=48000:d={dur}:c=stereo"
        )
    elif mode == "deadair":
        # Realistic creator speech clip with TWO long 1.4s silent dead-air pauses (2.5-3.9s and 7.2-8.7s)
        vsrc = f"testsrc2=s=1280x720:r=30:d={dur}"
        asrc = (
            f"aevalsrc=exprs='"
            f"0.22*between(t,0.3,2.3)*sin(2*PI*240*t)"
            f"+0.28*between(t,4.0,7.0)*sin(2*PI*300*t)"
            f"+0.46*between(t,5.2,5.9)*sin(2*PI*380*t)"
            f"+0.26*between(t,8.8,11.6)*sin(2*PI*270*t)"
            f"+0.48*between(t,9.8,10.5)*sin(2*PI*420*t)"
            f"':s=48000:d={dur}:c=stereo"
        )
    else:
        # Speech-like modulated audio with peaks around 4s and 9s
        vsrc = f"testsrc2=s=1280x720:r=30:d={dur}"
        asrc = (
            f"aevalsrc=exprs='0.11*sin(2*PI*240*t)*(0.6+0.4*sin(2*PI*3*t))"
            f"+0.35*between(t,3.9,4.5)*sin(2*PI*360*t)"
            f"+0.38*between(t,8.9,9.6)*sin(2*PI*420*t)':s=48000:d={dur}:c=stereo"
        )

    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", vsrc, "-f", "lavfi", "-i", asrc,
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-shortest", str(path)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Failed creating test video {mode}: {p.stderr}")


def _probe_streams(path: Path) -> tuple[float, float, int, int]:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration,width,height",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True)
    data = json.loads(p.stdout)
    v_dur = a_dur = 0.0
    w = h = 0
    for st in data.get("streams", []):
        if st.get("codec_type") == "video":
            v_dur = float(st.get("duration") or 0)
            w, h = int(st.get("width") or 0), int(st.get("height") or 0)
        elif st.get("codec_type") == "audio":
            a_dur = float(st.get("duration") or 0)
    return v_dur, a_dur, w, h


def _frame_rgb(path: Path, at_sec: float) -> bytes:
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at_sec:.2f}", "-i", str(path),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True)
    return p.stdout


def main():
    from anhy import brain, creators, jobs, render

    # 1. Option validation & Off / Balanced / Pro / Energetic compatibility
    check("edit_style accepts off/balanced/pro/energetic",
          all(jobs.clean_options({"edit_style": s})["edit_style"] == s for s in ("off", "balanced", "pro", "energetic")))
    check("edit_style syncs auto_edit bool",
          jobs.clean_options({"edit_style": "off"})["auto_edit"] is False
          and jobs.clean_options({"edit_style": "pro"})["auto_edit"] is True
          and jobs.clean_options({"edit_style": "energetic"})["auto_edit"] is True)
    check("legacy auto_edit bool syncs edit_style to pro/off",
          jobs.clean_options({"auto_edit": False})["edit_style"] == "off"
          and jobs.clean_options({"auto_edit": True})["edit_style"] == "pro")
    check("invalid option types rejected",
          all(_bad_option(k, v) for k, v in (("auto_edit", "false"), ("sound_fx", "yes"), ("edit_style", "wild"))))

    # 2. Multi-word highlights, AI beats (absolute + clip-relative), non-AI transcript fallback, restraint
    clip = {"start": 10.0, "end": 26.0, "highlight_words": ["no way", "later"],
            "edit_beats": [11.05, 11.2, 17.1, 24.9], "edit_style": "balanced"}
    words = [
        {"w": "wait", "s": 10.3, "e": 10.6},
        {"w": "no", "s": 11.0, "e": 11.2},
        {"w": "way!", "s": 11.22, "e": 11.55},
        {"w": "nothing", "s": 13.5, "e": 13.9},
        {"w": "happens", "s": 14.0, "e": 14.4},
        {"w": "later", "s": 17.0, "e": 17.4},
        {"w": "boom!", "s": 21.8, "e": 22.3},
    ]
    pts = render.edit_points(clip, words)
    check("balanced beats anchor cleanly to spoken words and respect spacing",
          1.0 in pts and 7.0 in pts and all(b - a >= 3.8 - 1e-6 for a, b in zip(pts, pts[1:])), pts)
    hl_pts = render.edit_points({**clip, "edit_beats": []}, words)
    check("multi-word highlight phrases match adjacent transcript tokens", 1.0 in hl_pts and 7.0 in hl_pts, hl_pts)
    fallback_pts = render.edit_points({**clip, "edit_beats": [], "highlight_words": []}, words)
    check("non-AI transcript pacing fallback finds real turns without AI beats", len(fallback_pts) >= 1, fallback_pts)
    check("no spoken moment and no audio/visual signal produces no arbitrary edit",
          render.edit_points({**clip, "highlight_words": [], "edit_beats": []}, []) == [])
    plan_empty = render.plan_edits({**clip, "edit_beats": [math.nan, 15, 999], "highlight_words": []}, words)
    check("invalid/hallucinated beats are ignored cleanly",
          all(b["source"] != "ai_beat" for b in plan_empty["beats"]))

    # Off mode disables all effects and reports clear skipped_reason
    plan_off = render.plan_edits({**clip, "edit_style": "off"}, words)
    check("off preset disables effects and explains why",
          not plan_off["applied"] and plan_off["beats"] == [] and "Off" in plan_off["summary"]["skipped_reason"])
    check("legacy auto_edit=False disables effects", render.edit_points({**clip, "auto_edit": False}, words) == [])

    # Sound control is independent from visual editing
    plan_muted = render.plan_edits({**clip, "edit_style": "energetic", "sound_fx": False}, words)
    check("separate sound toggle disables SFX while keeping visual edits",
          plan_muted["applied"] and len(plan_muted["beats"]) >= 2 and not plan_muted["sound_active"]
          and plan_muted["summary"]["sound_count"] == 0)

    # 3. brain._tidy accepts absolute AND clip-relative beats, and describe preserves beats
    cleaned = brain._tidy({"edit_beats": [10.1, 12.0, 12.5, 17.2, 19.6, "nope"], "highlight_words": []}, 10, 21)
    check("AI edit hints are bounded, spaced >= 2.2s, and sorted", cleaned["edit_beats"] == [12.0, 17.2, 19.6], cleaned["edit_beats"])
    rel_cleaned = brain._tidy({"edit_beats": [2.5, 7.0], "highlight_words": []}, 100.0, 120.0)
    check("clip-relative AI edit_beats are normalized to absolute timeline", rel_cleaned["edit_beats"] == [102.5, 107.0], rel_cleaned["edit_beats"])

    # 4. Creator defaults support edit_style and sound_fx
    c_clean = creators._clean({"name": "Test", "edit_style": "energetic", "sound_fx": False})
    check("creator defaults store edit_style and sound_fx",
          c_clean["edit_style"] == "energetic" and c_clean["auto_edit"] is True and c_clean["sound_fx"] is False)

    # 5. Filter & Pro mastering sanity checks
    check("no edit retains steady video", render._edit_video([]) == "[v0]null[v1];")
    pro_plan = render.plan_edits({**clip, "edit_style": "pro"}, words)
    aud_fc = render._edit_audio(pro_plan)
    check("pro audio chain includes studio voice EQ/compression, -14 LUFS, normalize=0 & alimiter",
          "highpass=f=75" in aud_fc and "acompressor=" in aud_fc and "loudnorm=I=-14" in aud_fc
          and "normalize=0" in aud_fc and "alimiter" in aud_fc and "[0:a:0]" in aud_fc)
    grade_fc = render._grade_chain(pro_plan)
    check("pro grade includes native-YUV film S-curve, warm/cool tone, sharpen & vignette",
          "lutyuv=" in grade_fc and "gamma_r=" in grade_fc and "unsharp=" in grade_fc and "vignette=" in grade_fc)
    mot_fc = render._motion_expr(pro_plan, 1080, 1920)
    check("pro motion includes living dolly creep, decisive punch-in & climax impact micro-shake",
          mot_fc and "sin(2*PI*24*" in mot_fc and "min(1.46," in mot_fc)
    ass_pro = render.build_ass(words, 10.0, 26.0, "PRO HOOK", "bounce", ["no way"], 260, 1460, edit_style="pro")
    check("pro ASS includes spring hook animation, caption bottom scrim & animated retention progress bar",
          "\\fscx84\\fscy84" in ass_pro and ",Scrim," in ass_pro and "\\fscx1\\fscy100\\t(0," in ass_pro)

    # 6. Real FFmpeg end-to-end renders across ALL key clip categories
    with tempfile.TemporaryDirectory(prefix="anhy-real-render-") as td:
        tmp = Path(td)
        out = tmp / "out"
        render.install_fonts()

        # 6a. Speech-heavy clip (Off vs Balanced vs Energetic + Sound Off)
        speech_src = tmp / "speech.mp4"
        _make_video(speech_src, 13.0, "speech")
        speech_words = [
            {"w": "Watch", "s": 0.4, "e": 0.8}, {"w": "what", "s": 0.9, "e": 1.2},
            {"w": "happens", "s": 1.3, "e": 1.7}, {"w": "right", "s": 1.8, "e": 2.1},
            {"w": "here.", "s": 2.2, "e": 2.6},
            {"w": "No", "s": 3.85, "e": 4.05}, {"w": "way!", "s": 4.1, "e": 4.5},
            {"w": "That", "s": 5.4, "e": 5.7}, {"w": "actually", "s": 5.8, "e": 6.3},
            {"w": "worked.", "s": 6.4, "e": 6.9},
            {"w": "Wait", "s": 8.8, "e": 9.1}, {"w": "are", "s": 9.15, "e": 9.3},
            {"w": "you", "s": 9.35, "e": 9.5}, {"w": "serious?!", "s": 9.55, "e": 10.1},
            {"w": "Unbelievable.", "s": 11.1, "e": 11.8},
        ]
        base_clip = {"start": 0.0, "end": 12.5, "hook": "THAT ACTUALLY WORKED", "layout": "blur",
                     "style": "bounce", "captions": True, "highlight_words": ["no way", "serious"], "edit_beats": []}
        res_off = render.render_clip(speech_src, out, "speech_off", {**base_clip, "edit_style": "off"}, speech_words, 1280, 720)
        res_bal = render.render_clip(speech_src, out, "speech_bal", {**base_clip, "edit_style": "balanced", "sound_fx": True}, speech_words, 1280, 720)
        res_pro = render.render_clip(speech_src, out, "speech_pro", {**base_clip, "edit_style": "pro", "sound_fx": True}, speech_words, 1280, 720)
        res_en = render.render_clip(speech_src, out, "speech_en", {**base_clip, "edit_style": "energetic", "sound_fx": True}, speech_words, 1280, 720)
        res_nocap = render.render_clip(speech_src, out, "speech_nocap", {**base_clip, "captions": False, "edit_style": "pro"}, speech_words, 1280, 720)

        check("speech off renders unedited with clear summary", not res_off["edit_summary"]["applied"] and res_off["edit_points"] == [])
        check("speech balanced applies real edits without AI beats",
              res_bal["edit_summary"]["applied"] and len(res_bal["edit_points"]) >= 2 and res_bal["edit_summary"]["sound_count"] >= 1,
              res_bal["edit_summary"])
        check("speech pro applies full pro editing (studio voice, progress bar, climax hit)",
              res_pro["edit_summary"]["applied"] and res_pro["edit_summary"]["studio_voice"]
              and res_pro["edit_summary"]["progress_bar"] and len(res_pro["edit_points"]) >= 2,
              res_pro["edit_summary"])
        check("speech energetic applies edits with higher intensity",
              res_en["edit_summary"]["applied"] and len(res_en["edit_points"]) >= len(res_bal["edit_points"]))
        check("captions-off clip still applies transcript/audio edits",
              res_nocap["edit_summary"]["applied"] and len(res_nocap["edit_points"]) >= 2)

        # Verify A/V sync and frame dimensions on all speech renders
        for label, fname in (("off", "speech_off.mp4"), ("balanced", "speech_bal.mp4"),
                             ("pro", "speech_pro.mp4"), ("energetic", "speech_en.mp4"),
                             ("captions-off", "speech_nocap.mp4")):
            vd, ad, w, h = _probe_streams(out / fname)
            check(f"{label} render has 1080x1920 & tight A/V sync",
                  (w, h) == (1080, 1920) and vd > 11.0 and abs(vd - ad) < 0.08,
                  f"{w}x{h} v={vd:.3f}s a={ad:.3f}s")

        # Confirm pro render visually differs strongly from off render during punch-in
        beat_t = res_pro["edit_points"][0] + 0.4
        check("pro render visually differs from off render during punch-in",
              _frame_rgb(out / "speech_off.mp4", beat_t) != _frame_rgb(out / "speech_pro.mp4", beat_t))

        # 6a2. Dead-air silence tightening (Pro jump-cut pacing + alternating multi-cam + frame-locked captions)
        da_src = tmp / "deadair.mp4"
        _make_video(da_src, 12.2, "deadair")
        da_words = [
            {"w": "Here", "s": 0.4, "e": 0.8}, {"w": "is", "s": 0.9, "e": 1.2}, {"w": "part", "s": 1.3, "e": 1.7}, {"w": "one.", "s": 1.8, "e": 2.2},
            # 1.6s dead air between 2.2s and 4.1s
            {"w": "Now", "s": 4.1, "e": 4.5}, {"w": "watch", "s": 4.6, "e": 5.0}, {"w": "this!", "s": 5.2, "e": 5.8}, {"w": "Wild.", "s": 6.2, "e": 6.8},
            # 1.8s dead air between 6.8s and 8.9s
            {"w": "And", "s": 8.9, "e": 9.3}, {"w": "boom!", "s": 9.8, "e": 10.4}, {"w": "Done.", "s": 10.8, "e": 11.4},
        ]
        res_da = render.render_clip(da_src, out, "deadair_pro",
                                    {"start": 0.0, "end": 12.0, "hook": "ZERO DEAD AIR", "layout": "crop",
                                     "captions": True, "edit_style": "pro", "sound_fx": True, "highlight_words": ["boom"]},
                                    da_words, 1280, 720)
        check("pro dead-air clip executes jump-cuts and removes >= 1.2s of silence",
              res_da["edit_summary"]["jump_cut_count"] >= 2 and res_da["edit_summary"]["trimmed_sec"] >= 1.2,
              res_da["edit_summary"])
        vd, ad, w, h = _probe_streams(out / "deadair_pro.mp4")
        check("jump-cut tightened clip is genuinely shorter with frame-tight A/V sync",
              (w, h) == (1080, 1920) and vd < 10.8 and abs(vd - ad) < 0.08,
              f"{w}x{h} v={vd:.3f}s a={ad:.3f}s")
        # Verify final caption word ("Done.") was remapped earlier into the shortened timeline!
        ass_da_txt = (out / "deadair_pro.ass").read_text(encoding="utf-8")
        check("captions stay locked inside jump-cut tightened duration",
              "DONE" in ass_da_txt.upper() and "0:00:11." not in ass_da_txt)

        # 6b. Quiet / uniform clip: must NOT force arbitrary motion or SFX
        quiet_src = tmp / "quiet.mp4"
        _make_video(quiet_src, 8.0, "quiet")
        res_quiet = render.render_clip(quiet_src, out, "quiet",
                                       {"start": 0.0, "end": 7.5, "hook": "", "layout": "blur", "captions": False, "edit_style": "balanced"},
                                       [], 1280, 720)
        check("quiet uniform clip is left unedited with clear explanation",
              not res_quiet["edit_summary"]["applied"] and res_quiet["edit_points"] == []
              and "quiet" in res_quiet["edit_summary"]["skipped_reason"].lower(), res_quiet["edit_summary"])
        vd, ad, w, h = _probe_streams(out / "quiet.mp4")
        check("quiet clip renders cleanly with A/V sync", (w, h) == (1080, 1920) and abs(vd - ad) < 0.08)

        # 6c. Short clip (<3s): restraint without crashing
        short_src = tmp / "short.mp4"
        _make_video(short_src, 2.8, "speech")
        res_short = render.render_clip(short_src, out, "short",
                                       {"start": 0.0, "end": 2.6, "hook": "QUICK", "layout": "crop", "captions": True, "edit_style": "balanced"},
                                       [{"w": "Quick", "s": 0.5, "e": 0.9}], 1280, 720)
        check("very short clip (<3s) skips mid-clip motion cleanly",
              not res_short["edit_summary"]["applied"] and "3 seconds" in res_short["edit_summary"]["skipped_reason"])
        vd, ad, w, h = _probe_streams(out / "short.mp4")
        check("short clip renders cleanly with A/V sync", (w, h) == (1080, 1920) and abs(vd - ad) < 0.08)

        # 6d. Gameplay clip with NO transcript (captions off) — audio + visual fallback works!
        gp_src = tmp / "gameplay.mp4"
        _make_video(gp_src, 12.0, "gameplay")
        res_gp = render.render_clip(gp_src, out, "gameplay",
                                    {"start": 0.0, "end": 11.5, "hook": "CLUTCH PLAY", "layout": "crop",
                                     "captions": False, "edit_style": "balanced", "sound_fx": True},
                                    [], 1280, 720)
        check("gameplay clip without transcript finds audio/visual peaks",
              res_gp["edit_summary"]["applied"] and len(res_gp["edit_points"]) >= 1
              and any(s in res_gp["edit_summary"]["signals_used"] for s in ("audio_energy", "visual_scene")),
              res_gp["edit_summary"])
        vd, ad, w, h = _probe_streams(out / "gameplay.mp4")
        check("gameplay clip renders cleanly with A/V sync", (w, h) == (1080, 1920) and abs(vd - ad) < 0.08)

        # 6e. Gameplay + Facecam clip: verify facecam PIP stays uncropped in top-right even mid-zoom!
        fc_src = tmp / "facecam.mp4"
        _make_video(fc_src, 12.0, "facecam")
        facecam_box = [960, 0, 320, 240]
        res_fc = render.render_clip(fc_src, out, "facecam",
                                    {"start": 0.0, "end": 11.5, "hook": "WHAT A REACTION", "layout": "gameplay",
                                     "facecam": facecam_box, "captions": True, "edit_style": "energetic", "sound_fx": True},
                                    [{"w": "Whoa!", "s": 3.9, "e": 4.3}, {"w": "No", "s": 8.8, "e": 9.0}, {"w": "way!", "s": 9.05, "e": 9.4}],
                                    1280, 720)
        check("facecam gameplay clip applies edits and marks facecam_safe",
              res_fc["edit_summary"]["applied"] and res_fc["edit_summary"]["facecam_safe"] and len(res_fc["edit_points"]) >= 2,
              res_fc["edit_summary"])
        vd, ad, w, h = _probe_streams(out / "facecam.mp4")
        check("facecam clip renders cleanly with A/V sync", (w, h) == (1080, 1920) and abs(vd - ad) < 0.08)
        # Inspect actual rendered pixels inside top-right facecam PIP during a punch-in beat!
        # PIP sits at x = 1080 - (380 + 8) - 26 = 666..1054, y = 26..319; inner magenta box should remain vibrant red/magenta!
        import numpy as np
        mid_beat = res_fc["edit_points"][0] + 0.45
        img = np.frombuffer(_frame_rgb(out / "facecam.mp4", mid_beat), dtype=np.uint8).reshape(1920, 1080, 3)
        pip_center = img[120:220, 780:940]  # middle of the magenta inner box inside the top-right facecam PIP
        mean_r, mean_g, mean_b = float(pip_center[:, :, 0].mean()), float(pip_center[:, :, 1].mean()), float(pip_center[:, :, 2].mean())
        check("facecam PIP stays intact & uncropped in top-right during punch-in",
              mean_r > 180 and mean_g < 90 and mean_b > 60,
              f"RGB=({mean_r:.0f},{mean_g:.0f},{mean_b:.0f})")

    print("ALL PASSED")


def _bad_option(k, v):
    from anhy import jobs
    try:
        jobs.clean_options({k: v})
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    os.environ.setdefault("ANHY_DATA", tempfile.mkdtemp(prefix="pc-edit-"))
    main()
