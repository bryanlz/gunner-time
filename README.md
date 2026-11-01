# Gunner Time 🔴⚪

A Telegram bot that messages you about every Arsenal men's first-team match, in all competitions:

1. **Lineup**: the starting XI as a numbered list plus the subs, sent as soon as the official lineup is published (usually 60–90 min before kickoff).
2. **Goal alerts** for either team, with the scorer, assist and new score, plus a **half-time update** with the score so far.
3. **Red card alerts** naming the player.
4. **Full-time report**: final score, every goal with its assist, red cards and xG, sent as one message once xG is available. If xG hasn't appeared within 30 minutes it's sent without it.

Times are shown in Singapore time (SGT). Data comes from FotMob's public match data. That source is free but unofficial, so if FotMob changes its site the bot may need a fix.

It runs **for free on GitHub Actions**: no server, no credit card.

---

## Setup (about 30 minutes, one time only)

### Step 1: Create the bot in Telegram
1. Open Telegram and search for **@BotFather** (it has a blue verified tick).
2. Send `/newbot`.
3. When it asks for a name, send `Gunner Time`.
4. When it asks for a username, send something ending in `bot`, e.g. `GunnerTimeAlertsBot`. It must be unique, so try another if it's taken.
5. BotFather replies with a **token** that looks like `8123456789:AAH...`. Copy it somewhere safe. **Anyone with this token can control your bot, so never share it or put it in the code.**
6. Tap the link BotFather gives you (`t.me/YourBotName`) to open your bot, press **Start**, and send it any message, e.g. `hi`.

### Step 2: Find your chat ID
Your chat ID tells the bot where to send messages. In the Terminal app, run these commands one at a time, replacing `PASTE_TOKEN_HERE` with your token:

```bash
cd ~/Downloads/Claude/gunner-time
```

```bash
TELEGRAM_BOT_TOKEN="PASTE_TOKEN_HERE" .venv/bin/python bot.py --get-chat-id
```

It prints something like `Chat id for Bryan: 123456789`. Save that number.

Then check that both work together. Your bot should message you:

```bash
TELEGRAM_BOT_TOKEN="PASTE_TOKEN_HERE" TELEGRAM_CHAT_ID="YOUR_CHAT_ID" .venv/bin/python bot.py --test-message
```

### Step 3: Put the code on GitHub
1. Create a free account at <https://github.com>.
2. Download **GitHub Desktop** from <https://desktop.github.com>, open it and sign in with your GitHub account.
3. Choose **File → Add Local Repository…** and pick the `gunner-time` folder (in Downloads → Claude).
4. It will say the folder isn't a repository yet. Click **create a repository**, keep the defaults, and click **Create Repository**.
5. Click **Publish repository** at the top. **Untick "Keep this code private"**, then click **Publish**.

**Why public?** Public repos get unlimited free GitHub Actions time. Private repos get 2,000 minutes a month, and matchdays would use that up. Making it public only shows the bot's code. Your token and chat ID go into encrypted Secrets in the next step, and nobody can see those.

### Step 4: Add your secrets
1. Open your repo on github.com (GitHub Desktop has a **View on GitHub** button).
2. Go to **Settings** (top tab) → **Secrets and variables** → **Actions** (left sidebar).
3. Click **New repository secret** and add these two, one at a time:

   | Name | Secret |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | your token from Step 1 |
   | `TELEGRAM_CHAT_ID` | your chat ID from Step 2 |

### Step 5: Check it's running
1. Click the **Actions** tab in your repo. If GitHub asks you to enable workflows, click the green button to enable them.
2. Click **Gunner Time** in the left sidebar → **Run workflow** → **Run workflow**.
3. After about 30 seconds, click the run, then **run**, then **Check for Arsenal updates**. You should see a line like:
   `Next: Arsenal v Hull at Sat 07 Nov 23:00 SGT, nothing to do yet`

That's it. From now on it runs by itself every 15 minutes.

---

## How it works
GitHub starts the bot every 15 minutes:

| When | What the bot does |
|---|---|
| More than 3 hours before kickoff | Checks the fixture list and stops after a few seconds |
| From 3 hours before kickoff | Stays running and checks the match every 60 s, ignoring FotMob's *predicted* lineup until the official one appears |
| During the match | Checks every 30 s for new goals and red cards, and spots goals that VAR overturns |
| After full time | Waits for xG (up to 30 min), then sends the full-time report |

Some notes on how it stays reliable:
- **Late starts:** GitHub's scheduled runs sometimes start 10–30 minutes late. Following each match from 3 hours before kickoff means a late start still catches the lineup, which comes out 60–90 minutes before.
- **Time limit:** each GitHub run is capped at 6 hours. The bot stops itself at 5½ hours and the next run carries on, so extra time and penalties are fine.
- **No duplicates:** after each run the bot saves `state.json` (a record of what it has already sent) back into the repo. That's why you'll see commits called "Update bot state".
- **Staying switched on:** GitHub pauses schedules on repos with no activity for 60 days. The bot updates its state file at least once a month to prevent this. If GitHub ever emails you that the workflow was disabled, go to Actions → Gunner Time → **Enable workflow**.

## Day to day
- **No action needed.**
- **Changing the code:** in GitHub Desktop, click **Fetch origin**, then **Pull origin** first, because the bot adds its own state commits. Then edit, write a short summary, click **Commit to main**, then **Push origin**.
- **Something wrong?** Look at the Actions tab. Each run has a log, and GitHub emails you if a run fails.
- **Preview what it would send for any match** (prints only, sends nothing). Find the match ID in the FotMob URL (`fotmob.com/match/6106238/...`):

  ```bash
  .venv/bin/python bot.py --preview 6106238
  ```
