# ADK Gemini Live API Toolkit Developer Guide Summary

## Part 1: Introduction to ADK Gemini Live API Toolkit

### **Key Concepts**

- **Bidi-streaming:** Real-time, bidirectional communication allowing simultaneous "speak and listen" interactions and interruptions.
- **Live API Platforms:** Supports both **Gemini Live API** (Google AI Studio) for prototyping and **Vertex AI Live API** (Google Cloud) for production.
- **ADK Architecture:** Uses a **Runner** to orchestrate the **Agent**, **SessionService** for persistence, and **LiveRequestQueue** for upstream communication.

### **Technical Requirements**

- **Async Context:** Mandatory use of `asyncio` for the event loop.
- **Authentication:** API Key for Gemini or Google Cloud credentials for Vertex AI.
- **Audio Specs:** 16-bit PCM at 16kHz (mono) for input.

### **Core Implementation Steps**

1.  **Initialize:** Define the Agent (tools/instructions), SessionService (InMemory or Database), and Runner.
2.  **Session Setup:** Get or create an ADK Session and initialize `RunConfig` and `LiveRequestQueue`.
3.  **Execution:** Start the `run_live()` event loop.
4.  **Concurrency:** Run an `upstream_task` (sending messages) and `downstream_task` (processing events) concurrently using `asyncio.gather`.

---

## Part 2: Sending Messages with LiveRequestQueue

### **Key Concepts**

- **Unified Interface:** `LiveRequestQueue` handles all message types (text, audio, video, control signals) through a single queue.
- **Message Types:** `send_content()` for discrete text turns; `send_realtime()` for continuous binary streams (audio/blobs).
- **Activity Signals:** `ActivityStart` and `ActivityEnd` for manual turn control (e.g., push-to-talk).

### **Technical Requirements**

- **Thread Safety:** Safe for concurrent access within the same event loop.
- **Ordering:** Guaranteed FIFO (First-In-First-Out) delivery.
- **Resource Management:** Must call `close()` to prevent "zombie" sessions and quota exhaustion.

### **Core Implementation Steps**

1.  **Create Queue:** Instantiate `LiveRequestQueue` within an async context.
2.  **Upstream Flow:** Implement a loop to receive client input and forward it to the queue using `send_content` or `send_realtime`.
3.  **Termination:** Explicitly call `queue.close()` in a `finally` block to signal the end of the stream.

---

## Part 3: Event Handling with run_live()

### **Key Concepts**

- **Async Generator:** `run_live()` yields `Event` objects in real-time (text, audio, tool calls, errors).
- **Event Flags:** `partial` (streaming chunks), `turn_complete` (end of response), and `interrupted` (user cut off the AI).
- **Automatic Tool Execution:** ADK automatically handles the function calling loop, executing tools and sending responses back to the model.

### **Technical Requirements**

- **Pydantic Serialization:** Use `model_dump_json()` for sending events to web/mobile clients.
- **Error Handling:** Distinguish between retryable (e.g., `UNAVAILABLE`) and terminal (e.g., `SAFETY`) errors.

### **Core Implementation Steps**

1.  **Iterate:** Use `async for event in runner.run_live(...)` to consume the stream.
2.  **Process Flags:** Update UI based on `partial` and `interrupted` flags to ensure a responsive experience.
3.  **Metadata:** Monitor `usage_metadata` for token consumption and cost tracking.

---

## Part 4: Understanding RunConfig

### **Key Concepts**

- **Response Modalities:** Choose between `["TEXT"]` or `["AUDIO"]` (only one per session).
- **Streaming Modes:** `BIDI` (WebSocket, bidirectional) vs. `SSE` (HTTP, request-response).
- **Session Resumption:** Automatically reconnects and preserves context after the ~10-minute WebSocket timeout.
- **Context Window Compression:** Summarizes old history to enable "unlimited" session duration and manage token limits.

### **Technical Requirements**

- **Quotas:** Manage concurrent session limits (e.g., 50–1,000 depending on tier).
- **Compression Thresholds:** Configure `trigger_tokens` and `target_tokens` (typically 70-80% of context window).

### **Core Implementation Steps**

1.  **Configure:** Set `streaming_mode=StreamingMode.BIDI` for real-time features.
2.  **Enable Resumption:** Set `session_resumption=types.SessionResumptionConfig()`.
3.  **Manage Quotas:** Implement architectural patterns like "Session Pooling with Queueing" for high-traffic apps.

---

## Part 5: Audio, Images, and Video

### **Key Concepts**

- **Model Architectures:** **Native Audio** (end-to-end audio, natural prosody) vs. **Half-Cascade** (text-to-speech hybrid, robust tool use).
- **Multimodal Input:** Images and video are sent as JPEG frames (recommended 1 FPS at 768x768).
- **Voice Configuration:** Customize voices (e.g., "Puck", "Kore") and languages via `SpeechConfig`.
- **VAD:** Server-side Voice Activity Detection is enabled by default but can be moved to the client for better control.

### **Technical Requirements**

- **Output Audio:** 16-bit PCM at 24kHz (mono) for native audio models.
- **Client-side Processing:** Use Web Audio API (`AudioWorklet`) for low-latency capture and playback.

### **Core Implementation Steps**

1.  **Audio Capture:** Convert Float32 microphone data to 16-bit PCM on the client.
2.  **Playback:** Use a ring buffer in the browser to handle network jitter for smooth audio output.
3.  **Transcription:** Enable `input_audio_transcription` in `RunConfig` for real-time captions.
4.  **Visuals:** Capture webcam frames to a canvas, convert to JPEG, and stream via `send_realtime`.

---

## Official Documentation Links

Reference the full ADK Gemini Live API documentation for detailed guides:

- **Part 1:** https://google.github.io/adk-docs/streaming/dev-guide/part1/
- **Part 2:** https://google.github.io/adk-docs/streaming/dev-guide/part2/
- **Part 3:** https://google.github.io/adk-docs/streaming/dev-guide/part3/
- **Part 4:** https://google.github.io/adk-docs/streaming/dev-guide/part4/
- **Part 5:** https://google.github.io/adk-docs/streaming/dev-guide/part5/
