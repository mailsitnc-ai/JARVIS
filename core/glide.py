"""Swipe typing for the hand keyboard - the thing your phone does when you trace through the letters.

You don't aim at each key any more: pinch, drag your fingertip through roughly the right letters,
release, and we work out the word from the SHAPE of the path. That removes the whole class of typo
the point-and-rest keyboard made, because no single key has to be hit accurately.

How the word is chosen (this is how phone keyboards do it too):
  - every word in the vocabulary has an "ideal" path: a line through the centres of its keys;
  - your path and each ideal path are resampled to the same number of points along their length;
  - we compare them twice - where they are (location) and what they look like once moved and scaled
    to the same size (shape) - because a small careful swipe and a big sloppy one mean the same word;
  - the first and last letters have to be near where you started and stopped, which throws away
    almost every candidate before any of that work happens.

Words you type letter by letter are remembered (`learn`), so your own names and slang win next time.
"""
from __future__ import annotations

import json
import math

SAMPLES = 24          # points each path is resampled to before comparing
START_NEAR = 1.6      # how many key-widths from your first point the word's first key may be
SHAPE, LOCATION = 0.55, 0.45
GOOD = 0.60           # an everyday word this close is taken; only a worse one is worth a deep search
MAX = 0.80            # nothing looks like a word: type nothing rather than something wrong
COMMON_BONUS = 0.05   # everyday words win a tie against obscure dictionary ones
# An obscure dictionary word has to be MUCH better, not slightly: a word with an extra letter in it
# ("helio" for "hello") can trace almost the same path, and you meant the word people actually use.
DEEP_PENALTY = 0.22
LEARNED_BONUS = 0.06


def centres(rows, area):
    """Key -> the middle of that key, in the same 0-1 frame coordinates the fingertip uses."""
    x0, y0, x1, y1 = area
    rh = (y1 - y0) / max(1, len(rows))
    out = {}
    for r, keys in enumerate(rows):
        kw = (x1 - x0) / max(1, len(keys))
        for c, key in enumerate(keys):
            out[key] = (x0 + kw * (c + 0.5), y0 + rh * (r + 0.5))
    return out


def key_size(rows, area):
    """(width, height) of one letter key - the yardstick for 'near'."""
    x0, y0, x1, y1 = area
    return (x1 - x0) / max(1, len(rows[0])), (y1 - y0) / max(1, len(rows))


def ideal_path(word, points):
    """The path you'd draw if you were perfect: the key centres, with repeats collapsed."""
    out = []
    for ch in word:
        p = points.get(ch)
        if p is None:
            return None
        if not out or p != out[-1]:
            out.append(p)
    return out


def resample(path, n=SAMPLES):
    """n points spread evenly ALONG the path, so speed and frame rate stop mattering."""
    pts = [p for i, p in enumerate(path) if i == 0 or p != path[i - 1]]
    if not pts:
        return []
    if len(pts) == 1:
        return [pts[0]] * n
    lengths = [0.0]
    for a, b in zip(pts, pts[1:]):
        lengths.append(lengths[-1] + math.dist(a, b))
    total = lengths[-1]
    if total <= 0:
        return [pts[0]] * n
    out, j = [], 0
    for i in range(n):
        want = total * i / (n - 1)
        while j < len(lengths) - 2 and lengths[j + 1] < want:
            j += 1
        span = lengths[j + 1] - lengths[j] or 1e-9
        f = (want - lengths[j]) / span
        out.append((pts[j][0] + (pts[j + 1][0] - pts[j][0]) * f,
                    pts[j][1] + (pts[j + 1][1] - pts[j][1]) * f))
    return out


def shape_of(pts):
    """The same path moved to the origin and scaled to a standard size - a big lazy swipe and a
    small careful one through the same letters come out looking the same."""
    if not pts:
        return []
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    moved = [(p[0] - cx, p[1] - cy) for p in pts]
    scale = max(max(abs(p[0]) for p in moved), max(abs(p[1]) for p in moved), 1e-6)
    return [(p[0] / scale, p[1] / scale) for p in moved]


def _mean_distance(a, b):
    return sum(math.dist(p, q) for p, q in zip(a, b)) / max(1, len(a))


def prepare(path):
    """Resample the drawn path once, so comparing it against a thousand words stays cheap."""
    pts = resample(path)
    return pts, shape_of(pts)


def score(user, word_path, wide):
    """How wrong this word is for that path: 0 is perfect. `wide` scales distances to key-widths.
    `user` is a path, or a (resampled, shape) pair from prepare()."""
    a, a_shape = user if isinstance(user, tuple) else prepare(user)
    b = resample(word_path)
    location = _mean_distance(a, b) / wide
    form = _mean_distance(a_shape, shape_of(b))
    return LOCATION * location + SHAPE * form


class Vocabulary:
    """The words swipe typing can produce: everyday words, plus the ones you've typed yourself."""

    def __init__(self, store=None):
        self.store = store
        self.learned = {}
        if store:
            try:
                self.learned = {str(k): int(v) for k, v in json.loads(store.read_text()).items()}
            except Exception:
                self.learned = {}

    @property
    def words(self):
        from core.wordfix import COMMON, KEEP
        return list(dict.fromkeys(list(COMMON) + list(KEEP) + list(self.learned)))

    def learn(self, word):
        """Remember a word the user typed out letter by letter, so swiping finds it next time."""
        word = str(word or "").strip().lower()
        if not word.isalpha() or len(word) < 2:
            return
        self.learned[word] = self.learned.get(word, 0) + 1
        if self.store:
            try:
                self.store.write_text(json.dumps(self.learned))
            except OSError:
                pass

    def bonus(self, word):
        return LEARNED_BONUS if word in self.learned else 0.0


def candidates(path, points, wide, words, first_near=START_NEAR):
    """Every plausible word for this path, best first, as (word, score)."""
    if len(path) < 2:
        return []
    start, end = path[0], path[-1]
    drawn = prepare(path)
    out = []
    for word in words:
        if len(word) < 2:
            continue
        head, tail = points.get(word[0]), points.get(word[-1])
        if head is None or tail is None:
            continue
        if math.dist(start, head) > first_near * wide or math.dist(end, tail) > first_near * wide:
            continue                      # you didn't start or stop anywhere near this word's ends
        ideal = ideal_path(word, points)
        if ideal is None:
            continue
        out.append((word, score(drawn, ideal, wide)))
    out.sort(key=lambda w: w[1])
    return out


def decode(path, points, wide, vocab, deep=None, limit=3):
    """The word you probably swiped, plus runners-up to offer: ([words], best_score).

    Nothing is returned when no word really fits - typing the wrong word is worse than typing none."""
    words = vocab.words if hasattr(vocab, "words") else list(vocab)
    scored = [(w, s - (vocab.bonus(w) if hasattr(vocab, "bonus") else 0.0) - COMMON_BONUS)
              for w, s in candidates(path, points, wide, words)]
    scored.sort(key=lambda w: w[1])
    if (not scored or scored[0][1] > GOOD) and deep:
        extra = [(w, s + DEEP_PENALTY) for w, s in candidates(path, points, wide, deep(path, points))]
        scored = sorted(scored + extra, key=lambda w: w[1])
    if not scored or scored[0][1] > MAX:
        return [], (scored[0][1] if scored else 99.0)
    best = [w for w, _ in scored[:limit]]
    return best, scored[0][1]


def dictionary_words(path, points):
    """Fallback for a word the everyday list doesn't have: the big system dictionary, but only the
    words that start and end on the right keys - a few hundred instead of a quarter of a million."""
    from core.wordfix import dictionary
    start, end = path[0], path[-1]
    near = sorted(points, key=lambda k: math.dist(points[k], start))[:3]
    last = sorted(points, key=lambda k: math.dist(points[k], end))[:3]
    heads = {k for k in near if len(k) == 1}
    tails = {k for k in last if len(k) == 1}
    return [w for w in dictionary()
            if 2 < len(w) <= 14 and w[0] in heads and w[-1] in tails and w.isalpha()]
