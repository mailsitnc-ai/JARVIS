# Carry Claude Code's JARVIS memory to the Mac

These are the memory notes Claude built while we ported JARVIS. Dropping them into your Mac's Claude
Code project folder lets a **Mac-native** session start with the full context (no Windows needed).

On the Mac, after `git pull`:

```bash
cd ~/Desktop/JARVIS
# 1) open Claude Code here once so it creates this project's folder, then quit it:
#    (Claude desktop app -> Code tab -> open ~/Desktop/JARVIS,  OR:  claude )
# 2) copy the memory in:
PROJ="$(ls -d ~/.claude/projects/*JARVIS* 2>/dev/null | head -1)"
[ -z "$PROJ" ] && PROJ="$HOME/.claude/projects/$(pwd | sed 's#/#-#g')"
mkdir -p "$PROJ/memory"
cp _claude_memory/*.md "$PROJ/memory/"
echo "Installed memory to $PROJ/memory"
```

Now start Claude Code on the Mac in `~/Desktop/JARVIS` and it will load these notes automatically. You
can then delete this folder if you like: `git rm -r _claude_memory && git commit -m "remove handoff"`.
