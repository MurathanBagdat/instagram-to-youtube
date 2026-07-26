# Instagram Reels → YouTube Shorts auto-sync

Automatically re-posts every new Reel from Instagram **@chiathefrenchton** to the
**chiathefrenchton** YouTube channel as a Short, within minutes.

## How it works

- A GitHub Actions job runs every 5 minutes (free on a public repo).
- It asks the official Instagram API for your recent posts.
- Any reel it hasn't seen before is downloaded and uploaded to YouTube via the
  YouTube Data API, using your reel caption as the title/description
  (with `#Shorts` appended).
- Synced reel IDs are tracked in `state.json` so nothing uploads twice.
- The very first run only records your existing posts — old reels are **not**
  backfilled; only reels posted after setup get synced.
- A second weekly job auto-refreshes the Instagram token so it never expires.

## One-time setup

### 1. Instagram (Meta) side

1. Your Instagram account must be a **Professional account** (Creator or
   Business). Instagram app → Settings → Account type and tools → Switch to
   professional account. (Free, doesn't change how your profile looks much.)
2. Go to <https://developers.facebook.com/> → **My Apps → Create App**.
   - Use case: **Other** → App type: **Business** (or pick "Instagram" if offered).
3. In the app dashboard, add the **Instagram** product → choose
   **"API setup with Instagram login"**.
4. Under **Generate access tokens**, add your Instagram account and click
   **Generate token**. Log in with your Instagram account and copy the token —
   this is a **long-lived token (60 days)**; the weekly workflow keeps it fresh.
5. Save it as the `IG_ACCESS_TOKEN` secret (step 4 below).

### 2. YouTube (Google) side

1. Go to <https://console.cloud.google.com/> → create a project
   (e.g. `chia-shorts-sync`).
2. **APIs & Services → Library** → enable **YouTube Data API v3**.
3. **APIs & Services → OAuth consent screen**:
   - External, fill in the app name and your email.
   - Add scope `https://www.googleapis.com/auth/youtube.upload` (optional here).
   - **Publish the app** (Publishing status: In production). This matters —
     apps left in "Testing" mode get refresh tokens that die after 7 days.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID** →
   Application type: **Desktop app**. Note the Client ID and Client Secret.
5. On your computer, run:

   ```bash
   python3 get_youtube_refresh_token.py
   ```

   Sign in with the Google account that owns the YouTube channel (click
   "Advanced → Go to app (unsafe)" on the unverified-app warning — it's your
   own app). It prints your `YT_REFRESH_TOKEN`.

### 3. Lift YouTube's private-video restriction (important)

YouTube sets videos uploaded by **unaudited** API projects to *private*
automatically. Until your project is audited, synced Shorts appear on your
channel as private and you publish them with one tap in the YouTube Studio app.

To make it fully automatic, submit the free audit form once:
<https://support.google.com/youtube/contact/yt_api_form> — say you're a creator
auto-posting your own Instagram content to your own channel. Approval usually
takes a few days.

### 4. GitHub secrets

In this repo run (each command prompts you to paste the value):

```bash
gh secret set IG_ACCESS_TOKEN
gh secret set YT_CLIENT_ID
gh secret set YT_CLIENT_SECRET
gh secret set YT_REFRESH_TOKEN
```

For automatic Instagram token refresh, also create a fine-grained personal
access token at <https://github.com/settings/personal-access-tokens/new>
(access to only this repo, permission **Secrets: Read and write**) and:

```bash
gh secret set GH_PAT
```

### 5. Test it

Actions tab → **Sync Instagram Reels to YouTube Shorts** → **Run workflow**.
The first run initializes `state.json`. Then post a reel and watch it appear
on YouTube within ~5–15 minutes.

## Manually migrating an old reel

Old reels are not backfilled automatically, but you can migrate any single
reel by URL:

```bash
gh workflow run migrate.yml -f reel_url="https://www.instagram.com/reel/XXXX/"
```

or on GitHub: **Actions → Migrate a reel → Run workflow**, paste the URL.
Add `-f force=true` to re-upload a reel that was already synced.

## Email notifications

Two kinds of email go to the address in `notify.py` (default:
murathanbagdat@hotmail.com):

- a **success email** right after any reel is uploaded (automatic or manual);
- a **daily report at 17:00 Türkiye time** with the last 24 h: how many reels
  were uploaded and any failed workflow runs.

Sending uses Gmail SMTP. One-time setup: on a Google account with
2-Step Verification enabled, create an **app password** at
<https://myaccount.google.com/apppasswords>, then:

```bash
gh secret set GMAIL_ADDRESS        # the Gmail address doing the sending
gh secret set GMAIL_APP_PASSWORD   # the 16-character app password
```

Without these secrets everything else still works — emails are simply skipped.

## Notes

- **Timing:** GitHub schedules can lag a few minutes at busy times; expect
  5–15 minutes end-to-end, occasionally more.
- **Music/copyright:** Reels using Instagram's licensed music library may get
  copyright claims or muted audio on YouTube. Original audio/voiceover is safe.
- **Quota:** YouTube's free API quota allows ~6 uploads/day — plenty for daily
  reels.
- **Config:** Set repo *variables* `PRIVACY_STATUS` (public/unlisted/private)
  or `MAX_PER_RUN` if you ever want to change behavior.
- Secrets stored in GitHub Actions secrets are encrypted and are **not**
  visible even though the repo is public.
