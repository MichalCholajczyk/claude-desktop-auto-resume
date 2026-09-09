# Claude Desktop App Auto-Resume for Windows 11

**Don't let the 5-hour limit stop Claude in the middle of an overnight run.**

When your session limit runs out in the **Claude Desktop** app (Windows), work
just stops until you manually type "continue". This tool watches the Claude
window for you: it detects when the limit is hit, remembers the reset time, and
**one minute after the reset it types "continue" and presses Enter by itself**.
You can leave it running while you are away.

It can also watch **several conversations and split views**, retry **API and
server errors**, and optionally answer Claude's **approach questions** for you.

It's the Windows-desktop counterpart to the popular Linux `claude-auto-retry`
(which only works in the terminal on Linux). This one drives the **Claude
desktop app on Windows 10/11**.

The interface is bilingual — **English / Polish**, switchable with the EN/PL
toggle in the top-right corner (English by default).

![Screenshot of the app](docs/screenshot.png)

---

## How it works — in short

1. **Watches your chosen conversations** every ~20 seconds. It can check all
   open conversation panes, including split views, or visit only the chats you
   select from the sidebar. It reads the interface like a screen reader — no
   screenshots, no OCR.
2. **Looks for the "Usage limit reached" notice first.** That red strip next to
   the chat box is Claude's own "session blocked" signal, so it's the primary
   trigger — the usage meter can lag or stick below 100% while the session is
   already blocked. The reset time is read straight from the notice
   (e.g. *"Resets at 2:40 PM"*).
3. **Falls back to the 5-hour limit meter.** When no notice is visible but the
   usage meter maxes out, it briefly opens Claude's usage panel (the circular
   meter in the bottom bar) and reads the **5-hour limit** row — ignoring the
   weekly and per-model limits, so a maxed-out weekly (e.g. Fable) limit never
   triggers a false alarm. The reset time comes from the same panel
   (e.g. *"Resets in 45 min"*).
4. **One minute after the reset** it brings the interrupted conversation to the
   front and sends `continue` (or your custom message). You can also choose to
   use **Try again** when that button is available. Each conversation has its
   own timer, so one waiting chat does not hold up the others.

While it waits for the reset, it also **keeps the PC from going to sleep**, so
the overnight send actually happens.

---

## Installation

### What you need

- **Windows 10 or 11**
- **Python 3.10+** — check in a terminal: `py --version`.
  (You can get it from [python.org](https://www.python.org/downloads/) and
  tick *"Add Python to PATH"* during install.)
- The **Claude Desktop** app, installed and signed in.

### Get it and run

```powershell
git clone https://github.com/MichalCholajczyk/claude-desktop-auto-resume.git
cd claude-desktop-auto-resume
```

Then just double-click **`run.bat`** — it installs the one dependency it needs
and starts the app.

Prefer to do it by hand?

```powershell
py -m pip install --user uiautomation
py claude_auto_continue.py
```

---

## First run

1. Open **Claude Desktop** and the conversations you want to continue.
2. Start this tool (`run.bat`). You'll see a dark panel with a big clock.
3. Check the **"Claude window"** field at the top. Click **"Refresh"** to reload
   both the window list and the conversation list.
4. On the **Conversations** tab, choose **All open conversation panes** to watch
   your open chats and split views. Or click the rows you want to watch — this
   switches to **Only checked conversations**.
5. Set your message on the **Settings** tab and choose any optional features below.
6. Click **"Start watching"** and leave it running in the background.

A green lamp means "watching". When a limit is detected, the clock counts down
to the next attempt. The conversation list shows each chat's status separately.

To resume immediately, click **"Resume now…"** and confirm. This applies to all
conversations in your chosen scope, so check the list first.

> **Couldn't read the reset time?** Type it into the **"Know the reset time?"**
> field (format `HH:MM`) and click **"Arm"** — the tool will resume the chosen
> conversations one minute after that time. Timers are not saved when you close
> the tool; start watching again after restarting it.

### Custom message

By default the tool sends **`continue`**, but you can send anything you like.
Type your own text into the **"Message to send"** box on the **Settings** tab.
It is used after a reset and by **"Resume now…"**. Leave it blank to fall back
to `continue`.

The box takes long prompts: it wraps and scrolls, and you can drag the handle
underneath it to make it taller (the height is remembered between runs). What
you type is sent as **one message**, so line breaks are collapsed into spaces —
Enter submits in Claude's composer, so a line break inside the message would
send it half-written.

### Multiple conversations and split views

**All open conversation panes** watches the chats currently open in the selected
Claude window, including Code split views. Browser and terminal panes are ignored.

For a specific list, click **Read chats**, then check the conversations you want.
You can add chats from both **Code** and **Chat and Cowork**: switch modes in
Claude and click **Read chats** again. Your selections are saved between runs.

The list includes chats currently loaded in Claude's sidebar, including pinned
chats when visible. To add an older chat, open it or expand its sidebar section,
then click **Read chats**. After starting a new conversation, **Refresh** also
updates the list.

One copy of Auto-Resume watches one Claude window and its splits. For separate
Claude windows, run a copy for each window.

### Use the Try again button

Enable **Prefer “Try again” after a limit reset** to retry with Claude's button.
If the button is missing, the tool sends your custom message instead. Leave this
option off to keep using your message after a reset.

### Retry API and server errors

**Retry API and server errors** is on by default. When Claude stops with a
server error, the tool clicks **Try again** if available, or sends your message.
The first retry is after 30 seconds; later attempts wait longer, up to 15 minutes.

After six unsuccessful attempts, the chat shows **Needs attention**. Fix the
problem, then use **Resume now…** or stop and start watching to try again.
Sign-in, access and invalid-request errors need your attention directly.

### Answer approach questions automatically

This option is **off by default**. Turn on **Answer approach questions
automatically** to let the tool select answers marked **Recommended** (including
Polish labels such as **rekomendacja**) and submit them.

If no answer is marked as recommended, it enters this in **Other** and submits:

> Pick your recommended option(s).

It leaves answers you have already started or selected alone, and does not
approve permission prompts. Turning off **Send automatically** also turns off
automatic retries and approach answers.

---

## ⚠️ Important before leaving it overnight

- **Turn off automatic screen lock.** On a locked desktop, Windows won't let the
  tool simulate the keyboard, so the send will fail.
  (Settings → Accounts → Sign-in options → *"If you've been away, when should
  Windows require you to sign in again?" → Never*, and disable any
  password-protected screensaver.)
- **At send time the tool briefly takes over the keyboard** — it physically
  clicks and types into the Claude window. At night that's irrelevant; during
  the day, just don't be typing at that exact second.
- **Leave the Claude window open and not minimized.** To read the 5-hour limit
  the tool briefly opens Claude's usage panel by clicking the meter, then closes
  it and puts your mouse cursor back — this needs the window visible.
- **Check which conversations you are watching.** Open-pane mode follows the
  panes currently open in Claude. Checked-conversation mode visits your selected
  chats. Busy chats and chats with an unsent draft are skipped.

Safety: just before sending, the tool checks that the Claude window is really in
front. If it can't bring it forward, it **won't type blindly** (it won't paste
your message into another app).

---

## Settings

Available in the app window; saved to `auto_continue_config.json`:

| Option | Default | What it does |
|---|---|---|
| Language (EN/PL toggle) | EN | interface language |
| Scan every (s) | 20 | how often the tool checks the Claude window |
| Conversations to watch | All open conversation panes | watch open panes or only checked chats |
| Message to send | `continue` | the text sent when the limit resets |
| Prefer “Try again” after a limit reset | Off | use the retry button when available instead of your message |
| Retry API and server errors | On | retry interrupted chats after temporary errors |
| Answer approach questions automatically | Off | submit recommended answers, or ask Claude to choose via Other |
| Send automatically | ✔ | turn off automatic messages, retries and approach answers |
| Keep the PC awake | ✔ | blocks system and display sleep while watching |

More advanced fields — number of retries, retry spacing, the 5-hour "hit"
threshold (`limit_threshold_pct`, default 100), and how often the usage panel
may be opened (`panel_backoff_s`) — can be edited directly in
`auto_continue_config.json`.

---

## Troubleshooting

**It can't see the Claude window.** Make sure Claude Desktop is actually running
(not just in the tray) and click "Refresh".

**A new or older conversation is missing.** Click **Refresh** or **Read chats**.
The tool reads the sidebar entries Claude has loaded. Open the conversation or
expand the relevant sidebar section, then refresh again. If Claude restarted,
select its window again.

**A checked conversation is being skipped.** Chats need unique full titles
within their Claude mode. If a chat was renamed, check its new entry. Also check
for an unsent draft or a reply still in progress.

**It keeps saying "couldn't read the usage panel".** The tool needs to click
the usage meter in Claude's bottom bar, so the Claude window must be visible
(not minimized) and reasonably sized. Bring the window up and it will recover on
the next scan.

**It's not reacting to a maxed weekly/Fable limit.** That's intentional — this
tool reads the **5-hour limit** separately. A weekly or per-model meter at 100%
on its own does not schedule a continuation.

**Live view in the Log.** The "Log" panel (and the `auto_continue.log` file)
show exactly what the tool sees and does — that's the first place to look when
something isn't right.

---

## Privacy & safety

The tool runs **locally on your machine**. It reads Claude's interface through
Windows accessibility tools and clicks or types on your behalf. No API key or
separate online service is needed. Messages sent through Claude are processed
by Claude as usual.

It takes no screenshots and does not save full conversations. The local log
contains chat titles and action details; the settings file stores your selected
chat titles and custom message.

---

## How it works under the hood (for the curious)

- The window (the Claude app is Electron/Chromium) is read through **Windows UI
  Automation** (the `uiautomation` package). Chromium builds its accessibility
  tree only on demand, so the tool first "wakes" it by querying the documents'
  `TextPattern`.
- **The 5-hour limit is read from Claude's own usage panel.** The bottom-bar
  meter only shows the *highest* of all your limits, so a maxed weekly Fable
  limit makes it read "100%" even when the 5-hour limit is fine. To disambiguate,
  the tool clicks the meter to open the **Usage** popover and reads the specific
  "5-hour limit" row (percentage + reset), then closes it with Escape and
  restores the cursor. It only opens the popover when the cheap bottom-bar meter
  is maxed, and backs off afterwards, to avoid flashing it constantly.
- Before acting, the tool checks the conversation and its own message box or
  retry button. It skips missing or ambiguous chats.
- Each conversation is tracked separately: watch for an interruption, wait for
  its reset or retry time, resume, then check the result.

---

## License

MIT — do whatever you want with it.
