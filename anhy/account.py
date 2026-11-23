"""Deleting things for good: one clip, a creator with everything under it, or a whole account.

Nothing here can be undone, so every caller asks the person first. Work in progress is never pulled out from under
itself: a video still being made has to finish first, and a stream still being recorded is stopped on the spot
(`delete_creator(..., stop_live=True)`).
"""
import shutil
import threading
import time
import zipfile
from pathlib import Path

from . import auth, config, creators, ctx, db, jobs, live, usage


class Busy(Exception):
    """Something is still being worked on."""


def _busy(job) -> bool:
    return job["status"] in ("queued", "running", "live") or job["id"] in live.SESSIONS


# ---------------------------------------------------------------- clips and videos
def delete_clip(jid: str, cid: str) -> bool:
    """Remove one clip and its files. The video and its other clips stay."""
    job = jobs.get(jid)
    if not job:
        return False
    clip = next((c for c in job["clips"] if c["id"] == cid), None)
    if not clip:
        return False
    if clip.get("status") in ("queued", "rendering"):
        raise Busy("This clip is still being made. Wait for it to finish first.")
    job["clips"] = [c for c in job["clips"] if c["id"] != cid]
    jobs.save(job)
    d = Path(job["dir"])
    for name in (clip.get("file"), clip.get("cover")):
        if name:
            (d / "clips" / Path(str(name)).name).unlink(missing_ok=True)
    for stem in {cid, Path(str(clip.get("file") or cid)).stem}:     # the captions and the notes kept beside the clip
        for ext in ("ass", "json"):
            (d / "clips" / f"{stem}.{ext}").unlink(missing_ok=True)
    src = clip.get("src")
    if src:                                        # the piece of video fetched or recorded just for this clip
        p = Path(src)
        try:
            mine = p.resolve().parent in ((d / "clips").resolve(), d.resolve()) and p.name.startswith(f"{cid}.src")
        except OSError:
            mine = False
        if mine:                                   # never the whole video's own file
            p.unlink(missing_ok=True)
    ctx.bump()
    return True


def delete_many(job_ids: list, clip_ids: list) -> dict:
    """Delete several videos and clips in one go. What is still being worked on is left alone and counted."""
    out = {"videos": 0, "clips": 0, "skipped": 0}
    whole = set()
    for jid in dict.fromkeys(job_ids):
        job = jobs.get(jid)
        if not job:
            continue
        if _busy(job):
            out["skipped"] += 1
            continue
        if jobs.delete(jid):
            whole.add(jid)
            out["videos"] += 1
    for jid, cid in dict.fromkeys(clip_ids):
        if jid in whole:
            continue
        try:
            out["clips"] += bool(delete_clip(jid, cid))
        except Busy:
            out["skipped"] += 1
    return out


def zip_clips(clip_ids: list, limit_gb: float = 4.0) -> Path:
    """One .zip of finished clips, for downloading many at once. Clips are already compressed, so they are only
    stored, not squeezed again: making the file is as fast as copying."""
    files, total, seen = [], 0, set()
    for jid, cid in dict.fromkeys(clip_ids):
        job = jobs.get(jid)
        clip = next((c for c in (job or {}).get("clips", []) if c["id"] == cid and c.get("status") == "done"), None)
        if not clip or not clip.get("file"):
            continue
        f = Path(job["dir"]) / "clips" / Path(str(clip["file"])).name
        if not f.is_file():
            continue
        total += f.stat().st_size
        if total > limit_gb * 1e9:
            raise ValueError(f"That is more than {limit_gb:g} GB of clips. Download them in smaller groups.")
        base = "".join(ch if ch.isalnum() or ch in " -_" else "" for ch in (clip.get("title") or clip.get("hook") or cid))[:60].strip() or cid
        name, n = f"{base}.mp4", 2
        while name.lower() in seen:
            name, n = f"{base} ({n}).mp4", n + 1
        seen.add(name.lower())
        files.append((f, name))
    if not files:
        raise ValueError("None of those clips are ready to download.")
    tmp = config.user_dir(ctx.uid()) / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    for old in tmp.glob("clips-*.zip"):                       # leftovers from downloads that were abandoned
        if time.time() - old.stat().st_mtime > 3600:
            old.unlink(missing_ok=True)
    out = tmp / f"clips-{int(time.time() * 1000)}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        for f, name in files:
            z.write(f, name)
    return out


# ---------------------------------------------------------------- creators
def _forget(cid: str) -> None:
    """Out of the app's lists straight away, even while the files are still on disk."""
    with creators._lock:
        (creators._mem.get(ctx.uid()) or {}).pop(cid, None)
    ctx.bump()


def _remove(cid: str, mine: list) -> None:
    """The files: every video filed under the creator, then the creator's own folder."""
    for j in mine:
        jobs.delete(j["id"])
    _forget(cid)
    shutil.rmtree(creators.root(cid), ignore_errors=True)
    ctx.bump()


def _remove_when_stopped(cid: str, s) -> None:
    """A recording that was just stopped is still wrapping up its last clips in the background. Nothing new is made
    after Stop, so once it has finished the creator's folder can go without pulling files out from under it."""
    deadline = time.time() + 10 * 60
    while not s.finished and time.time() < deadline:
        time.sleep(0.25)
    try:
        _remove(cid, [j for j in jobs.list_jobs() if j.get("creator") == cid])
    except Exception:  # noqa: BLE001 - it is gone from the app already; a leftover folder is not worth an error
        pass


def _session_for(cid):
    """The creator's recording: one that is running, or one that was stopped and is still wrapping up."""
    s = live.active_for(cid)
    if s:
        return s
    u = ctx.uid()
    return next((x for x in live.SESSIONS.values()
                 if not x.finished and x.job.get("creator") == cid and x.job.get("user") == u), None)


def delete_creator(cid: str, stop_live: bool = False) -> dict:
    """Remove a creator, their settings and folder, and every video and clip filed under them.

    With `stop_live` (what the page sends once the person has confirmed) a stream that is being recorded is stopped
    first instead of saying no: the creator and their videos disappear from the app at once, and the files go as
    soon as the recording has wrapped up."""
    c = creators.get(cid)
    if not c:
        return {"ok": False}
    s = _session_for(cid)
    if s and not stop_live:
        raise Busy("This creator is being recorded. Stop the recording first.")
    skip = s.job["id"] if s else None
    mine = [j for j in jobs.list_jobs() if j.get("creator") == cid]
    if any(_busy(j) for j in mine if j["id"] != skip):
        raise Busy("One of this creator's videos is still being made. Wait for it to finish first.")
    if s:
        s.stop("Creator deleted")
        _forget(cid)
        threading.Thread(target=ctx.bind(lambda: _remove_when_stopped(cid, s)), daemon=True).start()
        return {"ok": True, "videos": len(mine), "recording_stopped": True}
    _remove(cid, mine)
    return {"ok": True, "videos": len(mine)}


# ---------------------------------------------------------------- the whole account
def delete_account(uid: str) -> dict:
    """Remove an account and everything it owns: recordings, videos, clips, creators, connected accounts and
    history. Sign-in is removed too."""
    u = auth.get_user(uid)
    if not u:
        return {"ok": False}
    if u.is_admin:
        raise Busy("Admin accounts can't be deleted. Make it a normal account first.")
    with ctx.as_user(uid):
        for s in list(live.SESSIONS.values()):
            if s.job.get("user") == uid and not s.finished:
                raise Busy("A stream is still being recorded. Stop the recording first.")
        mine = jobs.list_jobs()
        if any(_busy(j) for j in mine):
            raise Busy("A video is still being made. Wait for it to finish, then delete the account.")
        for j in mine:
            jobs.delete(j["id"])
    with db.engine.begin() as c:
        for t in reversed(db.meta.sorted_tables):
            if t.name != "users" and "user_id" in t.c:
                c.execute(t.delete().where(t.c.user_id == uid))
        c.execute(db.users.delete().where(db.users.c.id == uid))
    auth.forget(uid)
    creators._mem.pop(uid, None)
    try:
        from . import accounts
        accounts._settings_mem.pop(uid, None)
    except Exception:  # noqa: BLE001
        pass
    from . import posting
    posting.forget(uid)                            # scheduled posts must not outlive the account that owns them
    usage.forget(uid)
    shutil.rmtree(config.user_dir(uid), ignore_errors=True)
    return {"ok": True}
