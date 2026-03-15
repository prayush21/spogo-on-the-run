"""ADK agent that plays the best Spotify search match via spogo."""

from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
import json
import os
import re
import shutil
import subprocess
from typing import Any, TypedDict

from google.adk.agents import Agent
from google.adk.tools import ToolContext

try:
    from google import genai
except ImportError:
    genai = None

_SPOGO_BIN = shutil.which("spogo") or "spogo"
_CONNECT_ENGINE = "--engine=connect"
_DEFAULT_SONG_LIST_COUNT = 5
_MAX_SONG_LIST_COUNT = 20
_TEXT_MODEL_FALLBACK = "gemini-2.5-flash"
_SONG_QUIZ_STATE_KEY = "song_quiz_state"
_SONG_QUIZ_STATE_SCHEMA_VERSION = 1
_SONG_QUIZ_ROUND_COUNT = 5
_SONG_QUIZ_MAX_ATTEMPTS = 2


class SongQuizRoundState(TypedDict):
    """Serializable details for one quiz round."""

    round_number: int
    requested_song: dict[str, Any]
    selected_track: dict[str, Any]


class SongQuizRoundOutcomeState(TypedDict):
    """Serializable outcome details for one completed quiz round."""

    round_number: int
    guesses: list[str]
    attempts_used: int
    is_correct: bool
    correct_song_name: str


class SongQuizState(TypedDict):
    """Serializable session-scoped state for Spotify quiz mode."""

    schema_version: int
    active: bool
    category_request: str
    rounds: list[SongQuizRoundState]
    current_round_index: int
    attempts_used: int
    current_round_guesses: list[str]
    score: int
    round_outcomes: list[SongQuizRoundOutcomeState]


def _is_json_serializable(value: Any) -> bool:
    """Check whether a value can be safely persisted in session state."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_serializable(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_serializable(item)
            for key, item in value.items()
        )
    return False


def _new_song_quiz_state() -> SongQuizState:
    """Create the default quiz state for a session."""
    return {
        "schema_version": _SONG_QUIZ_STATE_SCHEMA_VERSION,
        "active": False,
        "category_request": "",
        "rounds": [],
        "current_round_index": 0,
        "attempts_used": 0,
        "current_round_guesses": [],
        "score": 0,
        "round_outcomes": [],
    }


def _validate_song_quiz_state(raw_state: Any) -> tuple[bool, SongQuizState, str]:
    """Validate quiz state structure loaded from ADK session state."""
    default_state = _new_song_quiz_state()

    if raw_state is None:
        return True, default_state, ""

    if not isinstance(raw_state, dict):
        return False, default_state, "Quiz state must be a JSON object."

    schema_version = raw_state.get(
        "schema_version", _SONG_QUIZ_STATE_SCHEMA_VERSION
    )
    if not isinstance(schema_version, int):
        return False, default_state, "Quiz state schema_version must be an integer."
    if schema_version != _SONG_QUIZ_STATE_SCHEMA_VERSION:
        return (
            False,
            default_state,
            (
                "Quiz state schema version mismatch: "
                f"expected {_SONG_QUIZ_STATE_SCHEMA_VERSION}, got {schema_version}."
            ),
        )

    active = raw_state.get("active")
    category_request = raw_state.get("category_request")
    rounds = raw_state.get("rounds")
    current_round_index = raw_state.get("current_round_index")
    attempts_used = raw_state.get("attempts_used")
    current_round_guesses = raw_state.get("current_round_guesses", [])
    score = raw_state.get("score")
    round_outcomes = raw_state.get("round_outcomes")

    if not isinstance(active, bool):
        return False, default_state, "Quiz state active must be a boolean."
    if not isinstance(category_request, str):
        return False, default_state, "Quiz state category_request must be a string."
    if not isinstance(rounds, list):
        return False, default_state, "Quiz state rounds must be a list."
    if not isinstance(current_round_index, int) or current_round_index < 0:
        return (
            False,
            default_state,
            "Quiz state current_round_index must be a non-negative integer.",
        )
    if not isinstance(attempts_used, int) or attempts_used < 0:
        return (
            False,
            default_state,
            "Quiz state attempts_used must be a non-negative integer.",
        )
    if not isinstance(current_round_guesses, list):
        return (
            False,
            default_state,
            "Quiz state current_round_guesses must be a list.",
        )
    if not all(isinstance(guess, str) for guess in current_round_guesses):
        return (
            False,
            default_state,
            "Quiz state current_round_guesses must contain only strings.",
        )
    if not isinstance(score, int) or score < 0:
        return False, default_state, "Quiz state score must be a non-negative integer."
    if not isinstance(round_outcomes, list):
        return False, default_state, "Quiz state round_outcomes must be a list."
    if current_round_index > len(rounds):
        return (
            False,
            default_state,
            "Quiz state current_round_index cannot exceed total rounds.",
        )

    for index, round_item in enumerate(rounds):
        if not isinstance(round_item, dict):
            return (
                False,
                default_state,
                f"Quiz round at index {index} must be a JSON object.",
            )
        if not _is_json_serializable(round_item):
            return (
                False,
                default_state,
                f"Quiz round at index {index} is not JSON-serializable.",
            )

    for index, outcome_item in enumerate(round_outcomes):
        if not isinstance(outcome_item, dict):
            return (
                False,
                default_state,
                f"Quiz round outcome at index {index} must be a JSON object.",
            )
        if not _is_json_serializable(outcome_item):
            return (
                False,
                default_state,
                f"Quiz round outcome at index {index} is not JSON-serializable.",
            )

    normalized_state: SongQuizState = {
        "schema_version": schema_version,
        "active": active,
        "category_request": category_request.strip(),
        "rounds": deepcopy(rounds),
        "current_round_index": current_round_index,
        "attempts_used": attempts_used,
        "current_round_guesses": deepcopy(current_round_guesses),
        "score": score,
        "round_outcomes": deepcopy(round_outcomes),
    }
    return True, normalized_state, ""


def _load_song_quiz_state(tool_context: ToolContext | None) -> SongQuizState:
    """Load quiz state from ADK session state for the current session."""
    if tool_context is None:
        return _new_song_quiz_state()

    raw_state = tool_context.state.get(_SONG_QUIZ_STATE_KEY)
    is_valid, normalized_state, _ = _validate_song_quiz_state(raw_state)

    if not is_valid:
        # Self-heal corrupted state to keep future tool calls deterministic.
        tool_context.state[_SONG_QUIZ_STATE_KEY] = normalized_state

    return deepcopy(normalized_state)


def _save_song_quiz_state(
    tool_context: ToolContext | None, quiz_state: Any
) -> SongQuizState:
    """Validate and persist quiz state in ADK session state."""
    if tool_context is None:
        raise ValueError("tool_context is required to save quiz state.")

    is_valid, normalized_state, error_message = _validate_song_quiz_state(quiz_state)
    if not is_valid:
        raise ValueError(f"Invalid song quiz state: {error_message}")

    tool_context.state[_SONG_QUIZ_STATE_KEY] = normalized_state
    return deepcopy(normalized_state)


def _clear_song_quiz_state(tool_context: ToolContext | None) -> SongQuizState:
    """Reset quiz state for the current ADK session."""
    if tool_context is None:
        raise ValueError("tool_context is required to clear quiz state.")

    cleared_state = _new_song_quiz_state()
    tool_context.state[_SONG_QUIZ_STATE_KEY] = cleared_state
    return deepcopy(cleared_state)


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


def _evaluate_song_name_guess(guess: str, correct_song_name: str) -> dict[str, Any]:
    """Evaluate whether a song guess should be treated as correct."""
    normalized_guess = _normalize_match_text(guess)
    normalized_song_name = _normalize_match_text(correct_song_name)
    similarity = _string_similarity(guess, correct_song_name)

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
        "is_correct": is_correct,
        "similarity_score": round(similarity, 4),
    }


def _resolve_best_track_match(
    song_name: str, album_name: str = "", search_limit: int = 5
) -> dict[str, Any]:
    """Search and pick the best Spotify track without queueing it."""
    requested_song = (song_name or "").strip()
    requested_album = (album_name or "").strip()
    if not requested_song:
        return {
            "status": "error",
            "error_message": "Song request is empty.",
        }

    search_query = f"{requested_song} {requested_album}".strip()
    search_result = _search_tracks(search_query, limit=search_limit)
    if search_result["status"] != "success":
        return {
            "status": "error",
            "error_message": search_result.get("error_message", "Search failed."),
            "query": search_query,
            "search_query": search_query,
        }

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

    return {
        "status": "success",
        "song_name": requested_song,
        "album_name": requested_album,
        "search_query": search_query,
        "selected_track": selected_track,
        "match_scores": selection["match_scores"],
        "candidate_count": len(candidates),
        "candidates": candidates,
        "search_total_results": search_result["total_results"],
    }


def _get_current_song_quiz_round(
    quiz_state: SongQuizState,
) -> SongQuizRoundState | None:
    """Return the active round from quiz state if available."""
    round_index = quiz_state["current_round_index"]
    rounds = quiz_state["rounds"]
    if round_index < 0 or round_index >= len(rounds):
        return None
    return rounds[round_index]


def _build_song_quiz_round_progress(quiz_state: SongQuizState) -> dict[str, Any]:
    """Build non-spoiler round progress details for model responses."""
    total_rounds = len(quiz_state["rounds"])
    return {
        "round_number": quiz_state["current_round_index"] + 1 if total_rounds else 0,
        "total_rounds": total_rounds,
        "attempts_used": quiz_state["attempts_used"],
        "attempts_remaining": max(
            0,
            _SONG_QUIZ_MAX_ATTEMPTS - quiz_state["attempts_used"],
        ),
        "score": quiz_state["score"],
    }


def _build_song_quiz_score_summary(quiz_state: SongQuizState) -> dict[str, Any]:
    """Build final score payload for completed/cancelled quiz sessions."""
    total_rounds = len(quiz_state["rounds"])
    score = quiz_state["score"]
    return {
        "category_request": quiz_state["category_request"],
        "score": score,
        "total_rounds": total_rounds,
        "score_text": f"{score}/{total_rounds}",
        "correct_rounds": score,
        "wrong_rounds": max(0, total_rounds - score),
        "round_outcomes": deepcopy(quiz_state["round_outcomes"]),
    }


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


def _queue_track_uri(track_uri: str, timeout_seconds: int = 15) -> dict[str, Any]:
    """Add a track URI to the Spotify queue."""
    uri = str(track_uri or "").strip()
    if not uri:
        return {
            "status": "error",
            "error_message": "Track URI is empty.",
        }

    queue_result = _run_spogo(["queue", "add", uri, _CONNECT_ENGINE], timeout_seconds)
    if not queue_result["ok"]:
        return {
            "status": "error",
            "error_message": "Failed to add selected track to queue.",
            "queue_error": queue_result["error"],
        }

    return {
        "status": "success",
        "track_uri": uri,
    }


def _resolve_and_queue_best_match(
    song_name: str, album_name: str = "", search_limit: int = 5
) -> dict[str, Any]:
    """Search, rank, and queue the best Spotify match for a song request."""
    requested_song = (song_name or "").strip()
    requested_album = (album_name or "").strip()
    if not requested_song:
        return {
            "status": "error",
            "error_message": "Song request is empty.",
        }

    search_query = f"{requested_song} {requested_album}".strip()
    search_result = _search_tracks(search_query, limit=search_limit)
    if search_result["status"] != "success":
        return {
            "status": "error",
            "error_message": search_result.get("error_message", "Search failed."),
            "query": search_query,
            "search_query": search_query,
        }

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

    queue_result = _queue_track_uri(uri, timeout_seconds=15)
    if queue_result["status"] != "success":
        return {
            "status": "error",
            "error_message": "Failed to add selected track to queue.",
            "query": search_query,
            "search_query": search_query,
            "selected_track": selected_track,
            "queue_error": queue_result.get("queue_error", "Unknown queue error."),
            "match_scores": selection["match_scores"],
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
        "search_total_results": search_result["total_results"],
    }


def _advance_playback_to_next_queued_track() -> dict[str, Any]:
    """Skip to the next track and return current playback status."""
    next_result = _run_spogo(["next", _CONNECT_ENGINE], timeout_seconds=10)
    if not next_result["ok"]:
        return {
            "status": "error",
            "error_message": "Queue succeeded but skipping to the next track failed.",
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
        "playback_strategy": "queue_then_next",
        "playback_status": playback_status,
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

    queue_result = _queue_track_uri(uri, timeout_seconds=15)
    if queue_result["status"] != "success":
        return {
            "status": "error",
            "error_message": "Failed to add selected track to queue.",
            "selected_track": track,
            "queue_error": queue_result.get("queue_error", queue_result["error_message"]),
        }

    advance_result = _advance_playback_to_next_queued_track()
    if advance_result["status"] != "success":
        return {
            "status": "error",
            "error_message": "Queue succeeded but skipping to the next track failed.",
            "selected_track": track,
            "next_error": advance_result.get("next_error", "Unknown next error."),
        }

    return {
        "status": "success",
        "query": query,
        "selected_track": track,
        "device_gate": device_check["device_gate"],
        "active_devices": device_check["active_devices"],
        "search_total_results": selected["total_results"],
        "playback_strategy": advance_result["playback_strategy"],
        "playback_status": advance_result["playback_status"],
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

    queued_selection = _resolve_and_queue_best_match(
        requested_song, requested_album, search_limit=5
    )
    if queued_selection["status"] != "success":
        return queued_selection

    advance_result = _advance_playback_to_next_queued_track()
    if advance_result["status"] != "success":
        return {
            "status": "error",
            "error_message": "Queue succeeded but skipping to the next track failed.",
            "query": queued_selection["search_query"],
            "selected_track": queued_selection["selected_track"],
            "next_error": advance_result.get("next_error", "Unknown next error."),
            "match_scores": queued_selection["match_scores"],
        }

    return {
        "status": "success",
        "song_name": requested_song,
        "album_name": requested_album,
        "search_query": queued_selection["search_query"],
        "selected_track": queued_selection["selected_track"],
        "match_scores": queued_selection["match_scores"],
        "candidate_count": queued_selection["candidate_count"],
        "candidates": queued_selection["candidates"],
        "device_gate": device_check["device_gate"],
        "active_devices": device_check["active_devices"],
        "search_total_results": queued_selection["search_total_results"],
        "playback_strategy": advance_result["playback_strategy"],
        "playback_status": advance_result["playback_status"],
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

    guess_result = _evaluate_song_name_guess(guessed_name, current_song_name)
    is_correct = guess_result["is_correct"]

    return {
        "status": "success",
        "guess": guessed_name,
        "result": "right" if is_correct else "wrong",
        "is_correct": is_correct,
        "response": "Right guess!" if is_correct else "Wrong guess.",
        "similarity_score": guess_result["similarity_score"],
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

        queued_result = _resolve_and_queue_best_match(
            song_name, album_name, search_limit=5
        )
        if queued_result["status"] != "success":
            failure_error = (
                queued_result.get("queue_error")
                or queued_result.get("error_message")
                or "Search failed."
            )
            if queued_result.get("error_message") in {
                "No playable track could be selected from search results.",
                "Best match is missing a playable track URI.",
            }:
                failure_error = "No playable URI found for selected track."

            failed_payload: dict[str, Any] = {
                "song": {
                    "name": song_name,
                    "album_name": album_name,
                    "year": year,
                },
                "error": failure_error,
            }
            if queued_result.get("search_query"):
                failed_payload["search_query"] = queued_result["search_query"]
            elif queued_result.get("query"):
                failed_payload["search_query"] = queued_result["query"]
            if queued_result.get("selected_track"):
                failed_payload["selected_track"] = queued_result["selected_track"]
            if queued_result.get("match_scores"):
                failed_payload["match_scores"] = queued_result["match_scores"]

            failed_songs.append(failed_payload)
            continue

        queued_songs.append(
            {
                "requested_song": {
                    "name": song_name,
                    "album_name": album_name,
                    "year": year,
                },
                "search_query": queued_result["search_query"],
                "selected_track": queued_result["selected_track"],
                "match_scores": queued_result["match_scores"],
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


def start_song_quiz(
    user_request: str,
    count: int = _SONG_QUIZ_ROUND_COUNT,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Start a 5-round song quiz for a requested category and play round 1."""
    if tool_context is None:
        return {
            "status": "error",
            "error_message": "tool_context is required to start a song quiz.",
        }

    query = (user_request or "").strip()
    if not query:
        return {
            "status": "error",
            "error_message": "User request is empty.",
        }

    current_quiz = _load_song_quiz_state(tool_context)
    if current_quiz["active"]:
        return {
            "status": "error",
            "error_message": "A song quiz is already active. Submit a guess or cancel the current quiz first.",
            "category_request": current_quiz["category_request"],
            "current_round": _build_song_quiz_round_progress(current_quiz),
        }

    try:
        requested_count = int(count)
    except (TypeError, ValueError):
        requested_count = _SONG_QUIZ_ROUND_COUNT

    enforced_count = _SONG_QUIZ_ROUND_COUNT
    device_check = _check_active_device()
    if device_check["status"] != "success":
        return device_check

    generated = generate_song_list(query, enforced_count)
    if isinstance(generated, dict):
        if generated.get("status") == "error":
            return generated
        return {
            "status": "error",
            "error_message": "Song list generation returned an unexpected format.",
            "raw_output": generated,
        }

    songs = generated
    if len(songs) != enforced_count:
        return {
            "status": "error",
            "error_message": "Quiz mode requires exactly 5 songs, but generation returned a different count.",
            "required_rounds": enforced_count,
            "songs_found": len(songs),
            "category_request": query,
        }

    rounds: list[SongQuizRoundState] = []
    failed_rounds: list[dict[str, Any]] = []

    for round_number, song in enumerate(songs, start=1):
        song_name = str(song.get("name") or "").strip()
        album_name = str(song.get("album_name") or "").strip()
        year = song.get("year")

        if not song_name:
            failed_rounds.append(
                {
                    "round_number": round_number,
                    "song": song,
                    "error": "Generated song entry is missing a name.",
                }
            )
            continue

        selected_result = _resolve_best_track_match(song_name, album_name, search_limit=5)
        if selected_result["status"] != "success":
            failed_rounds.append(
                {
                    "round_number": round_number,
                    "song": {
                        "name": song_name,
                        "album_name": album_name,
                        "year": year,
                    },
                    "error": selected_result.get("error_message", "Search failed."),
                    "search_query": selected_result.get("search_query"),
                }
            )
            continue

        selected_track = selected_result["selected_track"]
        queue_result = _queue_track_uri(str(selected_track.get("uri") or ""), timeout_seconds=15)
        if queue_result["status"] != "success":
            failed_rounds.append(
                {
                    "round_number": round_number,
                    "song": {
                        "name": song_name,
                        "album_name": album_name,
                        "year": year,
                    },
                    "error": queue_result.get("queue_error", queue_result["error_message"]),
                    "search_query": selected_result.get("search_query"),
                    "selected_track": selected_track,
                }
            )
            continue

        rounds.append(
            {
                "round_number": round_number,
                "requested_song": {
                    "name": song_name,
                    "album_name": album_name,
                    "year": year,
                },
                "selected_track": selected_track,
            }
        )

    if failed_rounds or len(rounds) != enforced_count:
        return {
            "status": "error",
            "error_message": "Quiz setup failed because all 5 songs could not be prepared and queued.",
            "category_request": query,
            "required_rounds": enforced_count,
            "prepared_rounds": len(rounds),
            "failed_rounds": failed_rounds,
            "requested_count": requested_count,
            "enforced_count": enforced_count,
            "device_gate": device_check["device_gate"],
            "active_devices": device_check["active_devices"],
        }

    advance_result = _advance_playback_to_next_queued_track()
    if advance_result["status"] != "success":
        return {
            "status": "error",
            "error_message": "Quiz songs were queued but playback could not be advanced to round 1.",
            "category_request": query,
            "required_rounds": enforced_count,
            "prepared_rounds": len(rounds),
            "next_error": advance_result.get("next_error", "Unknown next error."),
        }

    quiz_state = _new_song_quiz_state()
    quiz_state["active"] = True
    quiz_state["category_request"] = query
    quiz_state["rounds"] = rounds
    quiz_state["current_round_index"] = 0
    quiz_state["attempts_used"] = 0
    quiz_state["current_round_guesses"] = []
    quiz_state["score"] = 0
    quiz_state["round_outcomes"] = []

    saved_state = _save_song_quiz_state(tool_context, quiz_state)
    return {
        "status": "success",
        "quiz_status": "started",
        "category_request": query,
        "requested_count": requested_count,
        "enforced_count": enforced_count,
        "round_count": len(saved_state["rounds"]),
        "current_round": _build_song_quiz_round_progress(saved_state),
        "response": (
            f"Quiz started for '{query}'. Round 1 of {enforced_count} is now playing. "
            f"You have {_SONG_QUIZ_MAX_ATTEMPTS} attempts to guess this song."
        ),
        "device_gate": device_check["device_gate"],
        "active_devices": device_check["active_devices"],
        "playback_strategy": advance_result["playback_strategy"],
        "playback_status": advance_result["playback_status"],
    }


def submit_song_quiz_guess(
    song_guess: str,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Submit a quiz guess with max two attempts per round and auto-advance."""
    if tool_context is None:
        return {
            "status": "error",
            "error_message": "tool_context is required to submit a quiz guess.",
        }

    guessed_name = (song_guess or "").strip()
    if not guessed_name:
        return {
            "status": "error",
            "error_message": "Song guess is empty.",
        }

    quiz_state = _load_song_quiz_state(tool_context)
    if not quiz_state["active"]:
        return {
            "status": "error",
            "error_message": "No active song quiz. Start a new quiz first.",
        }

    current_round = _get_current_song_quiz_round(quiz_state)
    if current_round is None:
        _clear_song_quiz_state(tool_context)
        return {
            "status": "error",
            "error_message": "Quiz state is inconsistent and was reset. Please start a new quiz.",
        }

    selected_track = current_round.get("selected_track")
    if not isinstance(selected_track, dict):
        selected_track = {}

    correct_song_name = str(selected_track.get("name") or "").strip()
    if not correct_song_name:
        requested_song = current_round.get("requested_song")
        if isinstance(requested_song, dict):
            correct_song_name = str(requested_song.get("name") or "").strip()

    if not correct_song_name:
        _clear_song_quiz_state(tool_context)
        return {
            "status": "error",
            "error_message": "Quiz state is missing the correct answer and was reset. Please start a new quiz.",
        }

    guess_result = _evaluate_song_name_guess(guessed_name, correct_song_name)
    is_correct = bool(guess_result["is_correct"])

    round_number = int(
        current_round.get("round_number") or (quiz_state["current_round_index"] + 1)
    )
    total_rounds = len(quiz_state["rounds"])
    next_attempts_used = quiz_state["attempts_used"] + 1
    guesses_for_round = [*quiz_state["current_round_guesses"], guessed_name]

    if not is_correct and next_attempts_used < _SONG_QUIZ_MAX_ATTEMPTS:
        quiz_state["attempts_used"] = next_attempts_used
        quiz_state["current_round_guesses"] = guesses_for_round
        saved_state = _save_song_quiz_state(tool_context, quiz_state)
        return {
            "status": "success",
            "quiz_status": "in_progress",
            "guess": guessed_name,
            "result": "wrong",
            "is_correct": False,
            "similarity_score": guess_result["similarity_score"],
            "response": "Wrong guess. Final attempt left for this song.",
            "current_round": _build_song_quiz_round_progress(saved_state),
        }

    round_outcome: SongQuizRoundOutcomeState = {
        "round_number": round_number,
        "guesses": guesses_for_round,
        "attempts_used": next_attempts_used,
        "is_correct": is_correct,
        "correct_song_name": correct_song_name,
    }
    quiz_state["round_outcomes"].append(round_outcome)
    if is_correct:
        quiz_state["score"] += 1

    round_result = {
        "round_number": round_number,
        "attempts_used": next_attempts_used,
        "is_correct": is_correct,
        "correct_song_name": correct_song_name,
    }

    is_last_round = round_number >= total_rounds
    if is_last_round:
        quiz_state["active"] = False
        quiz_state["current_round_index"] = total_rounds
        quiz_state["attempts_used"] = 0
        quiz_state["current_round_guesses"] = []

        completed_state = _save_song_quiz_state(tool_context, quiz_state)
        summary = _build_song_quiz_score_summary(completed_state)
        _clear_song_quiz_state(tool_context)

        return {
            "status": "success",
            "quiz_status": "completed",
            "guess": guessed_name,
            "result": "right" if is_correct else "wrong",
            "is_correct": is_correct,
            "similarity_score": guess_result["similarity_score"],
            "round_result": round_result,
            "response": (
                f"Quiz complete for '{summary['category_request']}'. "
                f"Final score: {summary['score_text']}."
            ),
            **summary,
        }

    quiz_state["current_round_index"] += 1
    quiz_state["attempts_used"] = 0
    quiz_state["current_round_guesses"] = []
    saved_state = _save_song_quiz_state(tool_context, quiz_state)

    advance_result = _advance_playback_to_next_queued_track()
    response_prefix = "Right guess!" if is_correct else (
        f"Wrong guess. The correct song was '{correct_song_name}'."
    )

    if advance_result["status"] != "success":
        return {
            "status": "partial_success",
            "quiz_status": "in_progress",
            "guess": guessed_name,
            "result": "right" if is_correct else "wrong",
            "is_correct": is_correct,
            "similarity_score": guess_result["similarity_score"],
            "round_result": round_result,
            "score": saved_state["score"],
            "current_round": _build_song_quiz_round_progress(saved_state),
            "response": (
                f"{response_prefix} I moved the quiz to the next round, but playback "
                "could not be auto-advanced."
            ),
            "error_message": "Round was processed, but playback next failed.",
            "next_error": advance_result.get("next_error", "Unknown next error."),
        }

    return {
        "status": "success",
        "quiz_status": "in_progress",
        "guess": guessed_name,
        "result": "right" if is_correct else "wrong",
        "is_correct": is_correct,
        "similarity_score": guess_result["similarity_score"],
        "round_result": round_result,
        "score": saved_state["score"],
        "current_round": _build_song_quiz_round_progress(saved_state),
        "response": f"{response_prefix} Moving to the next song.",
        "playback_strategy": advance_result["playback_strategy"],
        "playback_status": advance_result["playback_status"],
    }


def cancel_song_quiz(tool_context: ToolContext | None = None) -> dict[str, Any]:
    """Cancel and clear the active quiz state for safe recovery."""
    if tool_context is None:
        return {
            "status": "error",
            "error_message": "tool_context is required to cancel a song quiz.",
        }

    quiz_state = _load_song_quiz_state(tool_context)
    if not quiz_state["active"]:
        _clear_song_quiz_state(tool_context)
        return {
            "status": "success",
            "quiz_status": "idle",
            "response": "No active song quiz to cancel.",
        }

    summary = _build_song_quiz_score_summary(quiz_state)
    completed_rounds = len(summary["round_outcomes"])
    _clear_song_quiz_state(tool_context)

    return {
        "status": "success",
        "quiz_status": "cancelled",
        "response": (
            f"Cancelled the active quiz for '{summary['category_request']}'. "
            f"Progress before cancel: {summary['score_text']} after {completed_rounds} completed rounds."
        ),
        "completed_rounds": completed_rounds,
        **summary,
    }


root_agent = Agent(
    name="spogo_song_agent",
    model=os.getenv("AGENT_MODEL", "gemini-2.5-flash-native-audio-latest"),
    description=(
        "Plays requested songs on Spotify, generates structured recommendation lists, can queue generated lists, and supports an explicit 5-round song quiz mode."
    ),
    instruction=(
        "You are a Spotify playback assistant. "
        "When users ask for a song list or recommendations by mood, genre, album, artist, decade, or vibe, call generate_song_list with their request and optional count. "
        "When users ask you to generate songs and add them to Spotify queue, call generate_and_queue_song_list. "
        "For queue-list requests, first tell users how many songs were found, then confirm how many songs were added to queue. "
        "When the user asks to play music, call play_song with the requested song and optional album hint. "
        "Start quiz mode only when the user explicitly asks to start a song quiz/game/challenge. "
        "For explicit quiz-start requests, first acknowledge and reiterate the chosen quiz category once and say you are selecting and queueing songs for that category, then call start_song_quiz with the user category request. "
        "While quiz mode is active, route song-guess attempts to submit_song_quiz_guess and do not use check_current_song_guess for those guesses. "
        "If the user asks to stop, end, quit, or cancel an active quiz, call cancel_song_quiz. "
        "Outside quiz mode, when the user guesses the current song name, call check_current_song_guess with their guess and tell them if they are right or wrong. "
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
        start_song_quiz,
        submit_song_quiz_guess,
        cancel_song_quiz,
    ],
)
