#!/usr/bin/env bash
# arcan-skills-sync — Bridge npx skills installs into .arcan/skills/
#
# Creates symlinks in .arcan/skills/ pointing to skills found in:
#   1. .agents/skills/     (project-local)
#   2. ~/.agents/skills/   (global)
#
# This makes skills discoverable by the Arcan agent runtime.
# Run after `npx skills add` or `bstack bootstrap`.

set -e

ARCAN_DIR="${1:-.arcan}"
ARCAN_SKILLS="${ARCAN_DIR}/skills"

# Source directories (project-local first, then global)
PROJECT_AGENTS="$(pwd)/.agents/skills"
GLOBAL_AGENTS="${HOME}/.agents/skills"

mkdir -p "$ARCAN_SKILLS"

synced=0
skipped=0

echo "=== arcan-skills-sync ==="
echo "Syncing skills into ${ARCAN_SKILLS}..."
echo ""

for source_dir in "$PROJECT_AGENTS" "$GLOBAL_AGENTS"; do
  if [ ! -d "$source_dir" ]; then
    continue
  fi

  for skill_dir in "$source_dir"/*/; do
    [ -d "$skill_dir" ] || continue

    skill_name="$(basename "$skill_dir")"

    # Only sync directories that contain a SKILL.md
    if [ ! -f "${skill_dir}SKILL.md" ]; then
      continue
    fi

    link_path="${ARCAN_SKILLS}/${skill_name}"

    if [ -e "$link_path" ]; then
      skipped=$((skipped + 1))
      continue
    fi

    ln -snf "$(cd "$skill_dir" && pwd)" "$link_path"
    echo "  [link] ${skill_name} -> ${skill_dir}"
    synced=$((synced + 1))
  done
done

echo ""
echo "=== arcan-skills-sync complete ==="
echo "  Synced: ${synced} | Skipped: ${skipped}"

# Count total skills in .arcan/skills/
total=$(find "$ARCAN_SKILLS" -maxdepth 2 -name "SKILL.md" 2>/dev/null | wc -l | tr -d ' ')
echo "  Total skills in ${ARCAN_SKILLS}: ${total}"
