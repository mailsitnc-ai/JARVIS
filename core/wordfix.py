"""Autocorrect for the point-and-type keyboard - the thing a phone keyboard does for you.

Pointing at letters produces the typos a thumb does: a neighbouring key ("thrww"), a swapped pair
("thre" -> "three" needs a letter, "teh" needs a swap), a letter missed while sweeping, or a letter
typed twice because the finger rested a moment too long.

So when you finish a word (space or enter), if it isn't a real word we look for the real word one
small edit away, and edits that match the QWERTY layout - the key NEXT to the one you meant - count
as cheaper than random ones. A word that IS in the dictionary is never touched.
"""
from __future__ import annotations

import functools
import re

ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
LETTERS = "abcdefghijklmnopqrstuvwxyz"
SYSTEM_WORDS = "/usr/share/dict/words"

# Frequent words: both a fallback dictionary (Windows has no word list) and how we choose between
# two candidates - "teh" should become "the", not some obscurity out of the big dictionary.
COMMON = """a about after again all almost already also always am an and another any anything are around as
ask asked at back bad be because been before being best better between big both bring but buy by call called
came can cant come coming could couldnt did didnt do does doesnt doing done dont down each early eat even
ever every everyone everything exam exams find fine first for friend friends from get gets getting give go
going gone good got great had half happen happy has have havent he hello help her here hey him his hi hold
home homework hope hour hours how however if im in into is isnt it its ive just keep kept know known last
late later left less let lets like liked little long look looking lot love made make making many math maths
may maybe me mean meet message messages might mine minute minutes mom morning most move much must my name
need needs never new next nice night no not note notes nothing now number of off ok okay old on once one only
open or other our out over own paper people phone photo pick place please project put question questions
quick quickly rather read ready really right room said same say saying school see seen send sending sent
she should shouldnt show sir sit sleep slow small so some someone something soon sorry sound speak start
started still stop study studying stuff such sure take taken talk teacher tell test tests than thank thanks
that the their them then there these they thing things think this those though thought three through time
times to today together tomorrow tonight too took top try trying tuition turn two under until up us use used
very wait want was wasnt watch water way we week weekend well went were what when where which while who why
will with without wont word words work working world would wouldnt write writing wrong yeah year years yes
yesterday yet you your youre yours
actually already answer anyone anyway around believe better birthday busy careful certain chapter check
class classes clear college complete definitely different difficult during early enough exactly example
expect explain family favourite finish finished follow forgot forward guess hungry idea important interesting
kind later leave listen literally maybe money mostly myself nearly obviously perfect perhaps person picture
possible practice prepare pretty probably problem promise quiet reach reason remember revise revision sample
science second section seriously several simple since single sleepy slowly sometimes special specific subject
submit suddenly suppose teacher teachers themselves therefore tired together tough travel trouble understand
unless usually video watching weather whatever whether window without wonder worried writing yourself""".split()

# Chat shorthand: real spellings for us, even though no dictionary has them. Never "corrected".
KEEP = set("""sry plz pls thx ty tysm idk idc lol lmao lmk brb btw omw nvm imo tbh irl asap np gm gn gg ttyl
wyd hbu rn cuz coz bro bruh bhai yo ya yaar yep yup nah nope okk oki okok kk haha hehe hmm hmmm ik ikr ofc
wanna gonna gotta kinda sorta dunno lemme gimme ain aint prolly congrats pic pics insta whatsapp jarvis
sir maam ma dm msg msgs mins min hrs hr sec secs tmrw tmr tonite u ur r y k n b4 gr8 thanku""".split())


@functools.lru_cache(maxsize=1)
def neighbours() -> dict:
    """key -> the keys touching it on a QWERTY keyboard (sideways and diagonally)."""
    where = {k: (r, c) for r, row in enumerate(ROWS) for c, k in enumerate(row)}
    near = {}
    for key, (r, c) in where.items():
        near[key] = {other for other, (r2, c2) in where.items()
                     if other != key and abs(r - r2) <= 1 and abs(c - c2) <= 1}
    return near


@functools.lru_cache(maxsize=1)
def dictionary() -> frozenset:
    """Every word we'll accept, lower-case. The system list if there is one, plus the common words."""
    words = set(COMMON)
    try:
        with open(SYSTEM_WORDS, encoding="utf-8", errors="ignore") as fh:
            words.update(line.strip().lower() for line in fh if line.strip().isalpha())
    except OSError:
        pass
    return frozenset(words)


def known(word: str) -> bool:
    return word.lower() in dictionary()


def _candidates(word: str):
    """Every (candidate, cost) one small edit away. Cheap edits are the ones a finger actually makes."""
    near = neighbours()
    out = []
    for i, ch in enumerate(word):
        rest = word[:i] + word[i + 1:]
        # a neighbouring key instead of the one you meant
        for other in near.get(ch, ()):
            out.append((word[:i] + other + word[i + 1:], 1.0))
        # the same letter typed twice (rested on it a beat too long)
        out.append((rest, 0.7 if i and word[i - 1] == ch else 1.3))
        # two letters the wrong way round
        if i + 1 < len(word):
            out.append((word[:i] + word[i + 1] + ch + word[i + 2:], 1.0))
        # a letter missed while sweeping across - most often the second half of a double letter
        # ("thre" for "three", "helo" for "hello"), which resting-to-repeat misses easily
        for letter in LETTERS:
            double = letter == ch or (i and letter == word[i - 1])
            out.append((word[:i] + letter + word[i:],
                        0.9 if double else 1.4 if letter in near.get(ch, ()) else 1.7))
    for letter in LETTERS:
        out.append((word + letter, 1.5))
    return out


def _distance(a: str, b: str, cap: int = 2) -> int:
    """How many edits apart two words are (swaps count as one), giving up past `cap`."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            row[j] = min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (ca != cb))
            if prev2 and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                row[j] = min(row[j], prev2[j - 2] + 1)          # two letters swapped
        if min(row) > cap:
            return cap + 1
        prev2, prev = prev, row
    return prev[-1]


def _common_match(word: str):
    """A longer mistyped word ('tommorow') is often two edits from a common word - look there too."""
    if len(word) < 6:
        return None
    best, second = (None, 9), 9
    for candidate in COMMON:
        if candidate[0] != word[0] or abs(len(candidate) - len(word)) > 2:
            continue
        d = _distance(word, candidate)
        if d < best[1]:
            best, second = (candidate, d), best[1]
        elif d < second:
            second = d
    return best[0] if best[1] <= 2 and second > best[1] else None


def correct(word: str, limit: float = 1.45) -> str | None:
    """The word you probably meant, or None to leave it alone (already a word, or too unclear)."""
    raw = str(word or "")
    if len(raw) < 3 or not raw.isalpha():
        return None
    low = raw.lower()
    if known(low) or low in KEEP:
        return None
    words, common = dictionary(), set(COMMON)
    best, best_cost = None, 99.0
    for candidate, cost in _candidates(low):
        if candidate not in words or candidate == low:
            continue
        score = cost - (0.45 if candidate in common else 0.0)     # prefer words people actually use
        if score < best_cost:
            best, best_cost = candidate, score
    if best is None or best_cost > limit:
        best = _common_match(low)
        if best is None:
            return None
    if raw.isupper():
        return best.upper()
    if raw[:1].isupper():
        return best.capitalize()
    return best


def fix_text(text: str) -> str:
    """Autocorrect every word of a sentence - used by 'type ... (autocorrected)' and the tests."""
    return re.sub(r"[A-Za-z]+", lambda m: correct(m.group(0)) or m.group(0), str(text or ""))
