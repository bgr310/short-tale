# B-roll library

Drop looping background clips here. The pipeline uses them when a campaign's
`style.background.mode` is `library` or `library_first`, and as the fallback
when generated backgrounds are unavailable.

**What works well**

- Vertical (9:16) or large landscape footage — anything is scaled and centre-cropped to 1080x1920
- 15-60 seconds, no hard cuts, no on-screen text
- Calm and low-contrast, so captions stay readable on top

**Tagging**

Clips are matched against a campaign's `style.background.library_tags`. Tags
come from either:

1. `manifest.yml` in this folder:

   ```yaml
   calm-ocean-loop.mp4: [calm, abstract, water]
   city-timelapse.mp4:  [urban, busy]
   ```

2. Or, with no manifest, the words in the filename itself — so
   `calm-abstract-loop.mp4` matches the tags `calm` and `abstract`.

**Licensing is on you.** Everything in this folder is gitignored, so nothing
here is published with the repo. Only use footage you have the right to use.
