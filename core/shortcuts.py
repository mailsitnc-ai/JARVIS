"""Keyboard shortcuts JARVIS can press for you - by voice, typed, or from a hand gesture.

  tabs      next / previous / close / new / reopen / go to tab N
  apps      switch app (Cmd+Tab), hide the current app
  desktops  next / previous desktop (Ctrl + arrow)
  screen    lock, sleep
  editing   copy, paste, cut, undo, redo, select all, save, find

Every shortcut is a (key, modifiers) pair per platform, so the same names work on macOS and Windows.
"""
from __future__ import annotations

import re
import sys

MAC = sys.platform == "darwin"
_C = "cmd" if MAC else "ctrl"

# name -> (key, modifiers) on this platform
SHORTCUTS = {
    "next_tab": ("tab", ["ctrl"]),
    "prev_tab": ("tab", ["ctrl", "shift"]),
    "close_tab": ("w", [_C]),
    "new_tab": ("t", [_C]),
    "reopen_tab": ("t", [_C, "shift"]),
    "switch_app": ("tab", ["cmd"] if MAC else ["alt"]),
    "hide_app": ("h", [_C]) if MAC else ("down", ["cmd"]),
    "next_desktop": ("right", ["ctrl"]),
    "prev_desktop": ("left", ["ctrl"]),
    "lock": ("q", ["cmd", "ctrl"]) if MAC else ("l", ["cmd"]),
    "copy": ("c", [_C]), "paste": ("v", [_C]), "cut": ("x", [_C]),
    "undo": ("z", [_C]), "redo": ("z", [_C, "shift"]),
    "select_all": ("a", [_C]), "save": ("s", [_C]), "find": ("f", [_C]),
    "close_window": ("w", [_C, "shift"]) if MAC else ("f4", ["alt"]),
    "fullscreen": ("f", ["ctrl", "cmd"]) if MAC else ("f11", []),
}

HUMAN = {
    "next_tab": "next tab", "prev_tab": "previous tab", "close_tab": "closed the tab",
    "new_tab": "new tab", "reopen_tab": "reopened the last tab", "switch_app": "switched app",
    "hide_app": "hid the app", "next_desktop": "next desktop", "prev_desktop": "previous desktop",
    "lock": "locked the screen", "copy": "copied", "paste": "pasted", "cut": "cut",
    "undo": "undone", "redo": "redone", "select_all": "selected all", "save": "saved",
    "find": "opened find", "close_window": "closed the window", "fullscreen": "toggled full screen",
}

# What the user might say -> shortcut name. Checked in order, so put the specific ones first.
PHRASES = [
    (r"\b(?:re-?open|restore|undo\s+clos\w+)\s+(?:that\s+|the\s+|my\s+|last\s+|closed\s+)*tab\b", "reopen_tab"),
    (r"\b(?:new|open\s+a?\s*new)\s+tab\b", "new_tab"),
    (r"\b(?:close|cross|kill)\s+(?:this\s+|that\s+|the\s+|my\s+|current\s+)*tab\b|\btab\s+close\b", "close_tab"),
    (r"\b(?:next|forward)\s+(?:the\s+)?tab\b|\btab\s+(?:right|forward|next)\b|\bswitch\s+tab\b", "next_tab"),
    (r"\b(?:previous|prev|last|back|left)\s+(?:the\s+)?tab\b|\btab\s+(?:left|back|previous)\b", "prev_tab"),
    (r"\b(?:switch|change|next|cycle)\s+(?:the\s+)?apps?\b|\bapp\s+switcher\b|\bcommand\s*tab\b", "switch_app"),
    (r"\bhide\s+(?:this|the\s+current)?\s*app\b|\bhide\s+(?:this\s+)?window\b", "hide_app"),
    (r"\b(?:next|right)\s+(?:desktop|space|screen)\b|\b(?:desktop|space)\s+right\b", "next_desktop"),
    (r"\b(?:previous|prev|last|left)\s+(?:desktop|space|screen)\b|\b(?:desktop|space)\s+left\b", "prev_desktop"),
    (r"\block\s+(?:the\s+)?(?:screen|mac|computer|laptop)\b|\block\s+it\b|\block\s+up\b", "lock"),
    (r"\b(?:sleep|put\s+.{0,12}to\s+sleep)\b.*\b(?:mac|computer|laptop|screen)\b|"
     r"\b(?:mac|computer|laptop)\b.*\bsleep\b", "sleep"),
    (r"\bselect\s+all\b", "select_all"),
    (r"\bundo\s+that\b|\bundo\b\s*$", "undo"),
    (r"\bredo\b", "redo"),
    (r"\bfull\s*screen\b", "fullscreen"),
    (r"\bclose\s+(?:this|the)\s+window\b", "close_window"),
]
# "the last tab" means the PREVIOUS one to most people (that is handled in PHRASES); a specific tab is
# "tab 3" / "go to tab 3", and the rightmost is "tab 9" - what the browser shortcut itself does.
_TAB_N = re.compile(r"\b(?:go\s+to|switch\s+to|jump\s+to|open|show|tab)\s+(?:the\s+)?tab\s*(?:number\s*)?"
                    r"(\d|one|two|three|four|five|six|seven|eight|nine)\b|"
                    r"\btab\s+(\d|one|two|three|four|five|six|seven|eight|nine)\b", re.IGNORECASE)
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}


def parse(request: str):
    """('tab_n', 3) / ('next_tab', None) / (None, None) - what the user asked for."""
    text = str(request or "")
    m = _TAB_N.search(text)
    if m:
        word = (m.group(1) or m.group(2) or "").lower()
        return "tab_n", _WORDS.get(word, int(word) if word.isdigit() else None)
    for pattern, name in PHRASES:
        if re.search(pattern, text, re.IGNORECASE):
            return name, None
    return None, None


def press(name: str, number: int | None = None) -> str:
    """Send the shortcut. Returns a short human sentence about what was pressed."""
    from core import oslayer
    if name == "tab_n":
        if not number or not 1 <= number <= 9:
            return "Which tab, sir? Tabs 1 to 9, or 'tab 9' for the last one."
        oslayer.key_press(str(number), ["cmd" if MAC else "ctrl"])
        return f"Tab {number}." if number < 9 else "Last tab (9 jumps to the rightmost)."
    if name == "sleep":
        if MAC:
            import subprocess
            subprocess.run(["pmset", "sleepnow"], capture_output=True, timeout=10)
        else:
            oslayer.key_press("f4", ["alt"])
        return "Going to sleep, sir."
    combo = SHORTCUTS.get(name)
    if not combo:
        return f"I don't know the shortcut {name!r}."
    key, mods = combo
    ok = oslayer.key_press(key, mods)
    if not ok:
        return ("I couldn't send that key - macOS needs JARVIS allowed in System Settings > Privacy & "
                "Security > Accessibility.")
    return HUMAN.get(name, name.replace("_", " ")).capitalize() + "."
