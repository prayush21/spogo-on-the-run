# ADK Spogo Song Agent

This workspace now includes an ADK agent that:

1. Takes a song request from the user.
2. Runs `spogo search track <query> --engine=connect --json --limit=1 --offset=0`.
3. Selects the first search result.
4. Plays it on Spotify (with queue fallback if direct play fails).
5. Generates recommendation lists (default 5 songs) from free-form prompts like mood, genre, album, artist, or vibe.

## Project Structure

- `spogo_song_agent/agent.py` - ADK agent + tools.
- `spogo_song_agent/__init__.py` - package entry for ADK discovery.
- `spogo_song_agent/.env.example` - environment variable template.
- `requirements.txt` - Python dependency list.

## Prerequisites

- Python 3.10+
- `spogo` CLI installed and authenticated with Spotify.
- An active Spotify device (or set `SPOGO_DEVICE_ID` in `.env`).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create the real `.env` file from the example:

```bash
cp spogo_song_agent/.env.example spogo_song_agent/.env
```

Then edit `spogo_song_agent/.env` and set your Google API key.

## Run in ADK Web UI

From this workspace root (`my-agent-2`), activate the environment and run:

```bash
source .venv/bin/activate
adk web
```

If you prefer not to activate the venv, run:

```bash
./.venv/bin/adk web
```

In the UI:

1. Select `spogo_song_agent` in the top-left agent dropdown.
2. Ask for a song, for example:
   - "Play Kesariya"
   - "Play apna bana le"
   - "Play songs by Arijit Singh"
3. Ask for a song list, for example:
   - "Give me 5 happy Hindi songs"
   - "Suggest songs from 90s Bollywood romance"
   - "Create a chill indie playlist, 7 songs"

## Song List Tool Output

For recommendation/list requests, the tool returns structured JSON items with:

- `name`
- `year`
- `album_name`

Default list size is `5` if user does not request a number.

## Notes

- The tool uses `play` first, then falls back to `queue add` + `next` for reliability.
- If you want microphone voice requests in ADK Web UI, set `AGENT_MODEL` to a Live API capable model in `.env`.
