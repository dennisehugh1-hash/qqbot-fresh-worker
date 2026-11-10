# qqbot-fresh-worker

A publishable skill distilled from a real NapCat QQ bot whose replies are composed by a fresh Muse worker per incoming message. The bot runs on a Linux VM, survives host replacements through systemd supervision and a machine id backup, recovers missed messages after outages, and keeps private and group memory in separate files.

## What it contains

- SKILL.md holds the method. Architecture, wake flow, keepalive recovery, outage catch-up, file sending, link viewing, latency, and troubleshooting.
- skill.yaml holds the skill descriptor. Name qqbot-fresh-worker, category Work and knowledge, not listed in every prompt.
- scripts/bridge.py is the NapCat event receiver. It listens on 127.0.0.1 port 6198 and writes the inbox and history files.
- scripts/send.py sends one text message through the OneBot API.
- scripts/send_file.py sends one file through the OneBot API, upload first with a message segment fallback.
- scripts/catchup.py recovers messages missed during an outage from server history. Dry-run by default, and --inject writes to the inbox.
- scripts/link-view.py reads links, cards, and videos as silent background for one reply.
- scripts/start-all.sh is the single recovery entry point. It restores the machine id, reinstalls systemd units, and starts what is down.
- scripts/systemd/ holds the three unit masters for the bridge, the QQ client, and the AstrBot runtime.

## How to use it

- Copy the scripts into your own project folder and set the placeholders. Replace <user> in the systemd units with the Linux account that owns the files. Set BOT_QQ, OWNER_QQ, and CATCHUP_GROUPS in the environment before running catchup.py. Set QQBOT_HOME if your project lives somewhere other than ~/workspace/qqbot.
- Create bridge/napcat-api.env with mode 0600 holding NAPCAT_API and NAPCAT_TOKEN. Never commit that file and never paste its values into chat or docs.
- Write your own persona files. This skill describes the persona system and gives a generic structure. It ships no character content.
- Install the units, run start-all.sh, then run the five online checks from the skill. Back up /etc/machine-id to your backups folder right after the first login.
- Read SKILL.md before changing the wake flow or the recovery rules. The rules there are the ones that were learned the hard way.

## What it deliberately leaves out

- No credentials, tokens, keys, or passwords anywhere in this folder.
- No account numbers, group numbers, personal names, nicknames, or chat logs. Placeholders stand in for every identifier.
- No persona text, avatar, or signature. Those belong to the adopter.
- No paid model key. The AstrBot real-time route needs one, and this skill does not provide it.
- Some behaviors are untested in the source project and stay labeled as such in SKILL.md. History pagination depth, password login after a drop, and quick login after an abrupt kill were never proven live.
- The polling hook that wakes a worker per message is described in SKILL.md but its code is not shipped. The hook is runtime specific, so write your own poll script from the wake flow section.
