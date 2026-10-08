#!/usr/bin/env bash
# Re-vendors Superpowers skills (https://github.com/obra/superpowers) into .claude/skills.
# Usage: scripts/update-superpowers.sh [git-ref]   (default: main)
set -euo pipefail

ref="${1:-main}"
root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

git clone --quiet --depth 1 --branch "$ref" https://github.com/obra/superpowers.git "$tmp/superpowers"

skills_dir="$root/.claude/skills"
for dir in "$tmp/superpowers/skills"/*/; do
  name="$(basename "$dir")"
  rm -rf "$skills_dir/$name"
  cp -a "$dir" "$skills_dir/$name"
done
cp "$tmp/superpowers/LICENSE" "$skills_dir/SUPERPOWERS-LICENSE"

# Project skills have no plugin namespace: superpowers:foo -> foo
grep -rlE 'superpowers:[a-z]' "$skills_dir" | xargs -r sed -i -E 's/superpowers:([a-z][a-z-]*)/\1/g'

echo "Superpowers skills updated from $ref ($(git -C "$tmp/superpowers" rev-parse --short HEAD))"
