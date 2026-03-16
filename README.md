# ADK Spogo Song Agent

This workspace includes an ADK Spotify assistant that supports four main user journeys:

1. Play a requested song with best-match selection (song + optional album hint).
2. Generate a structured song list from a free-form request.
3. Generate a list and add each song to the Spotify queue (playlist-style queueing).
4. Run an explicit 5-round song quiz with two attempts per round and final scoring.

## Project Structure

- `spogo_song_agent/agent.py` - ADK agent, tools, queue/play helpers, and quiz state engine.
- `spogo_song_agent/__init__.py` - package entry for ADK discovery.
- `spogo_song_agent/requirements.txt` - agent-local dependencies used by ADK Cloud Run packaging.
- `spogo_song_agent/.env.example` - environment variable template.
- `requirements.txt` - root convenience wrapper for local setup.

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

## Cloud Run Step 1 Validation

ADK Cloud Run deployment expects the agent directory passed as `AGENT_PATH` to contain:

- `agent.py`
- `root_agent` defined in `agent.py`
- `__init__.py` with `from . import agent`
- `requirements.txt`

Run the local validation script:

```bash
bash scripts/validate_cloud_run_shape.sh spogo_song_agent
```

## Cloud Run Step 2 Runtime Env Lock (Vertex Live Voice)

For production voice mode on Cloud Run, set these environment variables in line with the ADK Gemini Live API guide:

- `GOOGLE_GENAI_USE_VERTEXAI=TRUE`
- `GOOGLE_CLOUD_PROJECT=<your_project_id>`
- `GOOGLE_CLOUD_LOCATION=<your_region>`
- `AGENT_MODEL=gemini-live-2.5-flash-native-audio`

Set deployment shell variables:

```bash
export GOOGLE_CLOUD_PROJECT="your-project-id"
export GOOGLE_CLOUD_LOCATION="us-central1"
export AGENT_MODEL="gemini-live-2.5-flash-native-audio"
```

When validation passes, this deploy shape is ready for `adk deploy cloud_run`:

```bash
./.venv/bin/adk deploy cloud_run \
   --project="$GOOGLE_CLOUD_PROJECT" \
   --region="$GOOGLE_CLOUD_LOCATION" \
   --with_ui \
   spogo_song_agent
```

After deploy, lock the runtime env on the Cloud Run service (service name is shown in deploy output):

```bash
bash scripts/configure_cloud_run_voice_env.sh \
   <cloud_run_service_name> \
   "$GOOGLE_CLOUD_PROJECT" \
   "$GOOGLE_CLOUD_LOCATION" \
   "$AGENT_MODEL"
```

This ensures the running container uses Vertex Live API and an audio-capable Vertex model for mobile voice sessions.

## Cloud Run Step 3 Secret Manager + IAM (Model/Auth Material)

For production runtime security, store sensitive values in Secret Manager and bind access to the Cloud Run runtime service account.

This step prepares:

- `GOOGLE_API_KEY` / `GEMINI_API_KEY` secret binding for list-generation fallback in `generate_song_list`.
- `SPOGO_AUTH_BLOB` secret binding for spogo auth material (consumed in Step 4 startup/auth wiring).
- IAM role `roles/aiplatform.user` on the Cloud Run runtime service account for Vertex model access.
- IAM role `roles/secretmanager.secretAccessor` on the two secrets for the runtime service account.

Create local source files outside git:

```bash
printf "%s" "$GOOGLE_API_KEY" > /tmp/google_api_key.txt

# Export this from your already-authenticated spogo setup.
# The blob must include sp_dc (required). sp_t is strongly recommended for connect playback reliability.
# Example source from a local spogo profile:
cp /path/to/exported/spogo_auth_blob.json /tmp/spogo_auth_blob.json
```

Run Step 3 setup:

```bash
bash scripts/configure_cloud_run_secrets_and_iam.sh \
   <cloud_run_service_name> \
   "$GOOGLE_CLOUD_PROJECT" \
   "$GOOGLE_CLOUD_LOCATION" \
   /tmp/google_api_key.txt \
   /tmp/spogo_auth_blob.json
```

If the Cloud Run service does not exist yet, the script still creates/updates secrets and IAM bindings, then prints the exact `gcloud run services update --update-secrets ...` command to run after deploy.

Optional arguments:

- `google_api_key_secret_name` (default: `spogo-google-api-key`)
- `spogo_auth_secret_name` (default: `spogo-spotify-auth`)
- `service_account_email` override (otherwise auto-resolved from Cloud Run service)

Verification checks:

```bash
gcloud run services describe <cloud_run_service_name> \
   --project="$GOOGLE_CLOUD_PROJECT" \
   --region="$GOOGLE_CLOUD_LOCATION" \
   --format='flattened(spec.template.spec.containers[0].env)' \
   | grep -E 'GOOGLE_API_KEY|GEMINI_API_KEY|SPOGO_AUTH_BLOB'
```

```bash
gcloud run services describe <cloud_run_service_name> \
   --project="$GOOGLE_CLOUD_PROJECT" \
   --region="$GOOGLE_CLOUD_LOCATION" \
   --format='value(spec.template.spec.serviceAccountName)'
```

This aligns with the ADK Live API guidance that production workloads on Vertex use Google Cloud credentials while sensitive app/runtime values are injected securely at deployment time.

## Cloud Run Step 4 Spogo Runtime Bootstrap (CLI + Startup Auth)

Step 4 ensures the Cloud Run runtime can run Spotify tools without assuming a preinstalled spogo binary in the base container.

What this step enables in runtime:

- Auto-install `spogo` when it is missing in `PATH`.
- Parse `SPOGO_AUTH_BLOB` and write a runtime `spogo` config + cookie store.
- Verify auth on startup before playback tools run.

Apply Step 4 runtime env config:

```bash
bash scripts/configure_cloud_run_spogo_runtime.sh \
   <cloud_run_service_name> \
   "$GOOGLE_CLOUD_PROJECT" \
   "$GOOGLE_CLOUD_LOCATION"
```

Optional args:

- `spogo_version` (default: `0.2.0`)
- `runtime_dir` (default: `/tmp/spogo-runtime`)
- `require_device_cookie` (`TRUE` or `FALSE`, default: `FALSE`)

Verification checks:

```bash
gcloud run services describe <cloud_run_service_name> \
   --project="$GOOGLE_CLOUD_PROJECT" \
   --region="$GOOGLE_CLOUD_LOCATION" \
   --format='flattened(spec.template.spec.containers[0].env)' \
   | grep -E 'SPOGO_AUTO_INSTALL|SPOGO_VERSION|SPOGO_RUNTIME_DIR|SPOGO_REQUIRE_AUTH_BLOB|SPOGO_VERIFY_AUTH_ON_STARTUP|SPOGO_REQUIRE_DEVICE_COOKIE|SPOGO_ENGINE|SPOGO_PROFILE'
```

```bash
gcloud run services logs read <cloud_run_service_name> \
   --project="$GOOGLE_CLOUD_PROJECT" \
   --region="$GOOGLE_CLOUD_LOCATION" \
   --limit=200 \
   | grep -Ei 'spogo|bootstrap|auth|cookie'
```

Accepted `SPOGO_AUTH_BLOB` formats for startup bootstrap:

- JSON object with `cookies` array (`{"cookies": [{"name": "sp_dc", ...}]}`)
- JSON cookie array (`[{"name": "sp_dc", ...}]`)
- Key/value text (`sp_dc=...`, `sp_key=...`, `sp_t=...`)

If Step 4 cannot reliably initialize spogo in runtime, proceed to plan Step 6 and switch to a custom Cloud Run container install path.

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
- After capturing the quiz category, acknowledge and reiterate that category once before quiz setup starts.
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

- "Starting a song quiz for '90s Bollywood romance'. I will select and queue 5 songs for this category now."
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
- If you want microphone voice requests in ADK Web, set `AGENT_MODEL` to a Live API-capable model in `.env` (`gemini-2.5-flash-native-audio-preview-12-2025` for local Gemini API, `gemini-live-2.5-flash-native-audio` for Vertex).
