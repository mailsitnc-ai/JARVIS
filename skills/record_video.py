# Evolved by JARVIS on 2026-09-17 11:28 for: record a 3 second video
import re
import tempfile
import os

SKILL = {'name': 'record_video', 'description': 'Capture a short video from the webcam for a specified duration and save it to a file.', 'triggers': ['\\brecord\\b', '\\bvideo\\b', '\\bseconds?\\b'], 'version': 1, 'origin': 'evolved', 'requires': ['opencv-python']}


def _parse_duration(request: str) -> int:
    """Return duration in seconds extracted from the request, default 5."""
    match = re.search(r"(\d+)\s*seconds?", request, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)\s*s\b", request, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 5  # fallback default


def run(request, context):
    duration = _parse_duration(request)
    if duration <= 0:
        return "Please specify a positive number of seconds to record."

    # Create a temporary file for the video
    tmp_dir = tempfile.gettempdir()
    video_path = os.path.join(tmp_dir, f"recorded_{duration}s.mp4")

    # Import cv2 lazily; the skill declares it in "requires"
    import cv2

    # Open default webcam
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return "Unable to access the webcam."

    # Define video properties
    fps = 20.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(video_path, fourcc, fps, (width, height))

    frames_to_capture = int(fps * duration)
    captured = 0
    while captured < frames_to_capture:
        ret, frame = cap.read()
        if not ret:
            break
        out.write(frame)
        captured += 1

    # Release resources
    cap.release()
    out.release()

    # Notify user and return path
    context["actions"].notify(
        f"Video saved to {video_path}", title="Video Recorded"
    )
    return f"Recorded a {duration}-second video and saved it to: {video_path}"
