#!/usr/bin/env bash
# Save the long-term memory of a session to git before the Vocareum lab session ends
# (Req. Section 10.3.4; Architecture Spec Section 12).
#
#     bash scripts/commit_state.sh            # commit only
#     bash scripts/commit_state.sh --push     # commit, then push to origin
#
# Stages data/ (incident store, outbox, feedback, evaluation results, golden dataset,
# prompt versions), verified resolutions, retrieval aliases and logs/, and commits them
# with a UTC timestamp. Never stages .env. Does nothing if there is nothing to save.

set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

paths=(data knowledge/raw/postmortems/verified knowledge/retrieval_aliases.json logs docs/evidence)
existing=()
for p in "${paths[@]}"; do
    [ -e "$p" ] && existing+=("$p")
done

git add -- "${existing[@]}"
git reset -q -- .env 2>/dev/null || true
if git diff --cached --quiet; then
    echo "Nothing to save."
    exit 0
fi
stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
git commit -q -m "State: session data $stamp" -m "Saved by scripts/commit_state.sh (incident store, outbox, feedback, evaluation results, logs)."
echo "Committed: $(git log --oneline -1)"
if [ "${1:-}" = "--push" ]; then
    git push
fi
