# Putting Anhy Clips on a server

Anhy Clips runs as three containers on one Linux server: the app, PostgreSQL, and Caddy (which handles HTTPS).

## What you need

- A Linux server with Docker. Clipping is CPU work: plan on **8+ CPU cores, 16 GB RAM and 200+ GB of disk** if
  several people will use it. Transcription runs at roughly 10x real time per video on 4 cores.
- A domain name pointing at the server (an `A` record). No public address, or no ports to open? See
  [Cloudflare Tunnel](#cloudflare-tunnel-no-ports-no-certificate) instead: it needs no `A` record and no open port.
- An AI key (see "Choose your AI" in the README). Optional: a Google "Web application" OAuth client, and an SMTP
  service for emails.

## Steps

1. Copy this folder to the server (or `git clone` it there).
2. `cp .env.example .env` and fill it in: the AI lines, `ANHY_DOMAIN`, `POSTGRES_PASSWORD`, and
   `COMPOSE_PROFILES=own-https` if the server is Anhy Clips' alone. Put key files, if you use files, in `./secrets/`.
3. `docker compose up -d --build`
   The first start downloads the speech model (about 480 MB) and creates the database tables.
4. Open `https://your-domain` and sign up. With Docker, accounts are on (`ANHY_ACCOUNTS=1`), so everyone signs in.
   Every new account is a normal user.
5. Make yourself an admin, on the server: `docker compose exec app python -m anhy.admin grant you@example.com`
   The admin dashboard is then at `https://your-domain/admin`. It answers "not found" for everyone who is not an
   admin, and no page or web request can make someone an admin: only that command can.
6. Sign in as that admin, open **Settings, AI**, and add your AI key there (or put it in `.env`).

## Connecting YouTube, Instagram, TikTok and Buffer

People connect their accounts with one click in **Settings, Connected apps**: they press Connect, sign in on the
platform's own page, and come back. For that, the server needs one app registered with each platform. An admin can
do it right there: press **Set up** on a platform, follow the steps shown, and paste the app's id and secret (or put
them in `.env`, see `.env.example`). The redirect address to register is shown in the panel, with a Copy button.
Buffer is the exception: it needs nothing registered here (see below).

- **YouTube** (Google Cloud): an OAuth client of type Web application, with YouTube Data API v3 turned on. Until
  Google verifies the app, people see a warning screen and uploads stay private.
- **Instagram** (Meta for Developers): a Business app with Instagram (API setup with Instagram login). Works with
  Creator and Business accounts. Until Meta reviews the app, only accounts added as testers can connect. Instagram
  fetches each clip from a one-hour signed link on your site, so the site needs a public https address.
- **TikTok** (TikTok for Developers): an app with Login Kit and the Content Posting API (Direct Post), scopes
  `user.info.basic` and `video.publish`. Until TikTok audits the app, clips are posted as private (Anhy Clips does
  this by itself and says so on the account).
- **Buffer** ([publish.buffer.com/settings/api](https://publish.buffer.com/settings/api)): nothing to register.
  Each person presses **Use an API key** and pastes the key Buffer gives them on that page, which works on every
  Buffer plan and brings every channel they have connected there: Instagram, TikTok, YouTube, X, LinkedIn,
  Facebook, Threads, Pinterest, Bluesky, Mastodon and more. Registering a Buffer app (Client ID and secret,
  redirect URI as shown in the panel) is optional, and only adds the one-click **Sign in with Buffer**.

Instagram and TikTok only accept https redirect addresses.

### Posting through Buffer

Two things are worth knowing about the Buffer route:

- **The site needs a public https address.** Buffer never takes an upload: it comes and fetches the clip from
  `/pub/…` on your site, so `ANHY_PUBLIC_URL` has to be a domain Buffer can reach over https. On a computer
  with no public address, Buffer posts fail with a message saying so.
- **Clips go out at once, not into Buffer's queue.** Anhy Clips' own queue has already decided when each clip is
  due (spacing, daily caps, a creator's minimum score), and the clip's link is only good for a few hours. So the
  post is sent to Buffer as "share now". Buffer's own queue and posting schedule for that channel are not used,
  and Buffer's weekly posting limits still apply: if a channel is at its limit, Buffer refuses the post and it is
  retried three times before it is marked failed.
- **"Posted" means the network has it.** Buffer takes a post first and publishes it afterwards, and it answers
  "taken" even when it later refuses the post. So a Buffer post stays **Sent to Buffer** in the queue until Buffer
  says it is sent, and Anhy Clips asks Buffer again every 20 minutes (and as soon as you press **Check**) until it
  is sent or Buffer gives the reason. Nothing is called posted on Buffer's promise alone.
- **Instagram needs to be told what it is.** Buffer refuses an Instagram post that does not say it is a Reel and
  whether it also goes to the feed; Anhy Clips sends both. Facebook Reels get their type too, and YouTube gets a
  title and category. If a post still fails, the message says which of these it was.
- **When it is not posting, ask it.** Settings, Connected apps has **Why isn't it posting?** next to Buffer. It
  goes through every step a clip takes: the sign-in, the channels (and whether Buffer has disconnected or locked
  one), the public https address Buffer fetches from, the Reels setting, and whether Buffer takes a post at all. It
  posts nothing to the networks (its one test post goes to X, when there is one, and is removed again).
- **View counts stay blank.** Buffer only reports a post's numbers to a personal API key, and not straight away,
  so posts made through Buffer show no views in the overview. The same clip posted through a direct YouTube or
  Instagram connection does.

## Google setup (sign-in and YouTube)

In Google Cloud, create an OAuth client of type **Web application** with these redirect URIs:

- `https://your-domain/auth/google/callback`
- `https://your-domain/oauth/youtube/callback`

Enable **YouTube Data API v3** in the same project. Posting publicly to YouTube needs Google's API audit;
until then uploads from the API stay private.

## Email

Set the `ANHY_SMTP_*` lines and `ANHY_MAIL_FROM` to send password-reset and confirmation emails through any
SMTP service. On a public site also set `ANHY_VERIFY_EMAIL=1`, so people confirm their address before clipping.
Without SMTP on an HTTPS site, no reset emails can be sent.

## Good to know

- **Data**: everything lives in two Docker volumes, `pgdata` (accounts, AI usage) and `data` (videos, clips).
  Back both up. `docker compose exec db pg_dump -U anhy anhy > backup.sql` dumps the database. (A server set up
  before the app was called Anhy Clips kept its old database: there the names are `peakcut`, so `-U peakcut peakcut`.)
- **Capacity**: `ANHY_JOB_WORKERS` is how many videos are processed at once; `ANHY_MAX_LIVE` is how many live
  streams are recorded at once. Raise them only as far as the CPU allows.
- **One server only**: jobs and live recordings run inside the app process. Do not start a second copy of the app
  against the same data.
- **YouTube downloads from data centres** are sometimes refused by YouTube ("confirm you're not a bot"). If links
  fail on the server but work at home, the server's address is the cause. Anhy Clips then pauses YouTube links for a
  few minutes and says so; uploads and Twitch or Kick links keep working.
- **Updates**: `git pull && docker compose up -d --build`. A restart pauses videos in progress (they pick up
  where they stopped) and live recordings (clips already found are finished from the saved stream).
- **Open sign-up**: anyone who can reach the site can make an account and use your AI key. On a public address,
  keep the limits in `.env` sensible, or put the site behind your own access control.

## A server that already runs other sites

Leave `COMPOSE_PROFILES=own-https` out of `.env`. Anhy Clips then listens only on `127.0.0.1:8765` and the web
server that is already there forwards the domain to it: see `deploy/nginx-anhy.conf`, or
`deploy/docker-compose.traefik.yml` for Traefik.

## Cloudflare Tunnel (no ports, no certificate)

A tunnel is a way to put Anhy Clips on the internet without opening any port on the server and without Caddy: a
small program, `cloudflared`, sits on the server and dials out to Cloudflare; visitors reach Cloudflare and
Cloudflare passes the request down that connection. It also suits a computer that has no public address at all
(home broadband with no port forwarding, a server behind someone else's firewall, a laptop kept awake).

What it cannot fix: a tunnel is not faster, uploads still pass through Cloudflare's 100 MB request limit (Anhy
Clips already sends big files in pieces), and the free plan is meant for the traffic of a small site.

1. **The domain has to be on Cloudflare.** If its nameservers are still at your registrar, add the domain in
   Cloudflare and change the nameservers there first, then wait for Cloudflare to say the zone is active.
2. **Make the tunnel.** In the Cloudflare dashboard open **Zero Trust, Networks, Tunnels, Create a tunnel**, pick
   **Cloudflared**, name it `anhy-clips`, save, and copy **the token** it shows you (the long line after `--token`).
   Treat the token like a password: whoever has it can send traffic through your tunnel.
3. **Give it the address to answer on.** On the tunnel's page, **Public hostname, Add a public hostname**:
   - Subdomain: `clips` (or whatever you want) and Domain: your domain. That address is what people will open, so
     it is the one to write down as `ANHY_DOMAIN` from here on.
   - Service: **HTTP**, and URL: `127.0.0.1:8765`.

   With Docker (the next step) `127.0.0.1` is the server itself, because the tunnel container runs on the server's
   own network rather than Docker's. If you run `cloudflared` as a program on the server instead of in Docker,
   it is the same address.
4. **Tell Anhy Clips, and start both.** In `.env` on the server:

   ```
   ANHY_DOMAIN=clips.example.com
   POSTGRES_PASSWORD=a-long-string-you-make-up
   TUNNEL_TOKEN=eyJhIjoi...            # the token from step 2
   ANHY_PUBLIC_URL=https://clips.example.com
   ANHY_CLIENT_IP_HEADER=CF-Connecting-IP
   COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.cloudflared.yml
   ```

   Leave `COMPOSE_PROFILES=own-https` out: Caddy has nothing to do here, and ports 80 and 443 stay closed. Then
   `docker compose up -d --build`. The tunnel's page in Cloudflare turns **Healthy** within a minute or two.
   (`COMPOSE_FILE` is what brings in `deploy/cloudflared.yml`, which is the tunnel as a fourth container.)
   Accounts are on with Docker, so the rest of "Steps" above applies: sign up, make yourself an admin, add your
   AI key.
5. **Check it.** `https://clips.example.com/healthz` answers `{"ok":true}`, and the dashboard opens. Nothing in
   **SSL/TLS** needs touching: the tunnel itself is encrypted, there is no origin certificate, and nothing but
   Cloudflare can reach Anhy Clips.

### Without Docker

`cloudflared` can be installed on the server by itself: `deploy/cloudflared-config.yml` is the tunnel's settings
file for that: the tunnel id, the hostname and the same `127.0.0.1:8765` sit in it, with the commands that make
the tunnel and install it as a service written at the top. Run Anhy Clips as usual (`python -m anhy.server`, or
`docker compose up -d db app` with only the tunnel installed on the server) and set the same `.env` lines:
`ANHY_DOMAIN`, `ANHY_PUBLIC_URL`, `ANHY_CLIENT_IP_HEADER`, and **not** `COMPOSE_FILE`, so no tunnel container is
started.

### Trying it quickly, before deciding

Cloudflare's **quick tunnel** needs no account, no domain and no token: run `cloudflared tunnel --url http://127.0.0.1:8765`
(`docker run --rm --network host cloudflare/cloudflared tunnel --url http://127.0.0.1:8765` if Docker is what you
have) and it prints a random `https://something.trycloudflare.com` address that works in seconds. Two things to
know: the address changes every time it starts, and it does not always pass the address in `--url` through, so the
page may answer `Open Anhy Clips by its own address` (that is the check in "If something does not work" below).
Add `--http-host-header` with the random address to make it match, and copy that address into `ANHY_PUBLIC_URL`
before starting Anhy Clips. Either way a quick tunnel is for a look, not for posting: sign-in,
the connected apps and Buffer want a stable https address.

### If something does not work

- **`Open Anhy Clips by its own address`**: with accounts off (`ANHY_ACCOUNTS=0`) Anhy Clips only answers to the
  address it knows as its own, and a tunnel can send a different one. Set `ANHY_PUBLIC_URL` to the tunnel's
  address; with a settings file also set `httpHostHeader` as shown. With Docker, accounts are on by default and it
  does not come up.
- **The tunnel says Healthy but the page is an error**: the Service in the public hostname must be the address
  Anhy Clips really listens on, `127.0.0.1:8765` (or the port in `ANHY_BIND_PORT`) — not `app:8765`, which only
  works between Docker containers, and the tunnel container in `deploy/cloudflared.yml` is not on Docker's network.
  (To put it there instead, drop `network_mode: host` and use `app:8765` as the Service.)
- **A sign-in page that never finishes, or a save refused** — the address must be the same everywhere: the public
  hostname, `ANHY_PUBLIC_URL`, and the redirect addresses registered with Google, Instagram, TikTok and Buffer.
  A wrong one is the usual cause. They are listed in "Google setup" above and in `.env.example`.
- **Uploads stop at about 100 MB**: that is Cloudflare's own request limit, not Anhy Clips. `ANHY_UPLOAD_PART_MB`
  (40 by default) keeps single requests under it; lower it further if a proxy in front is stricter still.
- **Everything comes from one address in the rate limits**: that is Cloudflare. `ANHY_CLIENT_IP_HEADER=CF-Connecting-IP`
  makes Anhy Clips read each visitor from Cloudflare's own header instead, and the header is believed only when the
  request really came from Cloudflare.

## Cloudflare CDN in front

A different arrangement from the tunnel above: the domain points at the server (`A` record, orange cloud) and the
server serves it, with Caddy or nginx doing HTTPS. The tunnel above needs none of this.

With the record proxied (orange cloud):

- Set `ANHY_CLIENT_IP_HEADER=CF-Connecting-IP` in `.env`, so rate limits see each visitor, not Cloudflare. The
  header is only believed for connections that really come from Cloudflare's address ranges.
- Images, videos and scripts under `/brand/` and `/assets/` are cached by Cloudflare automatically. Signed-in
  pages, clips and the API send `private` or `no-store` and are never cached by Cloudflare.
- Use SSL/TLS mode Full (strict) with a Cloudflare origin certificate in `secrets/` and
  `ANHY_CADDYFILE=Caddyfile.origin`.
- Cloudflare's standard plans refuse requests over 100 MB. Anhy Clips sends big uploads in pieces
  (`ANHY_UPLOAD_PART_MB`, 40 by default), so large files still work.

## Speech to text on a graphics card

Optional. A second machine with an NVIDIA card can do most of the transcription much faster:
`deploy/gpu/docker-compose.yml` runs it, and `ANHY_ASR_URL` plus `ANHY_ASR_TOKEN` on the main server point
at it. Keep its port closed to the internet. If it is unreachable, the main server carries on with its own
processor.
