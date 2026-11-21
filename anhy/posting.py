"""Post queue: decides when each finished clip goes to which account, posts it, and tracks views."""
import secrets
import threading
import time
from pathlib import Path

import json

from . import accounts, alerts, config, creators, ctx, jobs, live

_lock = threading.RLock()
_FILE = config.DATA_DIR / "posts.json"
try:
    _posts: list = json.loads(_FILE.read_text(encoding="utf-8")) if _FILE.exists() else []
except ValueError:
    _posts = []


def _mine() -> list:
    u = ctx.uid()
    return [p for p in _posts if p.get("user") == u]
OPEN = ("scheduled", "posting", "sent")   # "sent": Buffer has taken it and has not published it yet


def _save():
    with _lock:
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_posts, indent=1), encoding="utf-8")
        tmp.replace(_FILE)


def forget(uid: str):
    """Drop everything queued or recorded for a deleted account, so nothing is posted or retried for it any more."""
    with _lock:
        for p in _posts:
            if p.get("user") == uid:
                p["status"] = "deleted"          # the posting loop may be holding this one already: it then skips it
        _posts[:] = [p for p in _posts if p.get("user") != uid]
        _save()


def _day(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


def _mirror(p):
    """Copy the post's state onto its clip so the clip card can show it."""
    job = jobs.get(p["job"])
    clip = next((c for c in job["clips"] if c["id"] == p["clip"]), None) if job else None
    if clip is not None:
        clip.setdefault("posts", {})[p["id"]] = {k: p.get(k) for k in ("account_name", "platform", "status", "url", "due_at", "error", "instagram_tags")}
        jobs.save(job)


def _next_slot(account_id, gap_min, cap):
    """Earliest time that respects the gap between posts and the daily cap for this account."""
    mine = [p for p in _mine() if p["account"] == account_id and p["status"] in OPEN + ("posted",)]
    t = max(time.time(), max((p.get("posted_at") or p["due_at"] for p in mine), default=0) + gap_min * 60)
    for _ in range(30):
        if sum(1 for p in mine if _day(p.get("posted_at") or p["due_at"]) == _day(t)) < cap:
            break
        lt = time.localtime(t + 86400)
        t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 9, 0, 0, 0, 0, -1))  # 9:00 next day
    return t


def _is_ig_account(acc: dict) -> bool:
    return bool(acc) and (acc.get("platform") == "instagram"
                          or (acc.get("platform") == "buffer" and acc.get("service") == "instagram"))


def enqueue(job, clip, account_ids, now=False, instagram_tags=None) -> list:
    c = creators.get(job["creator"]) or creators.DEFAULTS
    tags = accounts.clean_instagram_tags(instagram_tags)
    made = []
    with _lock:
        for aid in account_ids:
            acc = accounts.get(aid)
            if not acc:
                continue
            if any(p["job"] == job["id"] and p["clip"] == clip["id"] and p["account"] == aid
                   and p["status"] in OPEN + ("posted",) for p in _mine()):
                continue  # never post the same clip to the same account twice
            due = time.time() if now else _next_slot(aid, c["post_gap_min"], c["daily_cap"])
            p = {"id": secrets.token_hex(8), "user": ctx.uid(), "job": job["id"], "clip": clip["id"], "creator": job["creator"],
                 "account": aid, "account_name": acc["name"], "platform": acc["platform"], "status": "scheduled",
                 "due_at": due, "created": time.time(), "title": clip.get("title") or clip.get("hook") or "Clip",
                 "score": clip.get("score", 0), "tries": 0, "url": "", "remote_id": "", "error": None, "views": None}
            if tags and _is_ig_account(acc):
                p["instagram_tags"] = list(tags)
            _posts.append(p)
            made.append(p)
        if made and tags:
            clip["instagram_tags"] = list(tags)
        _save()
    for p in made:
        _mirror(p)
    return made


def on_clip_ready(job, clip):
    """Called once when a clip first finishes rendering."""
    c = creators.get(job["creator"])
    if not c or not c["autopost"] or not c["accounts"]:
        return
    if clip.get("score", 0) < c["post_min_score"]:
        return  # includes hand-made clips (score 0): those wait for you to press Post
    made = enqueue(job, clip, c["accounts"])
    if made:
        creators.log(c["id"], f"Queued for {len(made)} account(s): {clip.get('title') or clip.get('hook')}")


def _caption(clip, creator):
    tags = " ".join("#" + h for h in clip.get("hashtags", []))
    return "\n\n".join(x for x in (clip.get("description", ""), tags, creator.get("post_footer", "")) if x)


def _post(p):
    job = jobs.get(p["job"])
    clip = next((c for c in job["clips"] if c["id"] == p["clip"]), None) if job else None
    acc = accounts.get(p["account"])
    try:
        if not clip or clip.get("status") != "done":
            raise RuntimeError("The clip no longer exists or is not finished.")
        if not acc:
            raise RuntimeError("That account was disconnected.")
        path = Path(job["dir"]) / "clips" / clip["file"]
        creator = creators.get(job["creator"]) or dict(creators.DEFAULTS, name="clips")
        title, cap = clip.get("title") or clip.get("hook") or "Clip", _caption(clip, creator)
        ig_tags = accounts.clean_instagram_tags(p.get("instagram_tags"))
        if acc["platform"] == "youtube":
            shorts = "" if any(str(tag).lower() == "shorts" for tag in clip.get("hashtags", [])) else "\n\n#Shorts"
            rid, url = accounts.youtube_upload(acc, path, title, cap + shorts, clip.get("hashtags", []), creator["privacy"])
        elif acc["platform"] == "instagram":
            rid, url = accounts.instagram_upload(acc, path, f"{title}\n\n{cap}", job["id"], user_tags=ig_tags)
        elif acc["platform"] == "tiktok":
            rid, url = accounts.tiktok_upload(acc, path, f"{title}\n\n{cap}", creator["privacy"])
        elif acc["platform"] == "buffer":
            rid, url = accounts.buffer_upload(acc, path, f"{title}\n\n{cap}", title, job["id"], creator["privacy"],
                                              user_tags=ig_tags if acc.get("service") == "instagram" else None)
            # Buffer taking the post is not the same as the network having it. It stays "sent" (shown as "Sent to
            # Buffer") until Buffer says it went out, or says why it did not: _buffer_settle keeps asking.
            sent = accounts.buffer_post_state(rid)
            if sent["status"] == "error":
                raise RuntimeError(sent["error"] or "Buffer could not publish this.")
            url = url or sent["url"]
            p.update(status="sent", posted_at=time.time(), remote_id=rid, url=url, error=None)
            _save()
            _mirror(p)
            creators.log(p["creator"], f"Sent to Buffer for {acc['name']}: {title}")
            return
        else:
            raise RuntimeError("That kind of account is no longer supported.")
        p.update(status="posted", posted_at=time.time(), remote_id=rid, url=url, error=None)
        creators.log(p["creator"], f"Posted to {acc['name']}: {title}")
    except Exception as e:  # noqa: BLE001
        p["tries"] += 1
        p["error"] = str(e)[-300:]
        if p["tries"] < 3 and clip and acc:
            p.update(status="scheduled", due_at=time.time() + 600)  # try again in 10 minutes
        else:
            p["status"] = "failed"
            creators.log(p["creator"], f"Post failed on {p['account_name']}: {p['error']}")
            alerts.notify(f"post failed on {p['account_name']} ({p['title']}): {p['error'][:200]}", kind="post")
    _save()
    _mirror(p)


def _run():
    while True:
        time.sleep(15)
        due = [p for p in list(_posts) if p["status"] == "scheduled" and p["due_at"] <= time.time() and p.get("user")]
        for p in due:
            try:
                with ctx.as_user(p["user"]):
                    if accounts.settings()["paused"]:
                        continue
                    with _lock:
                        if p["status"] != "scheduled":
                            continue
                        p["status"] = "posting"
                    _post(p)
            except Exception:  # noqa: BLE001
                pass
        try:
            _buffer_settle()
        except Exception:  # noqa: BLE001
            pass


BUFFER_CHECK_MIN = 20      # how often Buffer is asked what became of a post it took
BUFFER_GIVE_UP_HOURS = 6   # after this, a post Buffer has not sent is called out rather than waited on


def _buffer_settle():
    """Ask Buffer what became of the posts it took and has not sent yet. Buffer publishes by itself, so the queue
    has to look: without this a post Buffer quietly dropped stayed on screen as posted for ever."""
    waiting = [p for p in list(_posts) if p["status"] == "sent" and p.get("user") and p.get("remote_id")]
    users = {p["user"] for p in waiting}
    for uid in users:
        mine = [p for p in waiting if p["user"] == uid]
        if any(time.time() - p.get("checked", 0) < BUFFER_CHECK_MIN * 60 for p in mine):
            continue
        with ctx.as_user(uid):
            for p in mine:
                try:
                    acc = accounts.get(p["account"])
                    if not acc or acc.get("platform") != "buffer":
                        p.update(status="failed", error="That Buffer channel was disconnected before the post went out.")
                        continue
                    got = accounts.buffer_post_state(p["remote_id"])
                except Exception:  # noqa: BLE001 - Buffer unreachable: ask again later
                    p["checked"] = time.time()
                    continue
                p["checked"] = time.time()
                st = got["status"]
                if st == "sent":
                    p.update(status="posted", posted_at=time.time(), url=got["url"] or p.get("url") or "", error=None)
                    creators.log(p["creator"], f"Posted to {p['account_name']} through Buffer: {p['title']}")
                elif st in ("error", "draft") or (st in ("scheduled", "sending") and
                                                  time.time() - (p.get("posted_at") or time.time()) > BUFFER_GIVE_UP_HOURS * 3600):
                    p["tries"] += 1
                    msg = got["error"] or ("Buffer has not sent this post." if st in ("scheduled", "sending")
                                           else "Buffer did not publish this post; open the channel in Buffer to see why.")
                    p.update(status="failed", error=msg[:300])
                    creators.log(p["creator"], f"Post failed on {p['account_name']}: {p['error']}")
                    alerts.notify(f"post failed on {p['account_name']} ({p['title']}): {p['error'][:200]}", kind="post")
        _save()
        for p in mine:
            _mirror(p)


def _stats_for(recent):
    for aid in {p["account"] for p in recent if p["platform"] == "youtube"}:
        acc = accounts.get(aid)
        if acc:
            views = accounts.youtube_views(acc, [p["remote_id"] for p in recent if p["account"] == aid])
            for p in recent:
                if p["account"] == aid and p["remote_id"] in views:
                    p["views"] = views[p["remote_id"]]
    for p in recent:
        acc = accounts.get(p["account"]) if p["platform"] == "instagram" else None
        if acc:
            v = accounts.instagram_views(acc, p["remote_id"])
            if v is not None:
                p["views"] = v


def _stats():
    while True:
        for uid in {p.get("user") for p in _posts if p.get("user")}:
            try:
                with ctx.as_user(uid):
                    _stats_for([p for p in _mine() if p["status"] == "posted" and time.time() - p["posted_at"] < 14 * 86400])
            except Exception:  # noqa: BLE001
                pass
        _save()
        time.sleep(1800)


def cancel(pid):
    with _lock:
        p = next((p for p in _mine() if p["id"] == pid), None)
        if not p or p["status"] != "scheduled":
            raise ValueError("Only posts that are still waiting can be cancelled.")
        p["status"] = "cancelled"
        _save()
    _mirror(p)


def retry(pid):
    with _lock:
        p = next((p for p in _mine() if p["id"] == pid), None)
        if not p or p["status"] not in ("failed", "cancelled"):
            raise ValueError("Only failed or cancelled posts can be retried.")
        # A retry is a fresh post: the old one on Buffer (if there was one) is not asked about again, and is never
        # sent twice. A post Buffer has already taken and sent cannot be retried, so it cannot go out twice either.
        p.update(status="scheduled", due_at=time.time(), tries=0, error=None, remote_id="", checked=0, url="")
        _save()
    _mirror(p)


def check(pid) -> dict:
    """Ask the network (Buffer) what became of a post right now, instead of waiting for the next look. The answer is
    saved on the post, so the queue shows it straight away. A post still on its way back says so."""
    with _lock:
        p = next((p for p in _mine() if p["id"] == pid), None)
        if not p or not p.get("remote_id"):
            raise ValueError("That post has not been sent anywhere yet.")
        if p["platform"] != "buffer":
            raise ValueError("Only posts sent through Buffer can be checked; the other networks report at once.")
        if p["status"] not in ("sent", "posted", "failed"):
            raise ValueError("That post is still on its way.")
    got = accounts.buffer_post_state(p["remote_id"])
    with _lock:
        st = got["status"]
        if st == "sent":
            p.update(status="posted", posted_at=time.time(), url=got["url"] or p.get("url") or "", error=None)
        elif st in ("error", "draft"):
            p.update(status="failed", error=(got["error"] or "Buffer did not publish this post.")[:300])
        p["checked"] = time.time()
        _save()
    _mirror(p)
    return {"status": p["status"], "error": p.get("error") or "", "url": p.get("url") or ""}


def overview() -> dict:
    today = _day(time.time())
    _posts = _mine()  # everything below is about the signed-in user only
    rows = []
    all_cs, all_js = creators.all_creators(), jobs.list_jobs()
    for c in all_cs:
        js = [j for j in all_js if j["creator"] == c["id"]]
        clips = [k for j in js for k in j["clips"] if k.get("status") == "done"]
        ps = [p for p in _posts if p["creator"] == c["id"]]
        views = sum(p["views"] or 0 for p in ps)
        rows.append({
            "id": c["id"], "name": c["name"],
            "state": "recording" if live.active_for(c["id"]) else "waiting" if c["auto_watch"] and c["channel"] else "idle",
            "autopost": c["autopost"] and bool(c["accounts"]),
            "clips_today": sum(1 for k in clips if _day(k.get("made_at", 0)) == today), "clips_total": len(clips),
            "posted_today": sum(1 for p in ps if p["status"] == "posted" and _day(p["posted_at"]) == today),
            "posted_total": sum(1 for p in ps if p["status"] == "posted"),
            "queued": sum(1 for p in ps if p["status"] in OPEN), "failed": sum(1 for p in ps if p["status"] == "failed"),
            "views": views, "earnings": round(views / 1000 * c["rate_per_1k"], 2)})
    names = {c["id"]: c["name"] for c in all_cs}
    queue = sorted((p for p in _posts if p["status"] in OPEN), key=lambda p: p["due_at"])
    done = sorted((p for p in _posts if p["status"] in ("posted", "failed")), key=lambda p: -(p.get("posted_at") or p["due_at"]))[:15]
    show = [{**{k: v for k, v in p.items() if k != "user"}, "creator_name": names.get(p["creator"], "")} for p in queue + done]
    # last 14 days, oldest first, for the home graph
    days = [_day(time.time() - 86400 * i) for i in range(13, -1, -1)]
    made, posted, views = {d: 0 for d in days}, {d: 0 for d in days}, {d: 0 for d in days}
    for j in all_js:
        for k in j["clips"]:
            d = _day(k.get("made_at") or j["created"])
            if k.get("status") == "done" and d in made:
                made[d] += 1
    for p in _posts:
        if p["status"] == "posted" and _day(p["posted_at"]) in posted:
            posted[_day(p["posted_at"])] += 1
            views[_day(p["posted_at"])] += p["views"] or 0
    daily = [{"day": d, "clips": made[d], "posted": posted[d], "views": views[d]} for d in days]
    return {"paused": accounts.settings()["paused"], "creators": rows, "posts": show, "daily": daily,
            "alerts": alerts.configured(), "recording": len(live.active_mine())}


def digest_text() -> str:
    o = overview()
    lines = ["Anhy Clips daily summary"]
    for r in o["creators"]:
        lines.append(f"- {r['name']}: {r['state']}, {r['clips_today']} clips today, {r['posted_today']} posted today, "
                     f"{r['queued']} waiting, {r['failed']} failed, {r['views']} views")
    if not o["creators"]:
        lines.append("No creators set up yet.")
    if o["paused"]:
        lines.append("Posting is paused.")
    return "\n".join(lines)


def start():
    with _lock:
        for p in _posts:
            if p["status"] == "posting":  # interrupted mid-upload last time
                p.update(status="failed", error="The studio closed during this upload. Check the account, then retry.")
        _save()
    jobs.on_clip_ready.append(on_clip_ready)
    threading.Thread(target=_run, daemon=True).start()
    threading.Thread(target=_stats, daemon=True).start()
    threading.Thread(target=alerts.digest_loop, args=(digest_text,), daemon=True).start()
    threading.Thread(target=alerts.keep_awake_loop,
                     args=(lambda: bool(live.active()) or any(p["status"] in OPEN for p in _posts),), daemon=True).start()
