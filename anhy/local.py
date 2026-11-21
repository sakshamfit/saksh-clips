"""Videos that are already on this computer.

A video does not have to be sent through the browser to be clipped, and it does not have to be downloaded again.
When the video is already in a folder Anhy Clips can reach (your Downloads, a drive of recordings, the folder a
download tool puts things in), that folder is opened here, the video is picked from a list, and clipping starts
from the file itself. Nothing travels over the network: the file is linked into the video's own folder, which is
instant and takes no extra room. Only when it is on a drive that cannot be linked to is it worked on where it is,
and the job says so.

Who may look through the folders: on your own computer (sign-in off) you are the owner, so you may. On a server
where people sign in, only the admin may, because those folders belong to that machine and not to its visitors.
ANHY_LOCAL_PICK=0 turns the whole thing off, and ANHY_LOCAL_FOLDERS adds folders of your own."""
import hashlib
import json
import os
import time
from pathlib import Path

from . import config, media

VIDEO_EXTS = media.VIDEO_EXTS        # the same endings everywhere: an upload, a picked file, what the page accepts
# Folders that are never interesting and can be enormous to list. Skipped when they turn up in a listing.
SKIP_DIRS = {"appdata", "application data", "windows", "program files", "program files (x86)", "programdata",
             "system volume information", "$recycle.bin", "node_modules", "__pycache__", "site-packages",
             "library", ".git", ".cache", ".npm", ".venv", "venv"}
MAX_ITEMS = 500            # the most folders, and the most videos, shown in one listing
MAX_SAVED = 40             # the most folders that may be kept in the list


def enabled(user) -> bool:
    """May the person asking look through this computer's folders?"""
    if not config.LOCAL_PICK or user is None:     # nobody is asking: nothing is shown, whatever the settings say
        return False
    if not config.ACCOUNTS:                       # your own computer, no sign-in: you are the owner
        return True
    return bool(getattr(user, "is_admin", False)) and not getattr(user, "disabled", False)


# ---------------------------------------------------------------- the folders on offer
def _list_file() -> Path:
    return config.DATA_DIR / "local.json"


def _saved() -> list:
    """Folders added from the dashboard, kept in the data folder."""
    try:
        got = json.loads(_list_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for p in got.get("folders") or []:
        if isinstance(p, str) and p.strip() and p.strip() not in out:
            out.append(p.strip())
    return out[:MAX_SAVED]


def _save(folders: list):
    tmp = _list_file().with_suffix(".tmp")
    tmp.write_text(json.dumps({"folders": folders}, indent=1), encoding="utf-8")
    tmp.replace(_list_file())


def _usual() -> list:
    """The folders videos are usually in, on whichever kind of computer this is."""
    home = Path(os.path.expanduser("~"))
    out = []
    for base in (home, home / "OneDrive"):        # Windows often moves these into OneDrive
        for name in ("Downloads", "Videos", "Movies", "Desktop"):
            p = base / name
            try:
                if p.is_dir() and p not in out:
                    out.append(p)
            except OSError:
                continue
    return out


def _expand(raw: str) -> Path:
    return Path(os.path.expanduser(str(raw or "").strip().strip('"')))


def _label(p: Path) -> str:
    return p.name or (p.drive or str(p)).rstrip("\\/") or "This computer"


def roots() -> list:
    """Every folder the picker may open: the ones added here, the ones named in .env, then the usual ones."""
    out, seen = [], set()
    for raw, saved in ([(p, True) for p in _saved()] + [(p, False) for p in config.LOCAL_FOLDERS]):
        p = _expand(raw)
        try:
            key = str(p.resolve()).lower()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": _label(p), "path": str(p), "saved": bool(saved), "there": p.is_dir()})
    for p in _usual():
        try:
            key = str(p.resolve()).lower()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": _label(p), "path": str(p), "saved": False, "there": True})
    return out


def _root_paths() -> list:
    out = []
    for r in roots():
        try:
            out.append(_expand(r["path"]).resolve())
        except OSError:
            continue
    return out


def _in_roots(p: Path) -> bool:
    return any(p == r or r in p.parents for r in _root_paths())


def inside(path: str) -> Path:
    """The folder (or file) a path points at, checked. It has to be one of the folders on offer, or inside one:
    a path typed into the page is never followed anywhere else on the machine."""
    raw = str(path or "").strip().strip('"')
    if not raw:
        raise ValueError("Choose a folder first.")
    if len(raw) > 400:
        raise ValueError("That path is too long.")
    try:
        p = _expand(raw).resolve()
    except OSError:
        raise ValueError("That folder can't be reached right now.")
    if not _in_roots(p):
        raise ValueError("Anhy Clips may only look in the folders listed here. Add that folder first, then open it.")
    if not p.exists():
        raise ValueError("That folder isn't there any more. It may have been moved, or its drive unplugged.")
    return p


def add_folder(path: str) -> dict:
    """Keep a folder in the list, so its videos can be picked from now on."""
    raw = str(path or "").strip().strip('"')
    if not raw:
        raise ValueError("Choose a folder to add.")
    p = _expand(raw)
    if not p.is_absolute():
        raise ValueError("Give the whole path of the folder, for example D:\\Videos or /home/you/Downloads.")
    if not p.is_dir():
        raise ValueError("That isn't a folder, or Anhy Clips can't reach it.")
    try:
        p = p.resolve()
    except OSError:
        raise ValueError("That folder can't be reached right now.")
    folders = _saved()
    if any(f == str(p) or (_expand(f).exists() and _expand(f).resolve() == p) for f in folders):
        return {"path": str(p), "name": _label(p), "roots": roots()}
    if len(folders) >= MAX_SAVED:
        raise ValueError(f"Only {MAX_SAVED} folders can be kept here. Remove one first.")
    folders.append(str(p))
    _save(folders)
    return {"path": str(p), "name": _label(p), "roots": roots()}


def remove_folder(path: str) -> dict:
    """Take a folder out of the list. Nothing inside it is touched."""
    raw = str(path or "").strip().strip('"')
    p = _expand(raw)
    try:
        want = str(p.resolve())
    except OSError:
        want = str(p)
    _save([f for f in _saved() if f != raw and str(_expand(f)) != want])
    return {"roots": roots()}


def pick_window() -> str:
    """The computer's own "choose a folder" window, when it can be shown. "" if it was closed or isn't available."""
    from . import storage
    return storage.pick_folder()


# ---------------------------------------------------------------- looking in a folder
def _row(p: Path) -> dict:
    st = p.stat()
    return {"name": p.name, "path": str(p), "size": st.st_size, "mtime": st.st_mtime}


def browse(path: str = "") -> dict:
    """What is in a folder: its subfolders and its videos. With no folder, the folders on offer are listed."""
    listed = roots()
    if not str(path or "").strip():
        return {"path": "", "name": "", "parent": "", "roots": listed, "folders": [], "videos": [], "cut": False,
                "chosen": ""}
    d = inside(path)
    chosen = ""
    if d.is_file():                     # a whole path to a video was pasted: show the folder it is in
        chosen, d = d.name, d.parent
    folders, videos = [], []
    try:
        entries = list(d.iterdir())
    except OSError:
        raise ValueError("That folder can't be opened. Check it is still there and that Anhy Clips may read it.")
    for p in sorted(entries, key=lambda x: x.name.lower()):
        if p.name.startswith((".", "$")) or p.name.lower() in SKIP_DIRS:
            continue
        try:
            if p.is_dir():
                folders.append({"name": p.name, "path": str(p)})
            elif p.suffix.lower() in VIDEO_EXTS:    # a download still in progress (.part, .crdownload) is not a video yet
                videos.append(_row(p))
        except OSError:
            continue
    videos.sort(key=lambda v: -v["mtime"])          # newest first: that is the one just downloaded
    cut = len(folders) > MAX_ITEMS or len(videos) > MAX_ITEMS
    up = d.parent
    return {"path": str(d), "name": _label(d), "parent": str(up) if up != d and _in_roots(up) else "",
            "roots": listed, "folders": folders[:MAX_ITEMS], "videos": videos[:MAX_ITEMS], "cut": cut,
            "chosen": chosen}


def info(path: str) -> dict:
    """One video: its name, its size, how long it is, and whether Anhy Clips can read it at all."""
    p = inside(path)
    _check_video(p)
    try:
        out = _row(p)
    except OSError:
        raise ValueError("That file can't be read right now. Check it is still there.")
    out.update(duration=0, width=0, height=0, problem="",
               # changed in the last half minute: it may be a download that has not finished yet
               fresh=time.time() - out["mtime"] < 30)
    try:
        got = media.probe(p)
        out.update(duration=got["duration"], width=got["width"], height=got["height"])
    except Exception as e:                          # noqa: BLE001 - said on the row, in plain words
        from . import safe
        out["problem"] = safe.friendly(e, 160)
    return out


def _check_video(p: Path):
    if not p.is_file():
        raise ValueError("That isn't a video file.")
    if p.suffix.lower() not in VIDEO_EXTS:
        raise ValueError("That file isn't a video Anhy Clips can read (mp4, mov, mkv, webm, avi, mpg, ts or flv).")
    try:
        if p.stat().st_size == 0:
            raise ValueError("That file is empty. It may still be downloading: wait for it to finish and try again.")
    except OSError:
        raise ValueError("That file can't be read right now. Check it is still there.")


def thumb_file(path: str):
    """A small picture of one video, for the picker and for the look chooser. Made once and kept in the temporary
    folder, so the same unchanged file is never drawn twice. None when no picture could be made."""
    p = inside(path)
    if not p.is_file():
        return None
    try:
        st = p.stat()
    except OSError:
        return None
    key = hashlib.md5(f"{p}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8", "replace")).hexdigest()
    d = config.TMP_DIR / "local-thumbs"
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{key}.jpg"
    if out.is_file():
        return out
    tmp = d / f"{key}.{os.urandom(3).hex()}.tmp.jpg"      # ffmpeg picks the picture format from the ending
    try:
        at = 1.0
        try:
            at = min(3.0, (media.probe(p).get("duration") or 0) / 3)
        except Exception:                           # noqa: BLE001 - a guess is enough for a small picture
            pass
        media.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(p), "-frames:v", "1",
                   "-vf", "scale=480:-2", "-q:v", "4", str(tmp)], timeout=120)
        if tmp.is_file() and tmp.stat().st_size:
            tmp.replace(out)
            return out
    except Exception:                               # noqa: BLE001 - the row is still useful without a picture
        pass
    finally:
        tmp.unlink(missing_ok=True)
    return None


def take(path: str, job_dir: Path) -> tuple:
    """Put a picked video into its job's own folder, without downloading or sending it again.

    The file is linked where it is: on the same drive that is instant and takes no extra room, and the clips carry
    on working even if the original is deleted afterwards. A drive that cannot be linked to (a memory stick, a
    network folder, another filesystem) leaves the video where it is and works on it there, which is why the
    answer says whether it was linked. Returns (the file to work on, True when it was linked into place)."""
    p = inside(path)
    _check_video(p)
    dest = Path(job_dir) / ("source" + p.suffix.lower())
    if dest.is_file():                              # taken already: a job picked up after a restart
        return dest, True
    try:
        os.link(p, dest)
        return dest, True
    except (OSError, NotImplementedError, AttributeError):
        return p, False


def sweep():
    """Forget pictures of videos that have gone, so the temporary folder does not fill up with them."""
    d = config.TMP_DIR / "local-thumbs"
    if not d.is_dir():
        return
    for f in d.glob("*.jpg"):
        try:
            if time.time() - f.stat().st_mtime > 7 * 86400:
                f.unlink()
        except OSError:
            pass
