# MISFIT autopost

Writes, designs and posts MISFIT carousels to Instagram on a schedule, with a
one-tap review in Telegram. Everything runs on free tiers. Claude is not
needed once this is set up.

```
GitHub Actions (on a timer)
   │  1. picks the next topic from content/topics.txt
   │  2. asks Gemini to write the carousel in the MISFIT style
   │  3. renders 1080×1350 (4:5) slides with the MISFIT template
   ▼
Telegram bot ──► you tap Approve / Redo / Skip (or reply with feedback)
   ▼
GitHub Actions at 6 pm IST ──► Instagram API publishes the carousel (and Reel)
```

**Default schedule:** one post every day at 6 pm IST (back-up 7 pm), for an
audience that is mostly in India. Examples use Indian brands and everyday life,
and prices are in rupees (`config.json → audience`).

**Telegram is set to "live only"** (`config.json → telegram → only_live`): it
only messages you when a post goes live. Drafts, reminders and problems are in
the MISFIT app. Set `only_live` to `false` to get everything in Telegram again
(the review flow below).

**Two days ahead** (`config.json → days_ahead: 2`): today's and tomorrow's posts
always have a draft, so you can approve a day early. Each morning the bot writes
the next day's post.

- **About 6:30 to 7 am IST:** the next day's draft arrives in the app (and in Telegram if `only_live` is off),
  labelled with its slot ("For 6:00 pm IST today").
- **At each slot:** approved drafts go live. If a draft isn't approved yet,
  or Instagram refuses it, it goes out at the back-up time an hour later.
  If that's missed too, it moves to the same slot the next day, and that
  morning only the empty slot gets a new draft. Nothing is lost or skipped.

Your taps and replies are picked up within about 10 minutes, and the bot
confirms each approval with the exact posting time. Each slot still
accepts approvals up to 15 minutes after it starts.

**Series:** MarketingTip, BrandBreakdown (real brands decoded), CopyClinic
(before-and-after rewrites), MythOrMagic (beliefs tested against research),
PsychInTheWild (everyday moments explained), and MarketersAssemble (mindset).
The mix follows the `weight` of each series in `config.json`, and the two
drafts of a day are always from different series when possible. Carousels are
as long as the idea needs: short when it's simple, longer only when every
slide adds something.

---

## One-time setup (about 1 hour)

You need five free accounts and keys. Keep a notes file open and paste each
key into it as you go. You'll add them all to GitHub in step 6.

### Step 1. Make the Instagram account a Professional account

In the Instagram app, open **Settings → Account type and tools → Switch to
professional account**. Choose **Creator** or **Business**. It's free and
you can switch back at any time.

### Step 2. Create the Telegram bot

1. In Telegram, open **@BotFather** and send `/newbot`.
2. Give it a name (for example *MISFIT Review*) and a username ending in `bot`.
3. BotFather replies with a **token** like `123456:ABC-xyz…`.
   Save it as **TELEGRAM_BOT_TOKEN**.
4. Open your new bot and press **Start**. This step matters: bots can't
   message you until you've messaged them first.
5. Get your chat ID from your own bot. Send it any message (for example
   *hi*), then open this link in a browser, with your token in place of
   `<TOKEN>`:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
   Find `"chat":{"id":` followed by a number, such as `"chat":{"id":123456789`.
   That number is your **TELEGRAM_CHAT_ID**. If the page shows `"result":[]`,
   send the bot another message and refresh the page.

### Step 3. Get a free AI key (Groq)

1. Go to **console.groq.com** and sign up (signing in with Google works).
2. Open **API Keys → Create API Key**, give it any name, and copy the key.
   It starts with `gsk_`.
3. Save it as **GEMINI_API_KEY**. The secret keeps that name, and the bot
   recognises a Groq key automatically. (A Gemini key from aistudio.google.com
   also works if Google allows your account to use it.)

### Step 4. Create the Meta app and get the Instagram token

1. Go to **developers.facebook.com**, log in, and register as a developer if asked.
2. Open **My Apps → Create app**. Choose the use case for managing messaging
   and content on Instagram, and pick **Business** if it asks for an app type.
3. In the app's left menu, open **Instagram → API setup with Instagram business login**.
4. Next to your Instagram account, click **Generate token**, log in to
   Instagram, and allow the permissions.
5. Copy the token and save it as **IG_ACCESS_TOKEN**. Tokens from the
   dashboard last 60 days, and step 6 lets the bot renew it automatically.

You can leave the app in *Development* mode, because it only posts to your own account.
If **Generate token** doesn't list your account, open **App roles → Roles**,
add your Instagram account as an **Instagram Tester**, and accept the invite
in Instagram (**Settings → Website permissions → Apps and websites → Tester invites**).

### Step 5. Put this folder on GitHub

1. **Rename the `github` folder to `.github`** (with a dot in front). GitHub
   only runs the timers from a folder named `.github`. In Finder, rename it
   and confirm **Use "."** when macOS asks. The folder then becomes hidden,
   which is normal. Press **Cmd+Shift+.** in Finder to see hidden files.
2. Create a free account at **github.com**.
3. Click **New repository**. Name it `misfit-autopost`, set it to **Public**,
   and don't add a README.
4. Upload this folder. The easiest way is **GitHub Desktop** (free):
   **File → Add local repository →** choose this folder, then **Publish
   repository**. Untick "Keep this code private".
5. On github.com, check that the repository shows a `.github/workflows`
   folder with 5 files.
   *If it doesn't:* open each file in this folder's `.github/workflows`, then on
   github.com click **Add file → Create new file**, type the name exactly
   (for example `.github/workflows/generate.yml`), paste the contents, and commit.

The repository needs to be **public** because Instagram downloads the slide
images from a public link. Your keys stay private, because GitHub Secrets are
encrypted and never shown to anyone.

### Step 6. Add the keys as GitHub Secrets

In your repository, open **Settings → Secrets and variables → Actions →
New repository secret** and add each one:

| Name | Value |
|---|---|
| `GEMINI_API_KEY` | your Groq key from step 3 |
| `TELEGRAM_BOT_TOKEN` | from step 2 |
| `TELEGRAM_CHAT_ID` | from step 2 |
| `IG_ACCESS_TOKEN` | from step 4 |
| `GH_PAT` | see below (renews the Instagram token every month) |

**To create GH_PAT:** open your GitHub profile menu → **Settings → Developer settings →
Personal access tokens → Fine-grained tokens → Generate new token**.
- Repository access: **Only select repositories →** `misfit-autopost`
- Permissions: **Secrets → Read and write**
- Expiration: choose the longest option offered, and set yourself a reminder
  to make a new one before it expires.

### Step 7. Edit the config and test

1. In `config.json`, set `instagram_handle` to your handle. Check
   `next_number` for each series (it's set to continue from MarketingTip#6
   and MarketersAssemble#1).
2. Open the **Actions** tab. If GitHub asks, click **I understand… enable workflows**.
3. Run **0. Setup check** (click it, then **Run workflow**). Your bot should
   message you to say Gemini, Instagram and Telegram are all OK.
4. Run **1. Write a new draft**. In a minute or two the slides arrive in Telegram.
5. Tap **Approve**, then run **5. Go ahead and post** for an instant test.

From now on it runs by itself.

---

## Posting right away

1. Tap **Approve** in Telegram.
2. On GitHub, open **Actions → 5. Go ahead and post → Run workflow**, and
   choose **next approved draft** or **all approved drafts**.

It reads your Telegram taps first, so there's no need to wait for a check,
and posts immediately. The bot sends the Instagram link when it's live, or
tells you why it couldn't post. The scheduled slots carry on as normal.

## Day-to-day use (all in Telegram)

| You do | What happens |
|---|---|
| Tap **Approve** | It's scheduled for that draft's slot (or its back-up, if you approve late) |
| Tap **Post now** | It posts within about 10 minutes, without waiting for the slot |
| Tap **Redo** | It writes a fresh take on the same topic, for the same slot |
| Tap **Skip** | It sets this version aside; the topic stays first in line and the slot is empty until you send /new or the next morning |
| **Reply** to a draft's message with text, e.g. *"shorter, punchier hook"* | It revises that draft using your feedback |
| `/new` | Fills an empty slot now with the next topic |
| `/new decoy pricing` | Fills an empty slot now with your idea |
| `/topic [CopyClinic] pricing page headlines` | Adds an idea to the topic list (the [Series] tag is optional) |
| `/next ...` | Puts an idea at the front of the topic list |
| `/topics` | Shows what's coming next |
| `/status` | Shows each draft, its slot and whether it's approved |
| `/drop` (as a reply to a draft) | Removes that draft's topic for good |
| `/rerender` | Redraws the slides with the latest template (reply to one draft, or send alone for all) |

Each draft includes a **fact-check list** of the studies and numbers it uses.
Check anything marked *medium* or *low* before approving, because the free
writer can't search the web.

---

## Changing things

- **Topics:** the bot keeps `content/topics.txt` up to date on GitHub, so add
  ideas from Telegram with /topic or /next. Lines are used top to bottom per
  series; untagged lines are MarketingTip. New topic batches can also be added
  as files in `content/topic_seeds/` (each file is merged in once). When a
  series runs out of topics, the writer chooses fresh ones itself.
- **Series mix:** change each series' `weight` in `config.json`. A weight of 0
  pauses a series.
- **When drafts arrive:** edit the `cron:` line in
  `.github/workflows/generate.yml`. Times are in UTC (IST minus 5:30), so
  `"57 0 * * *"` means every day at 6:27 am IST.
- **When posts go live:** change `"slots"` in `config.json` (each slot has a
  time and a back-up time, in IST), and change the `cron:` line in
  `.github/workflows/post.yml` so it runs about 30 minutes before each slot
  and back-up, in UTC. The current `"57 11,12 * * *"` covers 6 pm and 7 pm
  IST. Drafts still sitting at an old time move to the next free new slot by
  themselves, keeping their approval.
- **Reels:** `config.json → reels`. `make: true` builds a 9:16 Reel from every
  draft (the slides as swiping cards with music), shown on the post's page in
  the app. `post` decides what goes live: `off` (carousel only), `both`
  (carousel and Reel) or `reel` (the Reel instead of the carousel). Music: put
  tracks you have the right to use in `assets/music/` (.mp3, .m4a or .wav) and
  one is picked per post; otherwise an original lo-fi loop is generated.
  Instagram's in-app music library can't be added through the API.
  Switched on (`post: both`) from 20 Oct 2026. The carousel and the Reel are
  approved separately in the app (`approved_parts` in each draft); only the
  approved parts go live. Telegram's Approve button approves both. Drafts already approved before a
  Reel existed go out as a carousel only. `share_to_feed: false` (the setting
  from 20 Oct 2026) keeps Reels out of the profile grid and followers' feeds:
  they show in the Reels tab, where non-followers find them. Reels are capped at about 2 Mbit/s so they stay under
  jsDelivr's 20 MB limit.
- **Stats:** `config.json → stats`. Every 2 hours the check job reads views,
  reach, likes, comments, saves and shares for posts from the last 60 days,
  plus the follower count, and the app's History shows them. Views, reach,
  saves and shares need the `instagram_business_manage_insights` permission on
  the access token; without it only likes and comments come through.
- **Auto-post without review:** set `"auto_post_if_not_reviewed": true` in
  `config.json`. A draft you haven't reviewed by its slot then posts anyway.
- **Slide styles per series:** `config.json → design → styles` gives each series
  a look and a highlight colour: `poster` (huge capitals, emoji stickers),
  `editorial` (serif headings, yellow highlighter, handwritten notes), `essay`
  (capital headings on a red band, section labels, pull quotes) or `notebook`
  (ruled paper, pen underlines, handwritten notes). Use `classic` for the old
  plain look, or set `design.enabled` to `false` to switch all of it off.
  After the writer, a second AI call decides the highlights, headings, emoji
  and notes for each slide (saved as `design` in the draft). If that call
  fails, the slides still render with plain highlights. An edit to the text
  gets fresh art direction automatically. /rerender redraws waiting drafts.
- **Light or dark slides:** `"theme"` can be `"alternate"`, `"light"` or `"dark"`
  (only used by series on the `classic` look).
- **Follow slide at the end:** set `closing_slide.enabled` to `true`.
- **Writing style:** voice, spelling and rules are in `config.json → writing`.
  The example posts the writer copies are in `content/past_posts.md`.
- **Exact original font:** the slides use Kumbh Sans, a free look-alike of
  Canva's *Glacial Indifference*. For the exact font, download Glacial
  Indifference (free, OFL licence), then put `GlacialIndifference-Regular.otf` and
  `GlacialIndifference-Bold.otf` in `assets/fonts/`. They're used automatically.
- **Preview locally** (optional, needs Python):
  `pip install -r requirements.txt && python -m src.main preview content/sample_post.json`
  writes the slides to `posts/preview/`.

## Limits and troubleshooting

- **10 slides at most** per carousel (Instagram's limit). The template uses at
  most 9: a hook, up to 7 body slides, and the takeaway.
- **"Image not reachable" or an Instagram media-download error:** check that
  the repository is public. If it still fails, set `"image_host": "jsdelivr"`
  in `config.json`.
- **Token errors:** run **3. Renew Instagram token** from the Actions tab. If
  that fails, generate a new token (step 4) and update the `IG_ACCESS_TOKEN` secret.
- **The bot's commits fail:** go to **Settings → Actions → General →
  Workflow permissions** and choose **Read and write permissions**.
- **Writer errors:** the bot picks the best free Groq model automatically. You
  can pin one with `"groq_model"` in `config.json` (for example
  `"llama-3.3-70b-versatile"`). If anything fails, the bot sends the error to
  Telegram.
- **GitHub pauses timers on repositories with no activity for 60 days.** The
  bot's own commits keep it active, but if you pause posting for months, re-enable
  the workflows from the Actions tab.
- **Free plans can change.** Every piece here (GitHub Actions, Gemini free tier,
  Telegram, Instagram API) is free today. If one changes, only that one part
  needs swapping.

## What's in this folder

```
config.json            settings you can edit
content/topics.txt     the topic queue
content/past_posts.md  your past posts, used as the style reference
content/sample_post.json  an example post (also used for test runs)
assets/                MISFIT logos and fonts (OFL licensed)
src/                   the code: writer, slide renderer, Telegram, Instagram
state/                 what's pending and what's been posted (the bot updates this)
posts/                 slides waiting for review (deleted after posting)
github/workflows/      the timers (rename the folder to .github before uploading):
                       setup check, write drafts (6:27 am), check Telegram (every
                       10 min), renew token (monthly), post (slots),
                       go ahead and post (run by hand)
```
