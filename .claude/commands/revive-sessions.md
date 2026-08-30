---
description: Find Claude Code sessions whose Remote Control dropped, and reconnect them
argument-hint: "[--apply] [host]"
allowed-tools: Bash(xa revive:*), Bash(ssh tw:*), Bash(priv print-live-sessions-report:*)
---

Find running Claude Code sessions that are no longer reachable from
claude.ai, and reconnect the ones it is safe to reconnect.

## Do this

1. **Report first, on every machine that hosts panes.** Remote Control is
   reconnected by typing into a pane, so only tmux-hosted sessions can be
   fixed automatically.

   ```bash
   xa revive                 # this machine: one line per live claude pane
   ssh tw 'xa revive'        # the server, where the long-running sessions live
   ```

2. **Show the user the report and stop there unless they said `--apply`.**
   Dry run is the default for a reason: reconnecting sends keystrokes into
   a live session.

3. **With `--apply`, send `/remote-control` to the `reconnectable` panes:**

   ```bash
   xa revive --apply
   ssh tw 'xa revive --apply'
   ```

4. **Cover the sessions with no pane.** `priv print-live-sessions-report`
   lists every live session with a `Remote Control: on|off` line. A session
   that is `off` and not in a tmux pane cannot be reconnected from outside
   its process — tell the user which ones they are, and that the fix is to
   focus that terminal and run `/remote-control` there
   (`priv focus-session -s <id>` raises the right tab).

## What it will refuse to do, and why you should not talk it out of it

- **A session held on another device** is reported `held_elsewhere` and left
  alone. Reconnecting it takes it back from the user's phone mid-sentence.
  `--include-held-elsewhere` exists; it is the user's call, never yours.
- **A pane with unsent text in its prompt** is skipped. `send-keys` appends
  to the buffer, so reconnecting would submit the half-typed instruction
  sitting there, plus `/remote-control`, as one prompt.
- **A busy or stalled session** is reported, never touched. Fixing a stalled
  API call means resending a prompt, which costs money and can duplicate
  work — hand that back to the user as a decision.
- **The same pane twice in ten minutes.** A rate guard on disk makes a
  second run a no-op; that is working as intended, not a failure.

## If nothing is found

`no live claude panes found` means no session on that host runs under tmux —
on a machine where Claude Code lives in terminal tabs, that is the expected
answer, not a bug. The panes are on the server.
