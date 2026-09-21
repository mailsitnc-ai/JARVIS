---
name: jarvis-camera-vision
description: "JARVIS webcam capture + screen/browser observation, command-gated, with vision descriptions"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5235d300-6e27-428c-867c-70c44be16e13
  modified: 2026-09-18T06:31:22.049Z
---

Added 2026-09-16 (user: "camera sensing that turns on only on my command, and browser observation"). Uses opencv-python-headless (installed --user, 43MB).

**Camera**: new capability `camera` in `core/permissions.py`, added to `SENSITIVE` set so `state()` does NOT return "allow" under autonomy (privacy: the webcam never fires from a background/agenda task, only an explicit approved command). Broker `ActionBroker.capture_camera(path)` (gated `camera`): lazily imports cv2 (auto-installs opencv-python-headless if missing), `cv2.VideoCapture(0, CAP_DSHOW)`, warms 6 frames, `cv2.imwrite`, remembers as last_written. Skill `skills/camera.py` ("take a photo/selfie", "webcam", "what do you see", "look through the camera"): captures; if the request implies describing (`_DESCRIBE`), also runs vision.

**Screen/browser observation**: skill `skills/observe_screen.py` ("what's on my screen/browser", "read my browser", "what am I looking at") -> `actions.screenshot()` (gated `screen`) then vision-describes it. (An evolved `observe_screen_2` was deleted 2026-09-17 - it treated the "Screenshot saved to ..." message as a path and always failed.)

**Webcam video** (added 2026-09-17, commit `434368f`): `ActionBroker.record_video(path, seconds)` is gated by `camera` too (same privacy rule - never from autonomy/agenda), calibrates webcam fps, honours the duration + save location, and records into the conversation focus + last_written so "open it"/"open the video you just recorded" works. Skill `skills/record_video.py` parses duration ("10 second"/"5s") and destination ("on my desktop", "to C:\...\clip.mp4"), declines "video game". Earlier evolved record_video was broken (fixed temp path, wrong duration, bypassed the gate). See [[jarvis-conversation-context]].

**Hand-gesture control** (2026-09-18, commit `23d74c5`): `core/gestures.py` `GestureController` - OpenCV skin-mask + convex-hull-defect finger counter (NO mediapipe: not installed, won't run on this no-AVX2 CPU), watches a webcam ROI, fires a mapped command on a stable finger count (DEFAULT_MAP 1=take a screenshot, 2=what time is it, 3=what's on my screen; 5/open palm=stop, 0=idle) with a cooldown + live preview window (cv2.imshow in a worker thread). `ActionBroker.start_gesture_control(runner, emit)` gated SENSITIVE `camera` (never autonomy); `stop_gesture_control()`. Skill `skills/hand_control.py` ("watch my hands"/"enable hand control"/"stop watching my hands"); fired commands are non-webcam so they don't fight the loop for the device. Fingers fire via `context['run']` (subrun -> existing skills). Recognition is basic/lighting-dependent (MVP). If the cv2 window misbehaves in the pythonw daemon thread, move it to a subprocess.

**Identify what I'm holding/doing**: camera skill triggers extended to "what am I holding/doing/wearing", "identify what I'm holding" -> capture + vision-describe.

**Vision**: `core/vision.py` `describe_image(path, question)` / `available()` posts the image inline (base64 data URI) to Gemini's OpenAI-compat endpoint (`llm.gemini` base_url + model + key). Returns text, or None (no gemini key), or "(...error...)" which the skills treat as failure (fall back to "saved but couldn't describe"). Doctor autonomy view shows SENSITIVE caps as "ask (still gated - privacy)".

VISION WORKING 2026-09-18 (commit `2c4174c`): user set a valid AI Studio key (note: a valid key can look like `AQ.A...`/53 chars, NOT only `AIza...` - don't reject on prefix). `gemini-flash-latest` 503'd under load; pinned **`gemini-2.5-flash`** (config default) which responds steadily and describes images correctly (live-verified: "A red circle is centered on a dark background"). So camera-describe ("what am I holding/doing"), observe_screen, and any Gemini vision all work now. If a call 503s it's transient model load, not the key. 153 tests pass. Related: [[jarvis-model-usage-switching]], [[jarvis-autonomy-and-self-improvement]].
