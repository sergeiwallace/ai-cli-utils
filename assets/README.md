# Assets

## Files

| File | Description |
|------|-------------|
| `logo.svg` | Primary logo — source of truth, edit this |
| `logo.png` | Rasterized logo (400×400) — used by socialify |
| `ai-cli-utils_socialify_preview.jpg` | GitHub social preview image (1280×640 JPEG) |

## Updating the social preview image

### 1. Update the logo (if needed)

Edit `logo.svg`, then re-render the PNG:

```bash
npx @resvg/resvg-js-cli \
  --font-dir /usr/share/fonts/truetype/dejavu \
  --font-default-family "DejaVu Sans Mono" \
  --fit-width 400 \
  assets/logo.svg assets/logo.png
```

Commit and push both files. Then purge the jsDelivr cache so socialify picks up the new version:

```bash
curl https://purge.jsdelivr.net/gh/sergeiwallace/ai-cli-utils@main/assets/logo.png
```

### 2. Regenerate the socialify image

1. Go to https://socialify.git.ci/sergeiwallace/ai-cli-utils
2. Set logo URL to: `https://cdn.jsdelivr.net/gh/sergeiwallace/ai-cli-utils@main/assets/logo.png`
3. Configure options (description, language, stars, forks, issues)
4. Download as **JPEG** — **not PNG**. JPEG preserves the black background; PNG renders with a white background which kills the contrast with the green logo.
5. Replace `assets/ai-cli-utils_socialify_preview.jpg` with the new file

### 3. Upload to GitHub

Settings → General → Social preview → Upload image → select the JPEG.

> Regenerate when: star count grows noticeably, repo description changes, or logo is updated.
