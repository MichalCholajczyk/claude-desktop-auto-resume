# Claude Desktop Auto-Resume for Windows

Resume interrupted Claude Desktop conversations after a usage reset or a server
error. Works locally through Windows UI Automation, with an English / Polish UI.

## What it can watch

- **All open conversation panes** in the selected Claude window, including Code
  split views. Browser and terminal tiles are excluded.
- **Only checked conversations**: read the loaded sidebar, check the conversations
  you want, and let the tool visit them in turn. You can collect chats from both
  Code and Chat and Cowork by switching modes in Claude and clicking **Read chats**.
- Each conversation has its own reset time, retry count and status. One waiting
  conversation does not block the others.

## Install and run

Requires Windows 10/11, Python 3.10+, and the signed-in Claude Desktop app.

```powershell
git clone https://github.com/MichalCholajczyk/claude-desktop-auto-resume.git
cd claude-desktop-auto-resume
```

Double-click **run.bat**. It installs `uiautomation` if needed and opens the tool.
Or run it manually:

```powershell
py -m pip install -r requirements.txt
py claude_auto_continue.py
```

## Set up an unattended run

1. Open the conversations you need in Claude Desktop.
2. Select the **Claude window** in Auto-Resume. Both **Refresh** and **Read chats**
   reload the conversation list, including conversations created since the last scan.
3. Choose **All open conversation panes**, or click the conversation rows to
   check them. Checking a row switches to **Only checked conversations**.
   Space and Enter also toggle the focused row.
4. Choose the optional behaviors below. Set your continuation text on the
   **Settings** tab; blank text falls back to `continue`.
5. Click **Start watching**. The list shows individual states and next attempt
   times. The clock shows the nearest scheduled attempt.

**Resume now…** applies to the chosen scope and asks for confirmation. It uses
Try again when preferred and available, otherwise sends the configured message.
It skips busy sessions, unanswered questions and existing composer drafts.

### After a usage limit

The tool recognizes Claude's usage-limit notice and reads its reset time. If
necessary, it opens the usage meter for that specific pane and reads the
**5-hour limit** row. A maxed weekly or context meter alone does not arm a send.

One minute after reset, the interrupted conversation receives the configured
message. Enable **Prefer “Try again” after a limit reset** to click that button
when available, with the configured message as fallback. A cleared banner at
send time does not cancel a previously armed continuation.

If the reset cannot be read, retry spacing defaults to ten minutes. You can also
enter `HH:MM` in **Know the reset time?** and arm all conversations in the chosen
scope. Manually armed timers and retry state are held in memory; after restarting
the tool, start watching again to detect current interruptions.

### API and server errors

**Retry API and server errors** recognizes current API errors and actionable
Try again controls. It prefers the retry button; when Code exposes only an error,
it sends the configured continuation message. Retries start after 30 seconds,
then back off to 60, 120, 240… seconds, capped at 15 minutes.

The default budget is six attempts per conversation. When it is exhausted the
conversation stays at **Needs attention** until you stop/start watching or use
Resume now. Account, authentication and non-retryable request errors require
manual attention. The classification follows the documented
[Claude API error types](https://platform.claude.com/docs/en/api/errors).

### Approach questions (optional, off by default)

Enable **Answer approach questions automatically** to handle Claude Code's live
question form. The tool selects options whose **labels** contain Recommended,
rekomendacja, rekomendowane, or equivalent supported Polish forms, then clicks
Submit. It ignores a recommendation mentioned only in an option description.

If no option is recommended, it fills the **Other** field with:

> Pick your recommended option(s).

It finds Other by its name, not its position. It leaves preselected answers,
nonempty answer drafts, unrecognized dialogs, and permission prompts alone.
The **Send automatically** setting controls both retries and approach answers.

## Practical limits

- Discovery reads **loaded sidebar rows**, including pinned rows that Claude
  exposes. It does not crawl thousands of historical chats or read private
  AppData databases. Open an older conversation or expand its sidebar section
  in Claude, then click Read chats again.
- Selected conversations must have unique full titles within their mode. If
  Claude renames one, check the new entry. Missing or ambiguous targets are
  skipped; the tool never falls back to typing at the window's center.
- Selection is saved by mode and title. Window handles and accessibility objects
  are not persisted. Choose the window again if Claude restarts.
- One Auto-Resume instance controls one selected Claude window and its splits.
  Separate top-level Claude windows need separate instances.
- Leave Claude visible and the desktop unlocked. Navigation and fallback keyboard
  input can briefly take focus. Do not type while an automated action is running.
- UI Automation depends on Claude's current accessibility structure. Unsupported
  layouts are reported in the log and skipped. A server outage cannot be forced
  to recover; the tool can only retry.

## Settings

Saved locally in `auto_continue_config.json`:

| Setting | Default | Meaning |
|---|---|---|
| `language` | `en` | English / Polish |
| `watch_scope` | `open` | `open` panes or `selected` conversations |
| `selected_chats` | `[]` | Mode and full-title identifiers |
| `message` | `continue` | Custom continuation text; line breaks become spaces |
| `prefer_try_again` | `false` | Prefer Try again after a usage reset |
| `retry_api_errors` | `true` | Retry current API/server errors |
| `auto_approach` | `false` | Answer live approach questions |
| `auto_send` | `true` | Disable for alerts only |
| `keep_awake` | `true` | Keep the system and display awake while watching |
| `scan_interval_s` | `20` | Approximate cycle interval; large lists take longer |
| `api_retry_wait_s` | `30` | Initial API retry delay |
| `max_retries` | `6` | Maximum attempts per interruption |
| `retry_wait_s` | `600` | Delay when a usage reset is unknown |
| `send_delay_after_reset_s` | `60` | Delay after a known reset |
| `verify_delay_s` | `30` | Wait before checking an action's result |
| `panel_backoff_s` | `300` | Minimum time between usage-panel reads per chat |

The main settings are in the UI; timing and retry-budget fields can also be
edited in the config file while Auto-Resume is closed.

## Development and verification

```powershell
py test_detection.py
py test_usage_meter.py
py -m unittest test_sessions test_send_after_reset test_app_ui -v
py inspect_claude.py --summary
py test_live_claude.py
```

The automated suite covers independent split targets and retry budgets, reset
sending after the banner clears, backoff, disabled options, drafts, ambiguous
names, recommendation labels, and Other fallback. It does not send real messages.

Opt-in live checks can exercise sidebar navigation (`--walk-sidebar`), recommended
selection (`--select-and-restore`) and literal Other-field entry
(`--type-and-restore`). They restore the changed controls without submitting an
answer. `inspect_claude.py` without `--summary` outputs a detailed local UI tree;
do not publish it without reviewing its titles and other visible information.

### Smoke-test results — 2026-09-09

Validated on Windows with the running Claude Desktop app:

| Check | Result |
|---|---|
| Limit-notice and reset-time detection | 11 checks passed |
| Usage-meter targeting | 8 checks passed |
| Session scheduler, refresh, reset-send regression and Tk UI | 37 tests passed |
| English / Polish switching and saved feature selections | Passed in the Tk UI suite |
| Python compilation | Passed |
| Live conversation discovery | Found 1 open Code conversation and 32 loaded sidebar entries |
| Live sidebar navigation | Opened the requested conversation and verified its own composer |
| Restore after navigation | Original conversation panes restored; no messages sent |

The top **Refresh** button is covered by a regression test: it reloads both
windows and conversations, including after Claude restarts. Earlier live checks
also exercised two simultaneous Code panes, recommended-option selection,
Other-field text entry and composer entry, restoring the changed controls.
API failures and usage resets were simulated in automated tests; no live outage
or quota exhaustion was induced. These results apply to the UI structure tested
on this date, rather than guaranteeing compatibility with future Claude releases.

## Privacy

No API key, external backend or telemetry is used. The app reads the local
accessibility tree and interacts with Claude. Actions submitted through Claude
are processed by Claude normally. The local log records conversation titles,
action statuses and diagnostics, but not full transcripts or custom message text.
Configuration contains selected conversation titles and your custom message.

## License

MIT.
