# Publishing

## The honest caveat

YouTube uploads here are driven through the **YouTube Studio web UI** with
Playwright. This is not a supported automation surface. It works, and it
avoids the Data API's quota (which allows only a handful of uploads a day), but
Google changes that interface without notice. When an upload breaks after
months of working, that is expected maintenance, not a bug in your setup.

Two consequences worth planning around:

- **Treat a broken upload as normal.** The selectors in
  `src/shorttale/publish/youtube.py` are deliberately redundant — each
  interaction tries a list of candidates — but a redesign will eventually
  outrun them. A failed upload returns the job to the review queue with the
  video intact, so you can always upload it by hand from `out/`.
- **Automated uploads carry account risk.** YouTube's terms restrict automated
  access. A low volume of reviewed, genuinely useful videos is a very different
  posture from a bot posting fifteen times a day, and `max_per_day` exists to
  keep you on the right side of that. This is your channel; decide deliberately.

If you would rather be on supported ground, the **YouTube Data API v3** is the
sanctioned route: OAuth, `videos.insert`, no browser. The tradeoff is a quota
of roughly six uploads per day and a Google Cloud project to maintain. The
publisher service is a clean seam — swapping the implementation behind
`POST /upload` is the whole change.

## Modes

Set per campaign under `publish.mode`:

| Mode | Behaviour |
|---|---|
| `review` | Renders and waits for you to press Approve. **Default.** |
| `auto_private` | Uploads automatically as Private. You flip it public. |
| `auto` | Uploads at the configured visibility, unattended. |

`review` is the default for a reason. A local model will occasionally write
something off-key, and the thirty seconds it takes to watch a Short is much
cheaper than a public misfire under your brand.

## First-time sign-in

```bash
make up
make login
# open http://localhost:7900/vnc.html and sign in to Google
```

The browser is real, headful, inside a virtual display. Do the whole flow
including 2FA. When you land on the Studio dashboard the script confirms the
session and closes the browser. It survives restarts; you only repeat it if
you clear the volume or Google expires the session.

Check it any time with `make doctor`, which reports whether the profile is
still signed in.

## Shorts classification

There is no "make this a Short" toggle. YouTube classifies a video as a Short
automatically when it is **under 60 seconds** and **vertical**. The pipeline
renders 1080×1920 and the campaign schema refuses a `max_seconds` above 60 when
YouTube is a target, so this takes care of itself.

## TikTok

Deliberately not automated. TikTok's upload flow has aggressive bot detection,
and driving it reliably means the kind of fingerprint evasion that gets
accounts banned rather than merely rate-limited. Automating it badly is worse
than not automating it.

What you get instead: the rendered file in `out/`, with the title, description,
and tags already written, ready to drag into the TikTok uploader. Listing
`tiktok` under `publish.platforms` returns a clear error rather than
pretending.

## Daily caps

`publish.max_per_day` is enforced against a log of actual successful uploads,
not attempts. When the cap is hit, approved jobs wait rather than failing, and
publish on the next run once the 24-hour window rolls.

## Disclosure

If your videos promote a product you own, say so. `publish.description_footer`
is appended verbatim to every description — the right place for a disclosure
line. Many jurisdictions require it, and audiences work it out anyway.
