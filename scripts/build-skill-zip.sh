#!/usr/bin/env bash
# Rebuild citation-check.skill, the packaged zip that claude.ai's
# Settings > Features > Skills uploader expects. Run this after editing
# anything under citation-check/ and before committing.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="citation-check.skill"
rm -f "$OUT"

# Exclude .claude-plugin/ -- that's Claude Code plugin metadata, not part of
# the plain skill format claude.ai expects.
zip -r "$OUT" citation-check \
  -x 'citation-check/.claude-plugin/*' \
  -x '**/.DS_Store'

echo "Built $OUT"
