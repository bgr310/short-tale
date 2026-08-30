# Secrets, and why this repo is safe to make public

The goal is that pushing this repo publicly leaks nothing, even if you are
careless. That is achieved structurally rather than by remembering to be
careful.

## Where every secret lives

| Secret | Where it lives | In git? |
|---|---|---|
| Reddit client id / secret | `.env` | no — gitignored |
| Pexels key (optional) | `.env` | no |
| App token (optional) | `.env` | no |
| **YouTube login** | Docker volume `publisher_profile` | **not on the repo path at all** |
| Campaign config | `config/campaigns/*.yml` | yes — validated to contain no secrets |

## Your YouTube password is never handled

There is no password field anywhere in this project, by design. You sign in
yourself, through a browser running inside the publisher container, viewed over
noVNC:

```bash
make login   # then open http://localhost:7900/vnc.html
```

What Google gives back is a session cookie stored in the container's profile
directory, which is a **Docker named volume** — not a bind mount, not a folder
inside the repo. It cannot be added to a commit, because it isn't on the
filesystem git can see.

If you ever want to revoke it: `docker volume rm short-tale_publisher_profile`,
and sign out of the session from your Google account's device list.

## Campaign files reject credentials before parsing

`config/campaigns/*.yml` is committed, so it is the most likely place for
someone to paste a key by mistake. A validator scans the **raw** YAML for
credential-shaped keys and refuses to load the campaign.

Running the scan before parsing matters more than it looks. Pydantic silently
drops unknown fields, so an `api_key:` line would have vanished from the parsed
model — passing validation cleanly while still sitting in the committed file.
The models also use `extra="forbid"`, so stray keys are loud rather than
ignored. There is a test for exactly this (`test_secrets_in_campaign_yaml_are_rejected`).

## Before every push

```bash
make scan                     # or ./scripts/check_secrets.sh
./scripts/install_hooks.sh    # makes it automatic on git push
```

The scan checks four things:

1. Files that must never be tracked (`.env`, profiles, cookie jars)
2. Key-shaped strings in tracked content (Reddit secrets, AWS, GitHub, private keys, serialised cookie jars)
3. Credential-shaped keys in campaign YAML
4. A full `gitleaks` pass, if it is installed

Exit code is non-zero on any hit, so it works as a hook or a CI step.

## If you already committed a secret

Rotate first, clean second — assume anything pushed is public forever.

1. Revoke the credential at its source (Reddit app → delete, regenerate)
2. `git rm --cached .env && git commit`
3. Purge history with [git-filter-repo](https://github.com/newren/git-filter-repo)
   or the BFG, then force-push
4. `make scan` to confirm

## Exposing the review UI

By default the UI binds to `127.0.0.1` and is reachable only from the machine
running Docker. If you change `APP_BIND` to `0.0.0.0`, **set `APP_TOKEN` in
`.env`** — every mutating endpoint (create, edit, approve, reject) then
requires it. Read-only routes stay open, so put it behind a VPN or reverse
proxy with auth if the network is not one you control.

The noVNC port is bound to loopback and has no password, on the assumption
that it is unreachable from outside the host. If you change that binding, add
a VNC password in `docker/publisher/start.sh` (`x11vnc -rfbauth`).

## What leaves your machine

- **Reddit**, for the posts you configured it to search
- **RSS feeds**, if you enabled any
- **Model downloads**, once, from GitHub and Hugging Face
- **YouTube**, only when you approve an upload

No inference, no telemetry, no analytics. The LLM, the voice, and the caption
model all run in containers on your hardware.
