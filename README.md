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
GitHub Actions at 6 pm IST (8 pm back-up) ──► Instagram API publishes the carousel
```

**Default schedule:** one post every day.
- **Around 8 am IST:** the day's draft arrives in Telegram for review.
- **6:00 pm IST:** the approved draft goes live on Instagram.
- **8:00 pm IST:** back-up slot. If the draft wasn't approved by 6 pm, or
  posting failed, it goes live at 8 pm instead. If 8 pm is missed too, it
  moves to 6 pm the next day (then 8 pm), and so on until it's live.

Your taps and replies are picked up within 30 minutes. Approve any time
before 6 pm, or before 8 pm for the back-up slot (each slot still accepts
approvals up to 15 minutes after it starts).

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
5. Tap **Approve**. The bot confirms it's scheduled for 6 pm. For this first
   test, tap **Post now** instead: within 30 minutes the carousel appears on
   Instagram and the bot sends you the link.

From now on it runs by itself.

---

## Posting right away

1. Tap **Approve** in Telegram.
2. On GitHub, open **Actions → 5. Go ahead and post → Run workflow**.

It reads your Telegram taps first, so there's no need to wait for a check,
and posts the approved draft immediately. The bot sends the Instagram link
when it's live, or tells you why it couldn't post.

## Day-to-day use (all in Telegram)

| You do | What happens |
|---|---|
| Tap **Approve** | It's scheduled for the next slot: 6 pm, else 8 pm, else 6 pm tomorrow |
| Tap **Post now** | It posts within 30 minutes, without waiting for the slot |
| Tap **Redo** | It writes a fresh take on the same topic |
| Tap **Skip** | It drops the draft, and the next one comes the next morning |
| Reply with text, e.g. *"shorter slides, punchier hook"* | It revises the draft using your feedback |
| `/new` | Makes a draft now from the next topic |
| `/new decoy pricing` | Makes a draft now on your idea |
| `/topic loss aversion in UPI cashback` | Adds the idea to the topic list |
| `/topics` | Shows what's coming next |
| `/status` | Shows what's waiting |
| `/rerender` | Redraws the current draft's slides with the latest template, keeping the same text |

If 8 pm comes and the draft still isn't approved, nothing goes live that day
and the bot tells you. Approving it after that schedules it for 6 pm the next
day, and no new draft is written that morning. If Instagram ever refuses a
post, the bot tells you why and tries again at the next slot automatically.

Each draft includes a **fact-check list** of the studies and numbers it uses.
Check anything marked *medium* or *low* before approving, because the free
writer can't search the web.

---

## Changing things

- **Topics:** edit `content/topics.txt`. Lines are used top to bottom. Start
  a line with `[MarketersAssemble]` to make it a mindset post. When the list
  is empty, the writer chooses topics itself and avoids anything already posted.
- **When the draft arrives:** edit the `cron:` line in
  `.github/workflows/generate.yml`. Times are in UTC (IST minus 5:30), so
  `"15 2 * * *"` means every day at 7:45 am IST. GitHub often starts timers
  5 to 15 minutes late, which is why it's set a little before 8.
- **When posts go live:** change `"post_times_ist"` in `config.json`
  (for example `["18:30", "21:00"]`), and also change the `cron:` line in
  `.github/workflows/post.yml` so it runs about 30 minutes before each time,
  in UTC (IST minus 5:30). The current `"57 11,13 * * *"` means 11:57 and
  13:57 UTC, which is 5:27 pm and 7:27 pm IST. It starts early because GitHub
  often runs timers late, then waits for the exact time.
- **Skipping days:** in both cron lines, replace the last `*` with the days to
  run. For example, `1-6` means Monday to Saturday.
- **Auto-post without review:** set `"auto_post_if_not_reviewed": true` in
  `config.json`. A draft you haven't reviewed by 6 pm then posts anyway.
- **Light or dark slides:** `"theme"` can be `"alternate"`, `"light"` or `"dark"`.
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
                       setup check, write draft (8 am), check Telegram (every
                       30 min), renew token (monthly), post (6 pm, 8 pm),
                       go ahead and post (run by hand)
```
