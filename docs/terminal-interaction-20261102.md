# Router terminal repair and two-pass review - 2026-11-02

## Final behaviour

The active desktop terminal is the native Qt component. The legacy HTML terminal is not in this call path. Existing unrelated work and the current branch were retained; no commit, package, installed executable or published release was changed.

- Ordinary wheel input scrolls desktop history. Persistent tmux sessions enable their own mouse-wheel history navigation. Pointer selection remains local for drag-copy and links; Ctrl+wheel remains local font zoom.
- Chinese glyphs occupy two cells. On scrollback restoration during font resizing, libvterm previously restored only the lead cell's style, leaving the continuation cell's colours and attributes zeroed. The continuation now inherits the full lead-cell state before receiving its continuation marker. No font, palette or layout redesign was introduced.
- The owner approved disabling the visual BEL flash. Tab completion input is unchanged.
- The owner explicitly approved clearing the terminal's stored output. Clear resolves the active pane of the exact application-owned tmux session, validates its numeric pane ID, and uses that fixed ID to exit copy mode, reset the display and remove history. Other windows/panes and files are untouched. This resets terminal display state; a full-screen application may need its normal redraw shortcut. No shell keystrokes or process termination are used. The existing worker enforces timeout/cancellation/connection identity checks, and repeats the cancellation/identity check before reporting success. Requests are bounded by one pending flag.
- An accepted Disconnect clears only the desktop after the worker finishes closing. Tail output arrives before the local reset. Cancelling disconnect preserves the view. This does not remove tmux history; reconnect can restore the remote session. Router login is retained.
- Reconnect used to replay a separate history snapshot of fixed window/pane 0.0, while tmux attached the actual active window. A two-window isolated case reproduced an old-PIN snapshot with current capture output in another window. This duplicate history path has been removed: tmux now owns both the current display and wheel history. Each new terminal worker resets the local buffer; ordinary state refreshes or input-queue warnings do not. This removes the wrong-pane replay path and its extra blocking history request.
- The install button is rendered from the actual dependency task on every refresh. Hidden/recreated pages adopt running installation IDs without requiring a popup. Completion, failed verification, cancellation, and request rejection restore the button and expose the result. Old task completions cannot finish a newer task. Existing dependency installation and post-install verification logic was retained; no new installer or timer was added.

## Compatibility sources

The support inventory lists tmux 2.9a, 3.1c, 3.2a, 3.3a, 3.5a and 3.6a. Each exact upstream tag was downloaded and reviewed for mouse/default wheel bindings, exact-session/current-pane targeting, pane_id formats, formatted conditional commands, display reset, copy-mode cancel and clear-history. URLs, hashes and date: terminal-interaction-sources-20261102.json. Cached source: build/terminal-review/sources. New remote behaviour depends on tmux userspace capabilities, not router CPU/wireless-driver selection. Plain sessions do not gain a tmux requirement. The install-state and desktop-disconnect changes add no router command.

## Review pass 1: state and lifecycle

36 targeted tests passed across tests.test_terminal_interaction, tests.test_terminal_install_state, tests.test_terminal_runtime and tests.test_wheel_routing. Review covered disconnect confirmation and late output, reconnect buffer identity versus normal refresh, live-stream-only reconnect, clear success/failure/timeout/cancellation/device changes, bounded duplicate requests, hidden/recreated installer pages, stale completions and a real dependency-service failed-verification completion. A cancellation/device change immediately before clear completion was explicitly hardened and tested.

## Review pass 2: actual rendering and sessions

20 interaction/installation tests passed with the Windows drawing backend and hidden native widgets. The actual Qt 6.3.2 DLL was checked against its source/build manifest and licenses. The official Qt SDK archive was checksum-verified; the standard native builder compiled, smoke-tested, preserved the previous DLL and installed the verified component. The final DLL SHA-256 is recorded in bin/router_terminal/build-manifest.json.

A Debian 12 isolated tmux 3.3a server, with a random test-only socket name, ran the production attach/clear commands against two windows and two panes. Real PTY wheel input entered copy mode in the active capture pane. Reconnect showed the capture pane rather than the old-PIN window. Clear affected the active pane, preserving the other pane and old-PIN window. Subsequent resize/reconnect did not restore cleared text. The original process remained alive and accepted fresh input. Result: build/terminal-review/tmux-smoke-result.json. The first multi-pane test aimed the wheel at the top pane; its coordinates were corrected to the active lower pane before rerunning. No production workaround was added for this test error. Debian packages were extracted only under the build test directory, not installed into the system.

Python syntax, UTF-8/mojibake and diff whitespace checks pass. Legacy migration test modules test_router_terminal/test_terminal_history are absent from their referenced original checkout, so those two old audit entrypoints could not run; existing JSON snapshots are not current evidence.

## Boundaries and remaining acceptance

Exit/relaunch the source app to load the rebuilt DLL, then reconnect once to apply tmux mouse support. Live router and Debian application-GUI/build acceptance remain pending; isolated tmux acceptance is not a claim that all supported firmware/models were exercised. An installed/frozen executable requires a separately requested package/update.

The report of a capture script returning to its menu within half a minute remains unconfirmed. The script and its output/log are required to distinguish actual process exit from the screen/history problems repaired here. No live capture script was run or modified.

## Regression follow-up: keyboard focus after Clear

The owner reported that clearing made input and file-run output appear unavailable, including after router re-login. A complete Windows-native-page/TerminalWorker/local-tmux bridge reproduced a definite defect: after clicking Clear, keyboard focus stayed on the toolbar SweepButton. The earlier separate tests manually focused the terminal, so they missed this transition. NativeTerminalView now returns focus to the connected terminal after successful local clear; disconnected display resets do not steal focus.

A permanent regression checks the actual page's focus owner after asynchronous clear completion. 37 affected tests pass. The end-to-end bridge ran the production page, service worker, native DLL and actual tmux over a local PTY: click Clear, type a command through the focused widget, observe the executed result on its own line, invoke the same terminal open_path operation used by File Manager, observe the script's executed marker, detach, simulate a new SSH client identity, reconnect, type again, clear again and run the script again. No artificial refocus is used after Clear. The re-login keyboard test initially used a literal newline in QTest.keyClicks, causing Qt's keyboard simulator to exit; it was corrected to a single-line command followed by an independent Enter event. No production workaround was added for that harness error. Result: build/terminal-review/full-chain-result.json; probe scripts: full_chain_probe.py and relay_tmux.py in the same directory.

This is a complete local bridge test, not live-router authentication or the user's capture script. Clearing does not stop a foreground script, so whether a foreground program was still consuming input at the user site remains unconfirmed. No router script was interrupted and no real device was accessed in this follow-up.
