# Architecture

## Containers

| Service | GPU | Holds | Why separate |
|---|---|---|---|
| `ollama` | yes | model weights | Restarting the app shouldn't unload a 9GB model |
| `api` | no | review UI, scheduler | A stuck render must not take down the UI |
| `worker` | yes | the pipeline | Where all the heavy lifting happens |
| `publisher` | no | the login profile | **The only container with a credential** |

`api` and `worker` share one image and differ only by command.

Keeping the publisher separate is the important boundary: the container that
holds a live YouTube session runs no model, does no scraping, and touches
nothing but a video file and a metadata blob. The GPU worker never handles a
credential of any kind.

## The pipeline

```
harvest ─▶ rank ─▶ script ─▶ voice ─▶ captions ─▶ visuals ─▶ render ─▶ [review] ─▶ publish
```

**harvest** — PRAW against your subreddits and queries; optional RSS with
Whisper transcription for podcast audio. A dedupe ledger in SQLite stops the
same post becoming a second video.

**rank** — a free heuristic pass (engagement, recency, keyword density,
first-person voice) narrows the field, then a batched LLM pass scores the
survivors. The heuristic exists so GPU time is never spent judging obvious
junk; the blend is 70% model, 30% heuristic.

**script** — structured JSON (hook / beats / CTA / title / description / tags),
validated and retried up to three times. Validation is mechanical (length
budget, ad-speak phrases, forbidden claims, exactly one product mention) plus a
second model pass checking claims against the allowed list. Anything unresolved
becomes a warning shown in the review UI rather than a silent pass.

**voice** — Kokoro via ONNX on the CPU. Each line is synthesised separately so
beat boundaries are known exactly, then concatenated with small gaps.

**captions** — Whisper provides *timing* only. Its transcript is aligned back
onto the script we already wrote with a sequence diff, so a mis-heard word can
never appear on screen. Unmatched runs are interpolated between their
neighbours; a weak alignment falls back to length-weighted even spacing.

**visuals** — cards drawn with Pillow, not screenshotted. No browser in the
worker, nothing to be blocked by, and the card restyles to the campaign palette
instead of breaking when Reddit ships CSS.

**render** — background first as its own file (cacheable, inspectable), then
one ffmpeg pass for overlays, burned-in ASS captions, and audio. NVENC is
probed with a real one-frame trial encode rather than trusting `-encoders`,
because inside a container without the `video` capability the encoder lists but
every session fails to open.

## VRAM discipline

12GB does not fit a 14B model and Whisper at once, so stages are strictly
sequential and the LLM is explicitly evicted (`keep_alive: 0`) before Whisper
loads. Speech stays on the CPU — at 82M parameters the GPU would gain it almost
nothing while costing contention and a fragile onnxruntime/CUDA pairing.

## Failure behaviour

Every stage failure is caught, recorded on the job, and leaves
`data/work/job_NNNNNN/` intact — script, per-line audio, cards, captions, and
background all separately inspectable.

Fallbacks are layered so a job degrades rather than dies: background generation
falls back across strategies to a flat colour; caption alignment falls back to
even spacing; the speech engine falls back from Kokoro to Piper; the encoder
falls back from NVENC to libx264. An upload failure returns the job to the
review queue rather than marking it failed, because the video is fine and the
natural next step is a human retry.

## Data

SQLite in WAL mode. One file, no extra container, trivially backed up, and
comfortably fast for a queue producing a couple of videos a day. Three tables:
`jobs`, `seen_items` (the dedupe ledger), and `publish_log` (which enforces
`max_per_day` against real uploads rather than attempts).

## Extension points

- **A new source**: implement a `Candidate`-returning function and register it
  in `sources/base.py:harvest_all`
- **A new visual template**: add to `style.format` and branch in the visuals
  stage; the card renderers are independent of the compositor
- **A new background preset**: one entry in `render/backgrounds.py:_preset_filter`
- **A different upload target**: reimplement `POST /upload` in the publisher;
  nothing upstream changes
