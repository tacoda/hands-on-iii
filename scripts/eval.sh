#!/bin/sh
# Send each eval case in evals/ to eval::start: the control is prompts/review.md
# as committed, the treatment is the working copy. Each case costs two paid turns.
#
#   scripts/eval.sh                       # every case in evals/
#   scripts/eval.sh evals/review-names-a-place.json
#   scripts/eval.sh --print evals/...     # print the request, send nothing
#
# Run it before you commit the edit: after the commit, both halves are one file.
set -eu
cd "$(dirname "$0")/.."

send=yes
if [ "${1:-}" = "--print" ]; then send=no; shift; fi
[ "$#" -gt 0 ] || set -- evals/*.json

control=$(mktemp)
trap 'rm -f "$control"' EXIT
git show HEAD:prompts/review.md > "$control"

for case in "$@"; do
  request=$(jq --rawfile c "$control" --rawfile t prompts/review.md \
    '.control.prompt = $c + .input | .treatment.prompt = $t + .input | del(.input)' "$case")
  if [ "$send" = no ]; then
    printf '%s\n' "$request"
  else
    echo "$case:" >&2
    iii trigger eval::start --json "$request"  # paid: two turns
  fi
done
