# ADK Spogo Song Agent

This workspace includes an ADK Spotify assistant that supports four main user journeys:

1. Play a requested song with best-match selection (song + optional album hint).
2. Generate a structured song list from a free-form request.
3. Generate a list and add each song to the Spotify queue (playlist-style queueing).
4. Run an explicit 5-round song quiz with two attempts per round and final scoring.

## Project Structure

- `spogo_song_agent/agent.py` - ADK agent, tools, queue/play helpers, and quiz state engine.
- `spogo_song_agent/__init__.py` - package entry for ADK discovery.
- `spogo_song_agent/.env.example` - environment variable template.
- `requirements.txt` - Python dependency list.

## Prerequisites

- Python 3.10+
- `spogo` CLI installed and authenticated with Spotify.
- At least one active Spotify Connect device.
- `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) for song-list generation features.

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

Then edit `spogo_song_agent/.env` and set your API keys/model settings.

## Run in ADK Web UI

From the workspace root:

```bash
source .venv/bin/activate
adk web
```

If you prefer not to activate the venv:

```bash
./.venv/bin/adk web
```

In ADK Web:

1. Select `spogo_song_agent` in the agent dropdown.
2. Try one prompt from each interaction flow below.

## Interaction Flows (Current Behavior)

The following reflects the current implementation in `spogo_song_agent/agent.py`.

### 1) Play Song Flow

Tool: `play_song(song_name, album_name="")`

Example prompts:

- "Play Kesariya"
- "Play Apna Bana Le from Bhediya"

What happens:

1. Validates active Spotify device.
2. Searches top 5 tracks.
3. Picks best match using weighted song-name + album similarity.
4. Adds selected track to queue.
5. Executes `next` to start playback immediately.

### 2) Generate Song List Flow

Tool: `generate_song_list(user_request, count=5)`

Example prompts:

- "Give me 5 happy Hindi songs"
- "Suggest 8 songs for late-night lo-fi focus"

Output schema per item:

- `name` (string)
- `year` (integer)
- `album_name` (string)

Count behavior:

- Default: `5`
- Allowed range: `1` to `20`

### 3) Generate and Queue Playlist-Style Flow

Tool: `generate_and_queue_song_list(user_request, count=5)`

Example prompts:

- "Create a chill indie playlist and add 7 songs to my queue"
- "Find 5 workout tracks and queue them"

What happens:

1. Validates active Spotify device.
2. Generates structured song list.
3. Resolves and queues each song using the same best-match logic as `play_song`.
4. Returns `success` or `partial_success` with queued/failed details.
5. Includes queue announcements:
   - before: songs found
   - after: songs added to queue

### 4) Song Guess Flow (Outside Quiz)

Tool: `check_current_song_guess(song_guess)`

Example prompts:

- "My guess is Kesariya"
- "Is this song Apna Bana Le?"

What happens:

1. Reads current playback status.
2. Compares guess with currently playing song name.
3. Returns right/wrong with similarity score.

### 5) Quiz Flow (Explicit Start Only)

Tools:

- `start_song_quiz(user_request, count=5, tool_context=...)`
- `submit_song_quiz_guess(song_guess, tool_context=...)`
- `cancel_song_quiz(tool_context=...)`

Rules:

- Starts only on explicit quiz intent.
- Exactly 5 rounds.
- Maximum 2 attempts per round.
- Score +1 only for correct rounds.
- Auto-advance to next song between rounds.
- Final response includes category + score out of 5.

## Phase 5 Examples (Steps 18-19)

### Start Quiz Mode

Prompt:

"Start a song quiz for 90s Bollywood romance"

Expected response pattern:

- "Quiz started for '90s Bollywood romance'. Round 1 of 5 is now playing. You have 2 attempts to guess this song."

### Round-by-Round Guessing

Prompt sequence:

1. "Is it Tujhe Dekha Toh?"
2. "No, maybe Pehla Nasha"

Expected behavior:

1. Wrong attempt 1 -> stay on same song and prompt final attempt.
2. Attempt 2 outcome:
   - if correct: award point and move next.
   - if wrong: reveal correct song and move next.

Representative responses:

- "Wrong guess. Final attempt left for this song."
- "Right guess! Moving to the next song."
- "Wrong guess. The correct song was '<song name>'. Moving to the next song."

### Final Score Output

On round 5 completion, expected summary pattern:

- "Quiz complete for '90s Bollywood romance'. Final score: 3/5."

Returned summary fields include:

- `category_request`
- `score`
- `total_rounds`
- `score_text`
- `round_outcomes`

### Cancel Quiz (Optional Recovery)

Prompt:

"Cancel the quiz"

Expected result:

- Active quiz: cancelled and score progress returned.
- No active quiz: returns idle response safely.

## Notes

- If there is no active Spotify device, the agent asks the user to open Spotify and play any song to activate a device.
- While quiz mode is active, guesses are routed to quiz submission flow, not regular current-song guessing.
- If you want microphone voice requests in ADK Web, set `AGENT_MODEL` to a Live API-capable model in `.env`.
