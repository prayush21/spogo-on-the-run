# Spogo Concise Capability Guide

This version focuses only on:

- `play`
- `pause`
- `next`
- `status`
- `queue add`
- `track info`
- `search` (track + album)
- reading values from search results

## 1) Core Commands

Use `--engine=connect` for playback and search.

```bash
# Start playback (item optional)
spogo play [<item>] --engine=connect

# Pause
spogo pause --engine=connect

# Skip to next
spogo next --engine=connect

# Current playback status
spogo status --engine=connect
spogo status --engine=connect --json

# Add a track to queue (accepts URI/URL/ID)
spogo queue add <item> --engine=connect

# Track details (accepts ID/URI/URL)
spogo track info <id> --engine=web --json

# Search tracks
spogo search track "<query>" --engine=connect --json --limit=20 --offset=0

# Search albums
spogo search album "<query>" --engine=connect --json --limit=20 --offset=0
```

## 2) Key Search Fields

### `search track` JSON (`.items[]`)

- `id`
- `uri`
- `name`
- `type` ("track")
- `url`
- `album` (album name string)

### `search album` JSON (`.items[]`)

- `id`
- `uri`
- `name`
- `type` ("album")
- `url`

## 3) Read Values from Search Results

Requires `jq`.

```bash
# First track URI from track search
spogo search track "kesariya" --engine=connect --json --limit=1 \
  | jq -r '.items[0].uri'

# First track ID from track search
spogo search track "kesariya" --engine=connect --json --limit=1 \
  | jq -r '.items[0].id'

# First album ID from album search
spogo search album "bhediya" --engine=connect --json --limit=1 \
  | jq -r '.items[0].id'

# First track name + album from track search
spogo search track "apna bana le" --engine=connect --json --limit=1 \
  | jq -r '.items[0] | "\(.name) | \(.album)"'
```

## 4) Reliable Playback Pattern

In practice, this sequence is the most stable:

```bash
# 1) Select active device first
spogo device set <device-id> --engine=connect

# 2) Search and extract track URI
TRACK_URI=$(spogo search track "<query>" --engine=connect --json --limit=1 \
  | jq -r '.items[0].uri')

# 3) Queue + skip (often more reliable than direct play)
spogo queue add "$TRACK_URI" --engine=connect
sleep 1
spogo next --engine=connect

# 4) Verify
spogo status --engine=connect --json
```

## 5) Minimal Notes

- `play [<item>]` accepts Spotify ID/URL/URI.
- For raw IDs with `play`, optional flag: `--type=track|album|playlist|show|episode`.
- `search track` and `search album` support pagination with `--limit` and `--offset`.
- Output helpers:
  - `--json` for parsing
  - `--plain` for simple scripts
