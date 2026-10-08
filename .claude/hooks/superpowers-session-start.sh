#!/usr/bin/env bash
# Injects the using-superpowers skill at session start (replaces the plugin's own hook).
set -euo pipefail

skill="${CLAUDE_PROJECT_DIR:-.}/.claude/skills/using-superpowers/SKILL.md"
[ -f "$skill" ] || exit 0

printf '<EXTREMELY_IMPORTANT>\nYou have superpowers.\n\n'
printf "**Below is the full content of your 'using-superpowers' skill - your introduction to using skills. For all other skills, use the 'Skill' tool:**\n\n"
cat "$skill"
printf '\n</EXTREMELY_IMPORTANT>\n'
