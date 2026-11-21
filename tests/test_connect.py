"""One-click Connect for YouTube, Instagram, TikTok and Buffer, and posting to each. The platforms are replaced by
stand-ins answering the way their documentation says, so nothing leaves this machine."""
import json, os, sys, tempfile, time
from urllib.parse import parse_qs, urlparse
os.environ["ANHY_DATA"] = tempfile.mkdtemp(prefix="pc-connect-")
os.environ.update(DATABASE_URL="", ANHY_ACCOUNTS="1", ANHY_PUBLIC_URL="https://clips.example.com", ANHY_VERIFY_EMAIL="0",
                  GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="", GOOGLE_CLIENT_FILE="", ANHY_IG_APP_ID="", ANHY_IG_APP_SECRET="",
                  ANHY_TIKTOK_CLIENT_KEY="", ANHY_TIKTOK_CLIENT_SECRET="", ANHY_BUFFER_CLIENT_ID="", ANHY_BUFFER_CLIENT_SECRET="")
sys.path.insert(0, ".")
from pathlib import Path
import sqlalchemy as sa
from fastapi.testclient import TestClient
from anhy import accounts, auth, config, ctx, db, server
db.init()
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

BASE = "https://clips.example.com"
ORIGIN = {"Origin": BASE}
def person(email, admin=False):
    u, _ = auth._create(email, email.split("@")[0], password_hash="x")
    if admin:
        with db.engine.begin() as c:
            c.execute(db.users.update().where(db.users.c.id == u.id).values(is_admin=True))
    cl = TestClient(server.app, base_url=BASE, follow_redirects=False)
    cl.cookies.set(auth.COOKIE, auth.start_session(u.id))
    return u, cl
boss, a = person("boss@example.com", admin=True)
user, b = person("user@example.com")

# ---- nothing is set up: the buttons say so, and only an admin may set it up
st = b.get("/api/status").json()
check("nothing can be connected before setup", st["connect"], {"youtube": False, "instagram": False, "tiktok": False, "buffer": False})
check("a normal user cannot see the setup", b.get("/api/apps").status_code, 404)
check("...or change it", b.put("/api/apps", json={"platform": "tiktok", "client_id": "abcd", "client_secret": "x"}, headers=ORIGIN).status_code, 404)
r = b.get("/oauth/instagram/start")
check("Connect before setup comes back with a reason", (r.status_code, "connect_error" in r.headers.get("location", "")), (303, True))
check("setup from another website is refused", a.put("/api/apps", json={"platform": "tiktok", "client_id": "abcd1234", "client_secret": "s"},
                                                    headers={"Origin": "https://evil.example"}).status_code, 403)
check("a bad id is refused", a.put("/api/apps", json={"platform": "tiktok", "client_id": "a b<", "client_secret": "s"}, headers=ORIGIN).status_code, 400)
for pf, cid in (("youtube", "g-client-123.apps"), ("instagram", "1234567890"), ("tiktok", "awtiktokkey123")):
    r = a.put("/api/apps", json={"platform": pf, "client_id": cid, "client_secret": f"{pf}-secret-value"}, headers=ORIGIN)
    check(f"{pf} app saved", (r.status_code, r.json()[pf]["ready"]), (200, True))
apps = a.get("/api/apps").json()
check("secrets are never sent back", any("secret-value" in json.dumps(v) for v in apps.values()), False)
check("each platform shows the address to register", apps["tiktok"]["redirect"], BASE + "/oauth/tiktok/callback")
r = a.put("/api/apps", json={"platform": "tiktok", "client_id": "", "client_secret": "tiktok-secret-value2"}, headers=ORIGIN)
check("a new secret alone keeps the saved id", accounts.app_keys("tiktok"), ("awtiktokkey123", "tiktok-secret-value2"))
check("everyone can connect now", b.get("/api/status").json()["connect"],
      {"youtube": True, "instagram": True, "tiktok": True, "buffer": False})
check("Buffer has no app here yet, and that is no problem: its key comes from Buffer", accounts.can_connect("buffer"), False)

# ---- the platforms, as stand-ins
class R:
    def __init__(self, code, data=None, headers=None, text=None):
        self.status_code, self._d, self.headers = code, data, headers or {}
        self.text = text if text is not None else json.dumps(data)
    def json(self):
        if self._d is None:
            raise ValueError
        return self._d
calls, uploads, tt = [], [], {"audited": False, "status": 0}
buf = {"channels": [], "posts": [], "reject": False, "denied": False, "token_form": {}, "state": None, "queries": []}
def buffer_api(payload):
    """Buffer's one address: every question is a GraphQL question, and the question says what is wanted."""
    q, v = str(payload.get("query", "")), payload.get("variables") or {}
    if buf["denied"]:
        return R(401, {"errors": [{"message": "Unauthorized"}]})
    if "organizations" in q:
        return R(200, {"data": {"account": {"organizations": [{"id": "org1", "name": "Clip Co"}]}}})
    if "channels(" in q:
        return R(200, {"data": {"channels": buf["channels"] if v.get("org") == "org1" else []}})
    if "createPost" in q:
        buf["posts"].append(v.get("input") or {})
        if buf["reject"]:
            return R(200, {"data": {"createPost": {"message": "Queue limit reached for this channel"}}})
        return R(200, {"data": {"createPost": {"post": {"id": "bufpost1", "status": "sending", "dueAt": None,
                                                        "externalLink": None}}}})
    if "deletePost" in q:
        buf.setdefault("deleted", []).append(v.get("id"))
        return R(200, {"data": {"deletePost": {"post": {"id": v.get("id")}}}})
    if "query OnePost" in q:
        buf["queries"].append(v.get("id"))
        st = buf["state"] or {"status": "sending"}
        return R(200, {"data": {"post": {"status": st["status"], "dueAt": None, "sentAt": st.get("sentAt"),
                                         "externalLink": st.get("externalLink"),
                                         "error": ({"message": st["error"], "supportUrl": "https://support.buffer.com"} if st.get("error") else None)}}})
    return R(200, {"errors": [{"message": "Cannot query that field."}]})
def post(url, data=None, json=None, headers=None, params=None, timeout=None, content=None):
    calls.append(("POST", url, data or json, headers or {}))
    if url == "https://oauth2.googleapis.com/token":
        return R(200, {"access_token": "yt-access", "refresh_token": "yt-refresh", "expires_in": 3600})
    if url == "https://auth.buffer.com/token":
        buf["token_form"] = data or {}
        if data.get("grant_type") == "refresh_token":
            return R(200, {"access_token": "buf-access-2", "refresh_token": "buf-refresh-2", "expires_in": 3600})
        if data.get("code") != "bufcode":
            return R(400, {"error": "invalid_grant", "error_description": "Authorization code has expired"})
        return R(200, {"access_token": "buf-access", "refresh_token": "buf-refresh", "expires_in": 3600,
                       "scope": "posts:write account:read offline_access"})
    if url == "https://api.buffer.com":
        return buffer_api(json)
    if url == "https://api.instagram.com/oauth/access_token":
        if data["code"] != "igcode":
            return R(400, {"error_message": "bad code"})
        return R(200, {"data": [{"access_token": "ig-short", "user_id": "17841", "permissions": "instagram_business_basic,instagram_business_content_publish"}]})
    if url.endswith("/oauth/token/"):
        if data.get("grant_type") == "refresh_token":
            return R(200, {"access_token": "tt-access-2", "refresh_token": "tt-refresh-2", "expires_in": 7200, "open_id": "oid", "scope": "user.info.basic,video.publish"})
        return R(200, {"access_token": "tt-access", "refresh_token": "tt-refresh", "expires_in": 7200, "open_id": "oid", "scope": "user.info.basic,video.publish"})
    if url.endswith("/creator_info/query/"):
        return R(200, {"data": {"creator_username": "streamerclips", "creator_nickname": "Streamer Clips",
                                "privacy_level_options": ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]}, "error": {"code": "ok"}})
    if url.endswith("/post/publish/video/init/"):
        lv = json["post_info"]["privacy_level"]
        tt["init"] = json
        if lv != "SELF_ONLY" and not tt["audited"]:
            return R(403, {"error": {"code": "unaudited_client_can_only_post_to_private_accounts", "message": "private only"}})
        return R(200, {"data": {"publish_id": "v_pub_1", "upload_url": "https://open-upload.tiktokapis.com/video/?upload_id=1"}, "error": {"code": "ok"}})
    if url.endswith("/post/publish/status/fetch/"):
        tt["status"] += 1
        if tt["status"] < 2:
            return R(200, {"data": {"status": "PROCESSING_UPLOAD"}, "error": {"code": "ok"}})
        return R(200, {"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7300] if tt["audited"] else []}, "error": {"code": "ok"}})
    if url.endswith("/17841/media"):
        return R(200, {"id": "container1"})
    if url.endswith("/17841/media_publish"):
        return R(200, {"id": "media1"})
    return R(404, {})
def get(url, params=None, headers=None, timeout=None):
    calls.append(("GET", url, params, headers or {}))
    if url.endswith("/youtube/v3/channels"):
        return R(200, {"items": [{"id": "UC123", "snippet": {"title": "My Channel"}}]})
    if url == "https://graph.instagram.com/access_token":
        return R(200, {"access_token": "ig-long", "token_type": "bearer", "expires_in": 5183944})
    if url == "https://graph.instagram.com/me":
        return R(200, {"user_id": "17841", "username": "clipper", "account_type": "MEDIA_CREATOR"})
    if url.endswith("/container1"):
        return R(200, {"status_code": "FINISHED"})
    if url.endswith("/media1"):
        return R(200, {"permalink": "https://www.instagram.com/reel/abc/"})
    return R(404, {})
def put(url, content=None, headers=None, timeout=None):
    uploads.append((url, len(content), headers))
    return R(201, None, text="")
accounts.httpx.post, accounts.httpx.get, accounts.httpx.put = post, get, put
accounts.time.sleep = lambda s: None

# ---- connecting: the button goes to the platform's own page, and back
def connect(cl, pf, code="code1"):
    r = cl.get(f"/oauth/{pf}/start")
    loc = urlparse(r.headers["location"])
    state = parse_qs(loc.query)["state"][0]
    back = cl.get(f"/oauth/{pf}/callback", params={"code": code, "state": state})
    return loc, back
loc, back = connect(b, "youtube")
check("YouTube: Google's page, asking to upload", (loc.netloc, "youtube.upload" in parse_qs(loc.query)["scope"][0]), ("accounts.google.com", True))
check("YouTube: back on Connected apps, connected", (back.status_code, back.headers["location"].startswith("/app/settings/apps?connected=youtube")), (303, True))
loc, back = connect(b, "instagram", "igcode#_")
q = parse_qs(loc.query)
check("Instagram: Instagram's own page, with publishing permission", (loc.netloc, q["client_id"][0], "instagram_business_content_publish" in q["scope"][0]),
      ("www.instagram.com", "1234567890", True))
check("Instagram: connected, with the #_ Instagram adds to the code removed", "connected=instagram" in back.headers["location"])
loc, back = connect(b, "tiktok")
q = parse_qs(loc.query)
check("TikTok: TikTok's own page, asking to post", (loc.netloc, q["client_key"][0], q["scope"][0]), ("www.tiktok.com", "awtiktokkey123", "user.info.basic,video.publish"))
check("TikTok: connected", "connected=tiktok" in back.headers["location"])
accs = {x["platform"]: x for x in b.get("/api/accounts").json()}
check("all three accounts are listed with their names", (accs["youtube"]["name"], accs["instagram"]["name"], accs["tiktok"]["name"]),
      ("My Channel", "@clipper", "@streamerclips"))
check("tokens never reach the page", any(t in b.get("/api/accounts").text for t in ("ig-long", "tt-access", "yt-refresh")), False)
check("someone else's accounts are not mine", a.get("/api/accounts").json(), [])
r = b.get("/oauth/tiktok/callback", params={"code": "x", "state": "made-up"})
check("a made-up return is refused", "connect_error" in r.headers["location"])
r = b.get("/oauth/tiktok/callback", params={"error": "access_denied"})
check("cancelling on the platform says so", "cancelled" in r.headers["location"])
loc = urlparse(a.get("/oauth/tiktok/start").headers["location"])
r = b.get("/oauth/tiktok/callback", params={"code": "c", "state": parse_qs(loc.query)["state"][0]})
check("one person's sign-in cannot be finished by another", "connect_error" in r.headers["location"])
check("an unknown platform does not exist", b.get("/oauth/myspace/start").status_code, 404)

# ---- Buffer: a key from Buffer's own settings, and every channel that key brings with it
CHANS = [{"id": "ch_ig", "name": "@clipper", "service": "instagram", "organizationId": "org1", "type": "profile",
          "isDisconnected": False, "isLocked": False, "isQueuePaused": False, "externalLink": "https://instagram.com/clipper"},
         {"id": "ch_yt", "name": "Clipper Clips", "service": "youtube", "organizationId": "org1", "type": "channel",
          "isDisconnected": False, "isLocked": False, "isQueuePaused": False, "externalLink": ""},
         {"id": "ch_x", "name": "@clipper", "service": "twitter", "organizationId": "org1", "type": "profile",
          "isDisconnected": True, "isLocked": False, "isQueuePaused": False, "externalLink": ""}]
buf.update(channels=CHANS, posts=[], reject=False, denied=False)
def connected():
    with ctx.as_user(user.id):
        return accounts.buffer_connected()
check("Buffer: something that isn't a key is refused", b.post("/api/accounts/buffer", json={"key": "not a key!"}, headers=ORIGIN).status_code, 400)
buf["denied"] = True
r = b.post("/api/accounts/buffer", json={"key": "v1.a-key-buffer-refuses"}, headers=ORIGIN)
buf["denied"] = False
check("Buffer: a key Buffer refuses is not kept", (r.status_code, connected()), (400, False))
r = b.post("/api/accounts/buffer", json={"key": "v1.a-long-buffer-api-key"}, headers=ORIGIN)
check("Buffer: an API key connects it, with no app on this server", (r.status_code, r.json()["channels"]), (200, 3))
rows = {x["id"]: x for x in b.get("/api/accounts").json() if x["platform"] == "buffer"}
check("Buffer: every channel becomes an account of its own", sorted(rows), ["bf_ch_ig", "bf_ch_x", "bf_ch_yt"])
check("Buffer: each says which network it is, for the post queue", rows["bf_ch_ig"]["name"], "@clipper (Instagram)")
check("Buffer: and which Buffer organization it belongs to", rows["bf_ch_yt"]["detail"], "Clip Co")
check("Buffer: a channel Buffer can't post to says why", "Disconnected" in rows["bf_ch_x"]["detail"], True)
check("Buffer: they are all called Buffer, so a creator's Setup groups them", {x["label"] for x in rows.values()}, {"Buffer"})
check("Buffer: the key never reaches the page", "a-long-buffer-api-key" in b.get("/api/accounts").text, False)
check("Buffer: another person gets none of it", a.get("/api/accounts").json(), [])
buf["denied"] = True
r = b.post("/api/accounts/buffer", json={"key": "v1.a-second-key-buffer-refuses"}, headers=ORIGIN)
buf["denied"] = False
check("Buffer: a bad second key leaves the working one alone", (r.status_code, connected()), (400, True))
buf["channels"] = CHANS[:2]                      # the X channel was dropped in Buffer since
r = b.post("/api/accounts/buffer/sync", headers=ORIGIN)
check("Buffer: reading the channels again drops the one that is gone",
      (r.status_code, r.json()["channels"], "bf_ch_x" in {x["id"] for x in r.json()["accounts"]}), (200, 2, False))
buf["channels"] = CHANS
check("Buffer: and brings it back", b.post("/api/accounts/buffer/sync", headers=ORIGIN).json()["channels"], 3)

# ---- "why isn't it posting?": every step a clip takes through Buffer, without posting a clip
r = b.get("/api/accounts/buffer/check")
d = r.json()
check("Buffer check: the whole way through, step by step", (r.status_code, len(d["steps"]) >= 4), (200, True))
check("Buffer check: the sign-in and the channels are the first things it looks at",
      [x["name"] for x in d["steps"]][:2], ["Buffer is connected", "Buffer answered the sign-in"])
check("Buffer check: it posts nothing to Instagram", [p for p in buf["posts"] if p.get("metadata")], [])
dis = next(x for x in d["steps"] if x["name"] == "Every Buffer channel is usable")
check("Buffer check: a channel that is disconnected in Buffer is shown, in Buffer's own words",
      (dis["ok"], "@clipper: disconnected" in dis["detail"]), (False, True))
check("Buffer check: the other channels are still fine, so posting carries on", d["problem"], "")
check("Buffer check: a working Buffer has nothing to report", (d["problem"], d["fix"][:13]), ("", "Buffer looks "))

# ---- Buffer can also be signed in with, through an app registered here
r = a.put("/api/apps", json={"platform": "buffer", "client_id": "buf-client-1234", "client_secret": "buf-secret-value"}, headers=ORIGIN)
check("Buffer: an app for the one-click sign-in can be added too", (r.status_code, r.json()["buffer"]["ready"]), (200, True))
check("Buffer: and then everyone can sign in", b.get("/api/status").json()["connect"]["buffer"], True)
check("Buffer: its secret is never sent back", "buf-secret-value" in a.get("/api/apps").text, False)
loc, back = connect(b, "buffer", "bufcode")
q = parse_qs(loc.query)
check("Buffer: Buffer's own sign-in page, with PKCE and permission to post",
      (loc.netloc, loc.path, q["client_id"][0], q["code_challenge_method"][0], q["scope"][0], len(q["code_challenge"][0]) > 20),
      ("auth.buffer.com", "/auth", "buf-client-1234", "S256", "posts:write account:read offline_access", True))
check("Buffer: signed in, and the page is told how many channels came",
      ("connected=buffer" in back.headers["location"], "channels=3" in back.headers["location"]), (True, True))
check("Buffer: the PKCE value and the redirect went with the code",
      (bool(buf["token_form"].get("code_verifier")), buf["token_form"].get("redirect_uri")), (True, BASE + "/oauth/buffer/callback"))
check("Buffer: tokens never reach the page", any(t in b.get("/api/accounts").text for t in ("buf-access", "buf-refresh")), False)

# ---- the one-hour links Instagram fetches clips from
with ctx.as_user(user.id):
    jd = config.user_dir(user.id) / "jobs" / "job-12345678" / "clips"
jd.mkdir(parents=True)
(jd.parent / "job.json").write_text(json.dumps({"id": "job-12345678", "user": user.id, "dir": str(jd.parent), "clips": [], "status": "done",
                                                "creator": None, "created": time.time(), "options": {}, "source": {"type": "file"}}), encoding="utf-8")
(jd / "clip01.mp4").write_bytes(b"\x00" * 6_000)
from anhy import jobs
jobs.load_user(user.id)
with ctx.as_user(user.id):
    link = accounts.clip_link("job-12345678", "clip01.mp4")
anon = TestClient(server.app, base_url=BASE)
path = urlparse(link).path
check("a signed link serves the clip to anyone, no sign-in", (anon.get(path).status_code, len(anon.get(path).content)), (200, 6000))
bad = path.replace(".mp4", "x.mp4")
check("a changed link does not", anon.get(bad).status_code, 404)
check("a made-up link does not", anon.get("/pub/abc.def.mp4").status_code, 404)
with ctx.as_user(user.id):
    old = accounts.clip_link("job-12345678", "clip01.mp4", seconds=-5)
check("an expired link does not", anon.get(urlparse(old).path).status_code, 404)

# ---- posting
with ctx.as_user(user.id):
    ig, ttacc = accounts.get(accs["instagram"]["id"]), accounts.get(accs["tiktok"]["id"])
    calls.clear()
    rid, url = accounts.instagram_upload(ig, jd / "clip01.mp4", "My caption", "job-12345678")
    made = next(c for c in calls if c[1].endswith("/17841/media"))
    check("Instagram: a Reel from the signed link", (made[2]["media_type"], made[2]["video_url"].startswith(BASE + "/pub/")), ("REELS", True))
    check("Instagram: published, with its link", (rid, url), ("media1", "https://www.instagram.com/reel/abc/"))
    check("Instagram: no user_tags sent when none given", "user_tags" in made[2], False)

    # ---- Instagram username tagging (direct Reel user_tags + Buffer Instagram @mentions + validation + API route)
    check("Instagram tags: clean strips @, splits and dedupes",
          accounts.parse_instagram_tags("@Creator_One, creator_one  @friend.2", strict=True),
          ["Creator_One", "friend.2"])
    bad_tag_err = False
    try:
        accounts.parse_instagram_tags("@good, bad!name", strict=True)
    except ValueError:
        bad_tag_err = True
    check("Instagram tags: invalid username rejected in strict mode", bad_tag_err, True)

    calls.clear()
    rid, url = accounts.instagram_upload(ig, jd / "clip01.mp4", "My caption", "job-12345678",
                                         user_tags=["@Creator_One", "friend.2"])
    made_tagged = next(c for c in calls if c[1].endswith("/17841/media"))
    check("Instagram: user_tags JSON sent on Reel container",
          json.loads(made_tagged[2]["user_tags"]),
          [{"username": "Creator_One", "x": 0.5, "y": 0.5}, {"username": "friend.2", "x": 0.5, "y": 0.5}])

    uploads.clear(); tt.update(audited=False, status=0)
    rid, url = accounts.tiktok_upload(ttacc, jd / "clip01.mp4", "My caption #fyp", "public")
    check("TikTok: an app not yet reviewed posts privately instead of failing", (tt["init"]["post_info"]["privacy_level"], url), ("SELF_ONLY", ""))
    check("TikTok: the whole file in one piece, with the right range", (len(uploads), uploads[0][2]["Content-Range"]), (1, "bytes 0-5999/6000"))
    check("TikTok: the account says why posts are private", "private" in accounts.get(ttacc["id"]).get("detail", ""))
    tt.update(audited=True, status=0)
    rid, url = accounts.tiktok_upload(ttacc, jd / "clip01.mp4", "My caption", "public")
    check("TikTok: once reviewed, public, with the video's link", (tt["init"]["post_info"]["privacy_level"], url),
          ("PUBLIC_TO_EVERYONE", "https://www.tiktok.com/@streamerclips/video/7300"))
    check("TikTok: big files go in pieces, the last taking the rest", accounts._tt_chunks(150_000_123), (10_000_000, 15))
    acc = accounts.get(ttacc["id"]); acc["expires"] = 0; accounts._put(acc)
    accounts._tt_token(accounts.get(ttacc["id"]))
    check("TikTok: an old sign-in is renewed, and the new refresh token kept", accounts.get(ttacc["id"])["refresh_token"], "tt-refresh-2")

    # ---- posting through Buffer: one channel at a time, and Buffer comes and fetches the clip
    bufacc, bufyt = accounts.get("bf_ch_ig"), accounts.get("bf_ch_yt")
    buf["posts"].clear()
    rid, url = accounts.buffer_upload(bufacc, jd / "clip01.mp4", "My caption\n\n#fyp", "My title", "job-12345678", "public")
    sent = buf["posts"][-1]
    check("Buffer: the clip goes out at once, from a signed link on this server",
          (sent["mode"], sent["schedulingType"], sent["channelId"], sent["assets"][0]["video"]["url"].startswith(BASE + "/pub/")),
          ("shareNow", "automatic", "ch_ig", True))
    check("Buffer: one video, with a thumbnail from a second in",
          (len(sent["assets"]), sent["assets"][0]["video"]["metadata"]["thumbnailOffset"]), (1, 1000))
    check("Buffer: an Instagram channel is told the clip is a Reel", sent["metadata"]["instagram"], {"type": "reel", "shouldShareToFeed": True})
    check("Buffer: the post is known by Buffer's id, with no link until it is out", (rid, url), ("bufpost1", ""))

    # ---- what became of a post Buffer took: it is only posted once Buffer says it went out
    buf["state"] = {"status": "sending"}
    got = accounts.buffer_post_state("bufpost1")
    check("Buffer: a post still on its way is not called posted", (got["status"], got["url"]), ("sending", ""))
    buf["state"] = {"status": "sent", "externalLink": "https://www.instagram.com/reel/xyz/"}
    got = accounts.buffer_post_state("bufpost1")
    check("Buffer: once sent, with the network's link", (got["status"], got["url"]), ("sent", "https://www.instagram.com/reel/xyz/"))
    buf["state"] = {"status": "error", "error": "The channel was disconnected", "supportUrl": "https://support.buffer.com"}
    got = accounts.buffer_post_state("bufpost1")
    check("Buffer: a post Buffer could not publish says why, in Buffer's words", (got["status"], got["error"]),
          ("error", "The channel was disconnected"))
    check("Buffer: the answer is read with the error's own fields, never as a bare object",
          "error { message supportUrl }" in accounts.BUF_ONE, True)
    buf["state"] = None

    # ---- the refusals people actually hit are said in plain words
    buf["reject"] = False
    for said, want in (("Invalid post: Instagram posts require a type (post, story, or reel).", "Reel"),
                       ("Failed to fetch image dimensions: Not Found", "ANHY_PUBLIC_URL"),
                       ("Queue limit reached for this channel", "posting limit"),
                       ("The video is too long for Instagram", "Reels")):
        check(f"Buffer: a refusal ({said[:34]}…) says what to do", want in accounts._buf_refusal(said, bufacc), True)

    # ---- sending goes through the queue, and the queue keeps asking Buffer until the post is out
    from anhy import posting, jobs as jobs_mod
    posting._posts[:] = []
    jd2 = Path(os.environ["ANHY_DATA"]) / "qjob"
    (jd2 / "clips").mkdir(parents=True, exist_ok=True)
    (jd2 / "clips" / "clip01.mp4").write_bytes(b"x" * 10)
    job = {"id": "job-abcdefgh", "user": user.id, "creator": None, "dir": str(jd2), "clips": [
        {"id": "clip01", "status": "done", "file": "clip01.mp4", "title": "A title", "start": 0, "end": 20, "score": 90}]}
    jobs_mod._jobs[job["id"]] = job
    clip = job["clips"][0]
    accs_buf = [a["id"] for a in accounts.list_accounts() if a["platform"] == "buffer"]
    with ctx.as_user(user.id):
        made = posting.enqueue(job, clip, accs_buf[:1], now=True)
    check("Queue: the post waits its turn", (len(made), made[0]["status"]), (1, "scheduled"))
    buf["posts"].clear()
    buf["state"] = {"status": "sending"}
    with ctx.as_user(user.id):
        posting._post(made[0])
    check("Queue: Buffer taking it is not the network having it", made[0]["status"], "sent")
    check("Queue: ...so it is still counted as waiting", made[0]["status"] in posting.OPEN, True)
    buf["state"] = {"status": "sent", "externalLink": "https://www.instagram.com/reel/abc/"}
    with ctx.as_user(user.id):
        posting._buffer_settle()
    check("Queue: asking Buffer again marks it posted, with its link", (made[0]["status"], made[0]["url"]),
          ("posted", "https://www.instagram.com/reel/abc/"))
    check("Queue: and it stops being waited on", made[0]["status"] in posting.OPEN, False)
    buf["state"] = {"status": "sent", "externalLink": "https://www.instagram.com/reel/def/"}
    with ctx.as_user(user.id):
        made2 = posting.enqueue(job, clip, accs_buf[:1], now=True)
    check("Queue: the same clip never goes to the same account twice", len(made2), 0)
    posting._posts[:] = []
    # Posting to Instagram (direct + Buffer Instagram) via /api/posts carries instagram_tags into posting
    clip.pop("posts", None)
    clip.pop("instagram_tags", None)
    r_bad = b.post("/api/posts", json={"job": job["id"], "clip": clip["id"],
                                       "accounts": [accs["instagram"]["id"]], "now": True,
                                       "instagram_tags": "@valid_user, bad!handle"}, headers={"Origin": BASE})
    check("API /api/posts: rejects invalid Instagram username with 400", r_bad.status_code, 400)
    r_ok = b.post("/api/posts", json={"job": job["id"], "clip": clip["id"],
                                      "accounts": [accs["instagram"]["id"], "bf_ch_ig"], "now": True,
                                      "instagram_tags": "@streamer_one, @cohost"}, headers={"Origin": BASE})
    check("API /api/posts: queues with cleaned instagram_tags",
          (r_ok.status_code, r_ok.json().get("instagram_tags")), (200, ["streamer_one", "cohost"]))
    ig_queued = [p for p in posting._posts if p["job"] == job["id"] and p["clip"] == clip["id"]]
    check("Queue: instagram_tags saved on direct IG and Buffer IG posts",
          [p.get("instagram_tags") for p in ig_queued],
          [["streamer_one", "cohost"], ["streamer_one", "cohost"]])
    calls.clear(); buf["posts"].clear(); buf["state"] = {"status": "sending"}
    with ctx.as_user(user.id):
        for p in ig_queued:
            posting._post(p)
    ig_media_call = next(c for c in calls if c[1].endswith("/17841/media"))
    check("Posting worker: passes user_tags to Instagram Reel upload",
          json.loads(ig_media_call[2]["user_tags"]),
          [{"username": "streamer_one", "x": 0.5, "y": 0.5}, {"username": "cohost", "x": 0.5, "y": 0.5}])
    check("Posting worker: appends @mentions for Buffer Instagram channel",
          "@streamer_one @cohost" in buf["posts"][-1]["text"], True)
    posting._posts[:] = []
    buf["state"] = None
    jobs_mod._jobs.pop(job["id"], None)
    try:
        accounts.buffer_upload(bufacc, jd / "clip01.mp4", "My caption")
        check("Buffer: a clip with no job to fetch from is refused", False)
    except RuntimeError as e:
        check("Buffer: a clip with no job to fetch from is refused", "which clip" in str(e), True)

    accounts.buffer_upload(bufyt, jd / "clip01.mp4", "A description", "A title", "job-12345678", "unlisted")
    y = buf["posts"][-1]["metadata"]["youtube"]
    check("Buffer: YouTube gets a title, a category and the creator's privacy",
          (y["title"], y["categoryId"], y["privacy"], y["madeForKids"]), ("A title", "20", "unlisted", False))
    check("Buffer: a caption too long for X is cut to fit", len(accounts._buffer_text("word " * 200, "twitter")) <= 280, True)

    buf["reject"] = True
    try:
        accounts.buffer_upload(bufacc, jd / "clip01.mp4", "cap", "t", "job-12345678")
        check("Buffer: a refusal is passed on", False)
    except RuntimeError as e:
        check("Buffer: a refusal is passed on in Buffer's own words", "Queue limit" in str(e), True)
    buf["reject"] = False

    c = accounts._buffer_cred(); c.update(kind="oauth", expires=0, refresh="buf-refresh"); accounts._buffer_put(c)
    accounts._buf_token(accounts._buffer_cred())
    check("Buffer: an old sign-in is renewed, and the new refresh token kept", accounts._buffer_cred()["refresh"], "buf-refresh-2")

    ids = [x["id"] for x in accounts.list_accounts() if x["platform"] == "buffer"]
    for aid in ids[:-1]:
        accounts.remove(aid)
    check("Buffer: the sign-in stays while one channel is left", accounts.buffer_connected(), True)
    accounts.remove(ids[-1])
    check("Buffer: the last channel takes the sign-in with it", accounts.buffer_connected(), False)

print("ALL PASSED" if not fails else f"{len(fails)} failed")
import shutil; db.engine.dispose(); shutil.rmtree(os.environ["ANHY_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
