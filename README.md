# short-tale

A fully local pipeline that finds real conversations about a topic you care
about, writes a short vertical video around one of them, renders it on your
own GPU, and — once you approve it — posts it to YouTube Shorts.

No cloud inference. No API keys for anything that thinks. The language model,
the voice, and the caption timing all run on your machine, in containers, and
the only outbound traffic is to the content sources you configure.

```
  reddit / rss  ──▶  rank  ──▶  script  ──▶  voice  ──▶  captions
                      (LLM)     (LLM)      (Kokoro)    (Whisper)
                                                            │
        YouTube  ◀──  YOU APPROVE  ◀──  render  ◀──  cards + background
                       (review UI)      (ffmpeg/NVENC)
```

**Built for:** an RTX 3080 Ti (12GB) and an older CPU, on Windows 11 with
WSL2 and Docker Desktop. It runs on native Linux too, and degrades to CPU-only
if no GPU is visible — slower, but it still finishes.

---

## Quick start

```bash
git clone <your-repo> short-tale && cd short-tale

make setup            # creates .env from the template
#                       edit .env and add your Reddit app credentials
make build            # builds the images (10-15 min the first time)
make up               # starts ollama, the worker, the UI, the publisher
make models           # downloads the LLM + voice + caption models (~12GB, once)
make doctor           # checks GPU, NVENC, models, credentials
make login            # sign in to YouTube by hand, once

open http://localhost:8080
```

Then pick a campaign and press **Generate one now**. When it finishes, the
video appears in the review queue. Watch it, edit the title if you like, and
press **Approve**. Only then does anything reach your channel.

### Before you trust it with anything

```bash
make dry              # renders a video from bundled fixtures
                      # no network, no GPU, no credentials needed
```

If that produces a file in `out/`, your render path works and every later
problem is a configuration problem.

---

## What you need

| | |
|---|---|
| GPU | RTX 3080 Ti or similar, 12GB. Works on 8GB with a smaller model. |
| Disk | ~25GB for models and images, plus whatever your videos take. |
| Windows | WSL2 + Docker Desktop with **Settings → Resources → WSL Integration** and GPU support enabled. |
| Reddit | A free "script" app from https://www.reddit.com/prefs/apps |

Nothing else. No paid API, no account anywhere.

---

## Defining what it makes

Everything lives in one YAML file per campaign in `config/campaigns/`. These
files are committed, so they never contain credentials — see
[docs/SECURITY.md](docs/SECURITY.md).

`config/campaigns/tailmailer.yml` is a complete worked example. The four parts
that matter:

**1. The topic — what to hunt for**

```yaml
topic:
  description: >
    People frustrated by spam email: junk after signing up for something,
    their address being sold or leaked, unsubscribe links that do nothing.
  keywords: [spam email, sold my email, unsubscribe, burner email]
  exclude_keywords: [spam recipe, cold email, deliverability]
  min_relevance: 65        # 0-100; nothing below this becomes a video
```

**2. Where to look**

```yaml
sources:
  reddit:
    subreddits: [privacy, PrivacyGuides, gmail, Scams, mildlyinfuriating]
    queries: ["how did they get my email", "my email was sold"]
    time_filter: year
    min_score: 25
  rss:
    enabled: false          # podcasts and blogs; audio is Whisper-transcribed
    feeds: [https://example.com/feed.xml]
```

**3. The product, and what may be said about it**

```yaml
product:
  name: Tailmailer
  url: tailmailer.com
  one_liner: An email proxy that gives you a unique masked address per signup.
  claims_allowed:            # the ONLY things the model may assert
    - masks your real email address behind a unique forwarding alias
    - lets you disable an individual alias at any time
  claims_forbidden:          # caught mechanically AND by a second model pass
    - free forever
    - blocks 100% of spam
    - any specific pricing
  plug_intensity: soft       # soft | medium | direct
```

This is the part worth spending time on. It is what stops a local model
inventing a feature you don't have and putting it in public under your name.

**4. How it looks, sounds, and ships**

```yaml
style:
  format: reddit_card
  duration: {min_seconds: 24, target_seconds: 38, max_seconds: 55}
  background:
    mode: generated_first    # generated | library | generated_first | library_first
    preset: aurora_drift
    colors: ["#0b1020", "#1b2a5e", "#3d7dff"]
  captions:
    style: karaoke
    highlight_color: "#3D7DFF"

publish:
  mode: review               # review | auto_private | auto
  visibility: public
  max_per_day: 2
  schedule: "0 9,17 * * *"   # empty = manual only
```

Run `docker compose exec worker shorttale campaigns` to see every campaign as
the system understands it.

### Adding a campaign

Copy `tailmailer.yml`, change the name, and restart the API. That's the whole
process — a second product or a second topic is a second file.

---

## Backgrounds

`mode: generated` builds the background from ffmpeg filters. Nothing to
download, nothing to license, and it can never fail for want of an asset.
Presets: `aurora_drift`, `radial_pulse`, `plasma_drift`, `soft_grain`, tinted
by `style.background.colors`.

`mode: library` uses clips you drop into `assets/broll/`, matched against
`library_tags`. See [assets/broll/README.md](assets/broll/README.md).

`generated_first` and `library_first` try one and fall back to the other, so a
missing clip never fails a job.

> Generated backgrounds are rendered at 270×480 and scaled up. A soft gradient
> has no detail worth preserving, and the blur costs ~16× more per frame at
> full resolution — the difference between a render that finishes on an older
> CPU and one that doesn't.

---

## How the 12GB gets shared

The pipeline is strictly sequential, because on a 12GB card a 14B model at Q4
(~9GB) and Whisper cannot both be resident:

1. **Ollama** loads for ranking and scriptwriting
2. The client calls `unload()`, evicting it from VRAM
3. **Whisper** loads for caption timing, then releases
4. **ffmpeg/NVENC** encodes

Speech runs on the **CPU** on purpose. Kokoro is an 82M-parameter model, so the
GPU would gain it almost nothing, while costing VRAM contention and an
onnxruntime/CUDA version matrix that breaks on upgrades. It is comfortably
faster than realtime on an older CPU.

Rough timings on a 3080 Ti for a 40-second video: 1-2 min for the language
stages, 20-40s for speech, 15-30s for captions, 30-60s for the render.

### Using a different model

```bash
# .env
OLLAMA_MODEL=llama3.1:8b-instruct-q5_K_M       # lighter, ~6GB
OLLAMA_MODEL_FAST=qwen2.5:3b-instruct-q4_K_M   # for the cheap ranking pass
```

Then `docker compose exec ollama ollama pull <model>`. Any Ollama model works;
nothing in the code is tied to a particular one.

---

## Publishing

Default is `mode: review` — nothing is uploaded until you press Approve in the
UI. This is the recommended setting and the reason the review queue exists.

`auto_private` uploads automatically as Private, so you flip it public
yourself. `auto` uploads at the configured visibility with no human in the
loop; `max_per_day` is the only brake.

Sign-in happens once, by hand, through a browser running inside the publisher
container:

```bash
make login          # then open http://localhost:7900/vnc.html and sign in
```

**This project never sees, stores, or transmits your password.** The session
cookie lives in a Docker named volume that cannot be committed. There is no
credential in the repo, the env file, or the images.

Read [docs/PUBLISHING.md](docs/PUBLISHING.md) before enabling any auto mode —
it covers what breaks, why TikTok is manual, and the officially supported API
alternative.

---

## Before you push to a public repo

```bash
./scripts/check_secrets.sh     # or: make scan
./scripts/install_hooks.sh     # runs it automatically on every push
```

The design keeps secrets out of reach by structure, not vigilance:

- Credentials only ever come from `.env`, which is gitignored
- Campaign YAML is *validated* to reject anything that looks like a key,
  before parsing, so a pasted secret fails loudly instead of sitting in a
  committed file
- Login cookies live in a Docker volume, never on the repo path
- Generated media, models, and b-roll are all gitignored

[docs/SECURITY.md](docs/SECURITY.md) has the details.

---

## Commands

```
make setup     create .env and the working folders
make build     build images          make up      start everything
make down      stop                  make logs    tail all services
make doctor    check the environment make models  download models (once)
make login     sign in to YouTube    make dry     offline test render
make run C=tailmailer                generate one video now
make test      run the test suite    make scan    check for secrets
```

---

## When it doesn't work

**`make doctor` says NVENC unavailable** — encoding falls back to CPU, which
works but is slow. On WSL2 confirm Docker Desktop has GPU support on, and that
the compose file's `capabilities: [gpu, compute, utility, video]` survived any
edits. `video` is the one that exposes the encoder.

**"no candidates found"** — usually missing Reddit credentials (`make doctor`
will say so), or a `time_filter` and `min_score` combination that is too
strict. Widen `time_filter` to `year` and drop `min_score`.

**"nothing scored above min_relevance"** — the harvest worked but the model
judged everything weak. Lower `min_relevance`, or add queries closer to how
people actually phrase the complaint.

**The upload fails partway** — the job returns to the review queue rather than
failing, so you can retry it. YouTube Studio's UI changes without notice; see
docs/PUBLISHING.md.

**A job failed** — the work directory under `data/work/job_NNNNNN/` is kept
intact, with the script, audio, cards, captions, and background all separately
inspectable.

---

## Layout

```
config/campaigns/     what to make — committed, never contains secrets
src/shorttale/
  sources/            reddit + rss harvesting
  llm/                ollama client, prompts
  pipeline/           rank → script → orchestrate → publish
  tts/                speech, caption timing
  render/             cards, backgrounds, subtitles, ffmpeg
  publish/            browser automation (separate container)
  web/                review UI
assets/               your b-roll, fonts, music — gitignored
out/                  finished videos
```

## Licence

MIT. See [LICENSE](LICENSE).

The tools are yours to point wherever you like; what you make with them is on
you. Be decent about it: the people whose posts become source material are
real, didn't volunteer, and are anonymised by default
(`style.anonymize_authors`).
# short-tale
