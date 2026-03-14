"""ADK agent that plays the best Spotify search match via spogo."""

from __future__ import annotations

from difflib import SequenceMatcher
import json
import os
import re
import shutil
import subprocess
from typing import Any

from google.adk.agents import Agent

try:
    from google import genai
except ImportError:
    genai = None

_SPOGO_BIN = shutil.which("spogo") or "spogo"
_CONNECT_ENGINE = "--engine=connect"
_DEFAULT_SONG_LIST_COUNT = 5
_MAX_SONG_LIST_COUNT = 20
_TEXT_MODEL_FALLBACK = "gemini-2.5-flash"


def _run_spogo(args: list[str], timeout_seconds: int = 20) -> dict[str, Any]:
    """Run a spogo command and return normalized output."""
    command = [_SPOGO_BIN, *args]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "error": "spogo CLI not found in PATH.",
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"spogo command timed out after {timeout_seconds}s.",
        }

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()

    if completed.returncode != 0:
        error_text = stderr or stdout or f"spogo exited with code {completed.returncode}."
        return {
            "ok": False,
            "error": error_text,
            "returncode": completed.returncode,
        }

    return {
        "ok": True,
        "stdout": stdout,
        "stderr": stderr,
        "returncode": completed.returncode,
    }


def _search_first_track(query: str) -> dict[str, Any]:
    """Search Spotify and return the first track match."""
    search_result = _run_spogo(
        [
            "search",
            "track",
            query,
            _CONNECT_ENGINE,
            "--json",
            "--limit=1",
            "--offset=0",
        ],
        timeout_seconds=20,
    )
    if not search_result["ok"]:
        return {
            "status": "error",
            "error_message": f"Search failed: {search_result['error']}",
        }

    try:
        payload = json.loads(search_result["stdout"] or "{}")
    except json.JSONDecodeError:
        return {
            "status": "error",
            "error_message": "Search command returned invalid JSON.",
            "raw_output": search_result["stdout"],
        }

    items = payload.get("items") or []
    if not items:
        return {
            "status": "error",
            "error_message": f"No songs found for '{query}'.",
        }

    first = items[0]
    track = {
        "id": first.get("id"),
        "uri": first.get("uri"),
        "name": first.get("name"),
        "album": first.get("album"),
        "url": first.get("url"),
    }

    if not track["uri"]:
        return {
            "status": "error",
            "error_message": "Search result is missing a playable track URI.",
            "track": track,
        }

    return {
        "status": "success",
        "query": query,
        "track": track,
        "total_results": payload.get("total", len(items)),
    }


def _search_tracks(query: str, limit: int = 5) -> dict[str, Any]:
    """Search Spotify and return up to `limit` track matches."""
    search_result = _run_spogo(
        [
            "search",
            "track",
            query,
            _CONNECT_ENGINE,
            "--json",
            f"--limit={limit}",
            "--offset=0",
        ],
        timeout_seconds=20,
    )
    if not search_result["ok"]:
        return {
            "status": "error",
            "error_message": f"Search failed: {search_result['error']}",
        }

    try:
        payload = json.loads(search_result["stdout"] or "{}")
    except json.JSONDecodeError:
        return {
            "status": "error",
            "error_message": "Search command returned invalid JSON.",
            "raw_output": search_result["stdout"],
        }

    items = payload.get("items") or []
    if not items:
        return {
            "status": "error",
            "error_message": f"No songs found for '{query}'.",
        }

    tracks: list[dict[str, Any]] = []
    for item in items:
        tracks.append(
            {
                "id": item.get("id"),
                "uri": item.get("uri"),
                "name": item.get("name"),
                "album": item.get("album"),
                "url": item.get("url"),
            }
        )

    return {
        "status": "success",
        "query": query,
        "tracks": tracks,
        "total_results": payload.get("total", len(items)),
    }


def _normalize_match_text(value: str | None) -> str:
    """Normalize strings for robust text similarity checks."""
    normalized = re.sub(r"[^a-z0-9]+", " ", (value or "").lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _string_similarity(left: str | None, right: str | None) -> float:
    """Return a similarity ratio between 0 and 1."""
    left_normalized = _normalize_match_text(left)
    right_normalized = _normalize_match_text(right)
    if not left_normalized or not right_normalized:
        return 0.0
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def _pick_best_track_match(
    tracks: list[dict[str, Any]], song_name: str, album_name: str = ""
) -> dict[str, Any]:
    """Pick the track with the highest weighted name+album match score."""
    requested_song = (song_name or "").strip()
    requested_album = (album_name or "").strip()

    best_track: dict[str, Any] | None = None
    best_total_score = -1.0
    best_name_score = 0.0
    best_album_score = 0.0

    for track in tracks:
        name_score = _string_similarity(requested_song, track.get("name"))

        if requested_album:
            album_score = _string_similarity(requested_album, track.get("album"))
            total_score = (0.7 * name_score) + (0.3 * album_score)
        else:
            album_score = _string_similarity(requested_song, track.get("album"))
            total_score = (0.85 * name_score) + (0.15 * album_score)

        if total_score > best_total_score:
            best_total_score = total_score
            best_name_score = name_score
            best_album_score = album_score
            best_track = track

    return {
        "track": best_track,
        "match_scores": {
            "name_score": round(best_name_score, 4),
            "album_score": round(best_album_score, 4),
            "total_score": round(best_total_score, 4),
        },
    }


def _parse_status_json(stdout: str) -> Any:
    if not stdout:
        return {"status": "empty"}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return stdout


def _pick_text_model_for_song_list() -> str:
    """Select a text-capable model for JSON recommendation generation."""
    configured_model = (os.getenv("AGENT_MODEL") or "").strip()
    if not configured_model:
        return _TEXT_MODEL_FALLBACK
    if "native-audio" in configured_model.lower():
        return _TEXT_MODEL_FALLBACK
    return configured_model


def _extract_json_array_text(raw_text: str) -> str:
    """Extract a JSON array payload from raw model text."""
    stripped = (raw_text or "").strip()
    if not stripped:
        return ""

    code_block = re.search(
        r"```(?:json)?\s*(.*?)\s*```",
        stripped,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if code_block:
        stripped = code_block.group(1).strip()

    start = stripped.find("[")
    end = stripped.rfind("]")
    if start >= 0 and end > start:
        return stripped[start : end + 1].strip()
    return stripped


def _normalize_song_recommendations(raw_items: Any, count: int) -> list[dict[str, Any]]:
    """Normalize model output to required song schema."""
    if not isinstance(raw_items, list):
        return []

    songs: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue

        name = str(item.get("name") or item.get("song") or item.get("title") or "").strip()
        album_name = str(
            item.get("album_name")
            or item.get("albumName")
            or item.get("album")
            or ""
        ).strip()

        year_value = item.get("year")
        if year_value is None:
            year_value = item.get("release_year") or item.get("releaseYear")

        year: int | None = None
        if isinstance(year_value, int):
            year = year_value
        elif isinstance(year_value, str):
            year_digits = re.sub(r"[^0-9]", "", year_value)
            if len(year_digits) >= 4:
                year = int(year_digits[:4])

        if not name or not album_name or year is None:
            continue

        songs.append(
            {
                "name": name,
                "year": year,
                "album_name": album_name,
            }
        )

        if len(songs) >= count:
            break

    return songs


def _parse_device_list_output(stdout: str) -> dict[str, Any]:
    """Parse `spogo device list` output and find active devices."""
    devices = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
    active_devices = [
        line for line in devices if re.search(r"\bactive\b", line, flags=re.IGNORECASE)
    ]

    return {
        "devices": devices,
        "active_devices": active_devices,
        "has_active_device": bool(active_devices),
    }


def _check_active_device() -> dict[str, Any]:
    """Validate there is at least one active Spotify Connect device."""
    device_result = _run_spogo(["device", "list"], timeout_seconds=10)
    if not device_result["ok"]:
        return {
            "status": "error",
            "error_message": "Can't connect to any devices at the moment. Please open Spotify on app and play any song to activate it.",
            "details": device_result["error"],
        }

    parsed = _parse_device_list_output(device_result["stdout"])
    if not parsed["has_active_device"]:
        return {
            "status": "error",
            "error_message": "Can't connect to any devices at the moment. Please open Spotify on app and play any song to activate it.",
            "devices": parsed["devices"],
            "active_devices": parsed["active_devices"],
        }

    return {
        "status": "success",
        "device_gate": "green_flag",
        "devices": parsed["devices"],
        "active_devices": parsed["active_devices"],
    }


def get_spogo_status() -> dict[str, Any]:
    """Check Spotify device connectivity and active device availability.

    Returns:
        dict: status and active device details.
    """
    return _check_active_device()


def play_first_song(song_request: str) -> dict[str, Any]:
    """Search for a song and play the first Spotify result using spogo.

    Args:
        song_request: Song name or phrase the user wants to play.

    Returns:
        dict: status, selected track, and playback result details.
    """
    query = (song_request or "").strip()
    if not query:
        return {
            "status": "error",
            "error_message": "Song request is empty.",
        }

    device_check = _check_active_device()
    if device_check["status"] != "success":
        return device_check

    selected = _search_first_track(query)
    if selected["status"] != "success":
        return selected

    track = selected["track"]
    uri = track["uri"]

    queue_result = _run_spogo(["queue", "add", uri, _CONNECT_ENGINE], timeout_seconds=15)
    if not queue_result["ok"]:
        return {
            "status": "error",
            "error_message": "Failed to add selected track to queue.",
            "selected_track": track,
            "queue_error": queue_result["error"],
        }

    next_result = _run_spogo(["next", _CONNECT_ENGINE], timeout_seconds=10)
    if not next_result["ok"]:
        return {
            "status": "error",
            "error_message": "Queue succeeded but skipping to the next track failed.",
            "selected_track": track,
            "next_error": next_result["error"],
        }

    status_result = _run_spogo(["status", _CONNECT_ENGINE, "--json"], timeout_seconds=10)
    playback_status: Any
    if status_result["ok"]:
        playback_status = _parse_status_json(status_result["stdout"])
    else:
        playback_status = {
            "status": "unavailable",
            "reason": status_result["error"],
        }

    return {
        "status": "success",
        "query": query,
        "selected_track": track,
        "device_gate": device_check["device_gate"],
        "active_devices": device_check["active_devices"],
        "search_total_results": selected["total_results"],
        "playback_strategy": "queue_then_next",
        "playback_status": playback_status,
    }


def play_song(song_name: str, album_name: str = "") -> dict[str, Any]:
    """Search top 5 tracks, select best name+album match, then queue and skip.

    Args:
        song_name: Requested song title or phrase.
        album_name: Optional album hint for improving match selection.

    Returns:
        dict: status, selected track, and playback result details.
    """
    requested_song = (song_name or "").strip()
    requested_album = (album_name or "").strip()
    if not requested_song:
        return {
            "status": "error",
            "error_message": "Song request is empty.",
        }

    device_check = _check_active_device()
    if device_check["status"] != "success":
        return device_check

    search_query = requested_song
    if requested_album:
        search_query = f"{requested_song} {requested_album}"

    search_result = _search_tracks(search_query, limit=5)
    if search_result["status"] != "success":
        return search_result

    candidates = search_result["tracks"]
    selection = _pick_best_track_match(candidates, requested_song, requested_album)
    selected_track = selection["track"]

    if not selected_track:
        return {
            "status": "error",
            "error_message": "No playable track could be selected from search results.",
            "query": search_query,
            "candidates_considered": len(candidates),
        }

    uri = selected_track.get("uri")
    if not uri:
        return {
            "status": "error",
            "error_message": "Best match is missing a playable track URI.",
            "query": search_query,
            "selected_track": selected_track,
            "match_scores": selection["match_scores"],
        }

    queue_result = _run_spogo(["queue", "add", uri, _CONNECT_ENGINE], timeout_seconds=15)
    if not queue_result["ok"]:
        return {
            "status": "error",
            "error_message": "Failed to add selected track to queue.",
            "query": search_query,
            "selected_track": selected_track,
            "queue_error": queue_result["error"],
            "match_scores": selection["match_scores"],
        }

    next_result = _run_spogo(["next", _CONNECT_ENGINE], timeout_seconds=10)
    if not next_result["ok"]:
        return {
            "status": "error",
            "error_message": "Queue succeeded but skipping to the next track failed.",
            "query": search_query,
            "selected_track": selected_track,
            "next_error": next_result["error"],
            "match_scores": selection["match_scores"],
        }

    status_result = _run_spogo(["status", _CONNECT_ENGINE, "--json"], timeout_seconds=10)
    playback_status: Any
    if status_result["ok"]:
        playback_status = _parse_status_json(status_result["stdout"])
    else:
        playback_status = {
            "status": "unavailable",
            "reason": status_result["error"],
        }

    return {
        "status": "success",
        "song_name": requested_song,
        "album_name": requested_album,
        "search_query": search_query,
        "selected_track": selected_track,
        "match_scores": selection["match_scores"],
        "candidate_count": len(candidates),
        "candidates": candidates,
        "device_gate": device_check["device_gate"],
        "active_devices": device_check["active_devices"],
        "search_total_results": search_result["total_results"],
        "playback_strategy": "queue_then_next",
        "playback_status": playback_status,
    }


def check_current_song_guess(song_guess: str) -> dict[str, Any]:
    """Check whether the player's guess matches the currently playing song."""
    guessed_name = (song_guess or "").strip()
    if not guessed_name:
        return {
            "status": "error",
            "error_message": "Song guess is empty.",
        }

    status_result = _run_spogo(["status", _CONNECT_ENGINE, "--json"], timeout_seconds=10)
    if not status_result["ok"]:
        return {
            "status": "error",
            "error_message": "Failed to fetch current playback status.",
            "details": status_result["error"],
        }

    status_payload = _parse_status_json(status_result["stdout"])
    if not isinstance(status_payload, dict):
        return {
            "status": "error",
            "error_message": "Playback status output is not valid JSON.",
            "raw_status": status_payload,
        }

    item = status_payload.get("item") or {}
    if not isinstance(item, dict):
        item = {}

    current_song_name = (item.get("name") or "").strip()
    if not current_song_name:
        return {
            "status": "error",
            "error_message": "No current song found in playback status.",
            "playback_status": status_payload,
        }

    current_album_name = (item.get("album") or "").strip()
    track_ref = (item.get("uri") or item.get("id") or "").strip()

    # Some spogo status payloads don't include album, so pull it from track info.
    if not current_album_name and track_ref:
        track_info_result = _run_spogo(
            ["track", "info", track_ref, _CONNECT_ENGINE, "--json"],
            timeout_seconds=10,
        )
        if track_info_result["ok"]:
            try:
                track_payload = json.loads(track_info_result["stdout"] or "{}")
            except json.JSONDecodeError:
                track_payload = {}
            if isinstance(track_payload, dict):
                current_album_name = (track_payload.get("album") or "").strip()

    normalized_guess = _normalize_match_text(guessed_name)
    normalized_song_name = _normalize_match_text(current_song_name)
    similarity = _string_similarity(guessed_name, current_song_name)

    is_exact = bool(normalized_guess and normalized_guess == normalized_song_name)
    is_contained = bool(
        normalized_guess
        and normalized_song_name
        and (
            normalized_guess in normalized_song_name
            or normalized_song_name in normalized_guess
        )
    )
    is_correct = bool(is_exact or similarity >= 0.9 or (is_contained and similarity >= 0.75))

    return {
        "status": "success",
        "guess": guessed_name,
        "result": "right" if is_correct else "wrong",
        "is_correct": is_correct,
        "response": "Right guess!" if is_correct else "Wrong guess.",
        "similarity_score": round(similarity, 4),
        "current_item": {
            "name": current_song_name,
            "album": current_album_name,
        },
        "is_playing": bool(status_payload.get("is_playing")),
    }


def generate_song_list(
    user_request: str, count: int = _DEFAULT_SONG_LIST_COUNT
) -> list[dict[str, Any]] | dict[str, Any]:
    """Generate a structured song list from a free-form user request.

    Args:
        user_request: Album, mood, genre, artist, or any music preference prompt.
        count: Number of songs to return. Defaults to 5.

    Returns:
        list[dict[str, Any]] on success, otherwise an error dict.
    """
    query = (user_request or "").strip()
    if not query:
        return {
            "status": "error",
            "error_message": "User request is empty.",
        }

    try:
        requested_count = int(count)
    except (TypeError, ValueError):
        requested_count = _DEFAULT_SONG_LIST_COUNT
    requested_count = max(1, min(requested_count, _MAX_SONG_LIST_COUNT))

    if genai is None:
        return {
            "status": "error",
            "error_message": "google-genai is not available. Install dependencies from requirements.txt.",
        }

    api_key = (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return {
            "status": "error",
            "error_message": "Missing GOOGLE_API_KEY (or GEMINI_API_KEY) for recommendation generation.",
        }

    model_name = _pick_text_model_for_song_list()
    prompt = (
        "You are a music recommendation assistant.\n"
        f"User request: {query}\n"
        f"Return exactly {requested_count} songs.\n"
        "Output must be ONLY a JSON array (no markdown, no explanation).\n"
        "Each item must include keys: name (string), year (integer), album_name (string).\n"
        "The request can be mood, genre, album, artist, decade, language, or vibe.\n"
        "Avoid duplicate songs."
    )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config={
                "temperature": 0.4,
                "response_mime_type": "application/json",
            },
        )
    except Exception as error:  # pragma: no cover - depends on remote API
        return {
            "status": "error",
            "error_message": "Song list generation failed.",
            "details": str(error),
        }

    try:
        raw_text = (response.text or "").strip()
    except Exception:
        raw_text = ""

    json_payload = _extract_json_array_text(raw_text)
    try:
        raw_items = json.loads(json_payload)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "error_message": "Model did not return valid JSON array output.",
            "model_used": model_name,
            "raw_output": raw_text,
        }

    songs = _normalize_song_recommendations(raw_items, requested_count)
    if not songs:
        return {
            "status": "error",
            "error_message": "Model output was JSON but missing required song fields.",
            "model_used": model_name,
            "raw_output": raw_items,
        }

    return songs


def generate_and_queue_song_list(
    user_request: str, count: int = _DEFAULT_SONG_LIST_COUNT
) -> dict[str, Any]:
    """Generate a song list and add each selected match to Spotify queue.

    Args:
        user_request: Album, mood, genre, artist, or any music preference prompt.
        count: Number of songs to generate and queue.

    Returns:
        dict: generation and queueing status details.
    """
    query = (user_request or "").strip()
    if not query:
        return {
            "status": "error",
            "error_message": "User request is empty.",
        }

    device_check = _check_active_device()
    if device_check["status"] != "success":
        return device_check

    generated = generate_song_list(query, count)
    if isinstance(generated, dict):
        if generated.get("status") == "error":
            return generated
        return {
            "status": "error",
            "error_message": "Song list generation returned an unexpected format.",
            "raw_output": generated,
        }

    songs = generated
    songs_found = len(songs)
    if songs_found == 0:
        return {
            "status": "error",
            "error_message": "No songs found for queueing.",
            "user_request": query,
        }

    queued_songs: list[dict[str, Any]] = []
    failed_songs: list[dict[str, Any]] = []

    for song in songs:
        song_name = str(song.get("name") or "").strip()
        album_name = str(song.get("album_name") or "").strip()
        year = song.get("year")

        if not song_name:
            failed_songs.append(
                {
                    "song": song,
                    "error": "Song entry is missing name.",
                }
            )
            continue

        search_query = f"{song_name} {album_name}".strip()
        search_result = _search_tracks(search_query, limit=5)
        if search_result["status"] != "success":
            failed_songs.append(
                {
                    "song": {
                        "name": song_name,
                        "album_name": album_name,
                        "year": year,
                    },
                    "search_query": search_query,
                    "error": search_result.get("error_message", "Search failed."),
                }
            )
            continue

        candidates = search_result["tracks"]
        selection = _pick_best_track_match(candidates, song_name, album_name)
        selected_track = selection["track"]

        if not selected_track or not selected_track.get("uri"):
            failed_songs.append(
                {
                    "song": {
                        "name": song_name,
                        "album_name": album_name,
                        "year": year,
                    },
                    "search_query": search_query,
                    "error": "No playable URI found for selected track.",
                    "match_scores": selection["match_scores"],
                }
            )
            continue

        queue_result = _run_spogo(
            ["queue", "add", selected_track["uri"], _CONNECT_ENGINE],
            timeout_seconds=15,
        )
        if not queue_result["ok"]:
            failed_songs.append(
                {
                    "song": {
                        "name": song_name,
                        "album_name": album_name,
                        "year": year,
                    },
                    "search_query": search_query,
                    "selected_track": selected_track,
                    "error": queue_result["error"],
                    "match_scores": selection["match_scores"],
                }
            )
            continue

        queued_songs.append(
            {
                "requested_song": {
                    "name": song_name,
                    "album_name": album_name,
                    "year": year,
                },
                "search_query": search_query,
                "selected_track": selected_track,
                "match_scores": selection["match_scores"],
            }
        )

    queued_count = len(queued_songs)
    failed_count = len(failed_songs)

    if queued_count == 0:
        return {
            "status": "error",
            "error_message": "Song list was generated, but no songs could be queued.",
            "user_request": query,
            "songs_found": songs_found,
            "announcement_before_queueing": f"I found {songs_found} songs. Queueing them now.",
            "announcement_after_queueing": "I could not add any songs to your Spotify queue.",
            "queued_count": queued_count,
            "failed_count": failed_count,
            "generated_songs": songs,
            "queued_songs": queued_songs,
            "failed_songs": failed_songs,
            "device_gate": device_check["device_gate"],
            "active_devices": device_check["active_devices"],
        }

    status_value = "success" if failed_count == 0 else "partial_success"
    return {
        "status": status_value,
        "user_request": query,
        "songs_found": songs_found,
        "announcement_before_queueing": f"I found {songs_found} songs. Queueing them now.",
        "announcement_after_queueing": (
            f"Added {queued_count} of {songs_found} songs to your Spotify queue."
        ),
        "queued_count": queued_count,
        "failed_count": failed_count,
        "generated_songs": songs,
        "queued_songs": queued_songs,
        "failed_songs": failed_songs,
        "device_gate": device_check["device_gate"],
        "active_devices": device_check["active_devices"],
    }


root_agent = Agent(
    name="spogo_song_agent",
    model=os.getenv("AGENT_MODEL", "gemini-2.5-flash-native-audio-latest"),
    description=(
        "Plays requested songs on Spotify, generates structured recommendation lists, and can queue generated lists."
    ),
    instruction=(
        "You are a Spotify playback assistant. "
        "When users ask for a song list or recommendations by mood, genre, album, artist, decade, or vibe, call generate_song_list with their request and optional count. "
        "When users ask you to generate songs and add them to Spotify queue, call generate_and_queue_song_list. "
        "For queue-list requests, first tell users how many songs were found, then confirm how many songs were added to queue. "
        "When the user asks to play music, call play_song with the requested song and optional album hint. "
        "When the user guesses the current song name, call check_current_song_guess with their guess and tell them if they are right or wrong. "
        "Never claim playback worked unless the tool returns status=success. "
        "If playback fails due to no active device, tell the user to open Spotify app and play any song to activate a device. "
        "Use get_spogo_status when users ask about Spotify device readiness or connectivity. "
        "For recommendation output, present the returned songs clearly with name, year, and album name."
    ),
    tools=[
        play_song,
        get_spogo_status,
        check_current_song_guess,
        generate_song_list,
        generate_and_queue_song_list,
    ],
)
