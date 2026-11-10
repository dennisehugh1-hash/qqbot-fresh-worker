# QQ Bot on Fresh-Worker Wakes

## Architecture and layout

- Run QQ through NapCat, which exposes a WebUI on port 6099 and a OneBot HTTP API on port 3000.

- Keep a small local bridge on 127.0.0.1 port 6198. It receives NapCat HTTP events, appends message events to an inbox file and to per-context history files, and writes other events to an events file.

- Wake a fresh Muse worker for each new inbox line with a polling hook. The worker composes the reply and sends it back through the OneBot API. The worker holds no memory between wakes, so every wake re-reads what it needs.

- Supervise the bridge, the QQ client, and the optional AstrBot runtime with systemd and Restart always. Keep the unit masters in the project folder, because the host wipes the system directory on replacement and the units must be reinstalled from home.

- Use start-all.sh as the single recovery entry point. It restores the machine id from a home backup before QQ starts, reinstalls missing units, and starts whatever is down. It is idempotent, so run it as often as needed.

- Lay the project out as bridge, personas, systemd, backups, and logs under one project root. Each persona folder holds a persona file, a memory file, and two split history files. One selector file names the active persona.

- Keep a single primary engine at a time. Either Muse answers through the bridge or AstrBot answers through a paid model API. Never let both answer the same message.

- Switch personas by changing the active field in the selector file. The next wake uses the new persona, and the other persona files stay untouched.

## Answer one message

- Read the selector file first on every wake, then read only the active persona branch. Never read the other persona files.

- For a task persona, read the persona file, then the memory file, then only the history file that matches the message context. Read the private file for private messages and the group file for group messages. Never read the other split file for that message.

- For a character persona, read the core card first, then the integration rules, the style checklist, the persona file, the anchor card, the guard rules, the dynamic memory, the media index, the matching history file, and the life log, in that order.

- Check the tail of the matching history file before replying. If an out entry to the same user or group already exists with a timestamp after the incoming message, skip the message.

- Answer every private message. Answer a group message only when it is addressed to the bot. An at mention, the bot name spoken aloud, or a reply to one of the bot messages counts as addressed. Log other group messages without replying.

- For a task that takes over 30 seconds, send a receipt first, write a progress file, and update it at each milestone. Answer progress questions from the progress file without interrupting the running task.

- Dedupe a new task before starting it. Normalize the text and check the pending list and the current task file. If the same person asked for the same thing within 30 minutes and it is still running, report the current step instead of starting over.

- Update the memory file only when a durable fact, preference, or task outcome changed. Bump the version by one and replace the file atomically. Never let two engines write the same memory file at once.

- Advance the inbox and events offsets only after a successful send. Never advance past a failed message.

## Keep the bot online

- Verify online status with five checks. The bridge root returns bridge up. The AstrBot panel answers HTTP 200. The WebUI and the OneBot API both listen. The login info call returns retcode 0 with the right account. The status call reports online true and good true. Wait about 45 seconds after a start before judging a service dead.

- Run start-all.sh after a host replacement. It restores the machine id from the home backup only when QQ is down, reinstalls missing systemd units, and starts whatever is down.

- Treat the machine id as the device identity. Back it up to the home backups folder right after the first login. A replaced host gets a random machine id, and the platform sees a new device with an old token and drops the session to QR wait. Restoring the backed-up id lets QQ present the old device identity and log in without a scan.

- Check login health every 10 minutes. On the first API-unreachable check, run start-all.sh exactly once for that outage episode, then verify on the next check. If it is still unreachable, report once and suppress repeats until recovery. The user decides the next step.

- Never auto-restart when the API answers but reports offline or the wrong account. That state needs a rescan or manual recovery, and a restart cannot create a login.

- Never restart QQ while it sits in a clean QR wait, and never add a quick-login flag to it. A failed quick login drags the login page into SMS verification and poisons the QR endpoint until QQ fully restarts.

- Never log in with a password or captcha from automation. Never log in with the same account from a second client while NapCat is online. Never put a second account in the same QQ data directory.

- Treat a fresh QR code as short-lived. It is about 147 by 147 pixels and valid for about 120 seconds. Verify its MD5 against the NapCat cache copy before sending it to the user.

## Recover missed messages and send files

- Remember that the bridge only receives live events. A message sent while the bridge or QQ is down never enters the inbox and is not replayed after recovery. Catchup recovers it from server history.

- Run catchup.py dry-run first. It prints missed trigger messages and writes nothing. Add --inject only when you want it to write.

- Pull group history with get_group_msg_history for each known group. Keep only messages that mention the bot through an at mention or the bot number in the text.

- Pull friend history with get_friend_msg_history for each owner. Keep only incoming messages. Private chats are always answered, so every incoming private message counts.

- Dedupe with two checks, and a message must pass both. Check the message id against the inbox file. Check user, time, and text against the history files. History entries carry no message id, so message id dedupe alone would re-inject messages already answered by hand.

- Inject with --inject to append each missed message to the inbox as a bridge-shaped event and to append the matching in entry to the active persona history file for that context. Inject oldest first so order is preserved. A later run dedupes what was injected.

- Check dedupe across both personas and both contexts. A message handled by either persona counts as handled, so a persona switch can never make catchup re-inject it. Inject still writes only to the active persona files.

- Keep the window at the last 6 hours by default and change it with --max-age-secs. Anchor the window at now, not at the last inbox receive time, because live traffic after an outage moves that time past the outage. Dedupe is what prevents reprocessing, not the window.

- Run catchup with --inject once when a keepalive check confirms the bot is back online after an outage episode. A catchup failure does not change the recovery result and never triggers a retry or restart.

- Accept the known limits. Group replies without an at mention are not detected. Only the last count messages per source are fetched, with a default of 50, and pagination depth is untested. Images in missed messages are not pre-downloaded.

- Send files with send_file.py. It tries the upload file API first and falls back to a file message segment. Verify the file exists and opens before sending, and say in chat what file was sent.

- Treat a send_file.py timeout as unknown, not as failure. Wait 30 seconds, then check the matching history file for a recent out record with the same file name. If one exists, do not resend. If none exists, resend once and say the first attempt timed out.

- Split a long reply across send.py calls, one call per bubble, about 1 second apart, at most 3 bubbles. Give images their own bubble.

- View links, cards, and videos only as silent background with link-view.py. Use at most one per wake and only for the message being replied to. It fetches titles and descriptions, downloads video up to 50 MB, and extracts 3 frames. It never logs in, never sends credentials, and never follows non-http schemes. Never mention the helper, frames, or cover-only results in the QQ reply. If a link cannot be opened, ignore it, tease, or deflect in character without inventing unseen content.

## Latency and the real-time alternative

- Expect the poll floor to be 5 seconds. That is the smallest hook interval, so a message waits 0 to 5 seconds to be noticed, 2.5 seconds on average.

- Expect a fresh worker per wake to dominate the wait. It must re-read the persona, the memory, and the history tail, then think, then send. Measured wakes took 35 to 68 seconds for the full character rule stack. A task persona reads far less and answers faster.

- Do not blame file size for the wait. A wake reads about 13 KB of small files plus a history tail, and reading them takes milliseconds. The cost is the fresh worker loading context and the model thinking.

- Use AstrBot plus a paid model API for near real-time replies. Measured replies took about 6 seconds through a persistent websocket. It costs API usage on every message and needs a funded key. The fresh-worker route costs no API calls and answers in tens of seconds. Choose by budget.

- Do not try to cut latency by shrinking files further. The files are already small. The levers that matter are the engine choice and the size of the rule stack the wake must read.

## Troubleshoot common failures

- Bring all components down after a host replacement, then run start-all.sh and run the five online checks. Check that the machine id backup exists. If it is missing, expect a QR wait.

- Move a service under systemd with Restart always when it keeps dying after each worker run. A process started from a short-lived worker run is reaped when that run ends, and a detached start does not save it. Never start long-lived services from an agent call.

- Report an unreachable API on the first check and let the keepalive run start-all.sh once. Do not run it twice in the same episode and do not loop retries.

- Leave the account alone when the API answers but reports offline or the wrong account. That state needs a rescan or manual recovery.

- Leave QQ alone while it sits in a clean QR wait. Do not restart it and do not add a quick-login flag. A failed quick login drags the login page into SMS verification and poisons the QR endpoint until QQ fully restarts.

- Check history before resending a file after a timeout. A timeout is not proof of failure, and a second send can deliver a duplicate.

- Treat a message as handled when either persona handled it. Catchup dedupes across both personas, so a persona switch never re-injects an answered message.

- Answer group messages only when addressed. If the bot answers group messages it should ignore, check the at mention, name call, and reply triggers.

- Verify a quick login claim against start-all.sh. The systemd path never passes the quick-login flag to QQ. The flag appears only in the setsid fallback and in the systemctl active check.

- Leave history files alone as they grow. A thousand lines is a few hundred kilobytes, and the whole project history stays under a few megabytes. Pruning history to save space buys nothing.

## Available connectors

- Use `muse.connector_search` to find other connectors and `muse.skill_search` to find related skills.

- If nothing here fits, look on the web for a maintained API or MCP server and set it up with [Custom Connectors and MCPs](/opt/muse/skills/custom-connector-setup/SKILL.md), or build a custom connector.
