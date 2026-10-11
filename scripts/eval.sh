#!/bin/sh
# Run each eval case in evals/ as an A/B: the control is prompts/review.md as
# committed, the treatment is the working copy. Each half is one harness turn
# that can call nothing, and the case's evaluator grades its reply. Each case
# costs two paid turns.
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

# One half: send its turn, wait for the end, and grade the reply.
half() {  # request, half (control or treatment)
  session="s_eval$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')_$2_1"
  iii call harness::send --json "$(printf '%s' "$1" | jq --arg s "$session" --arg h "$2" \
    '{session_id: $s, message: .[$h].prompt, provider: .model.provider, model: .model.model,
      options: {max_turns: 1, functions: {allow: [], deny: ["*"]}, metadata: {phase: "eval"}}}')" \
    > /dev/null  # paid: one turn
  while :; do
    status=$(iii call harness::status --json "{\"session_id\": \"$session\"}")
    case "$(printf '%s' "$status" | jq -r .status)" in completed|cancelled|failed) break ;; esac
    sleep 5
  done
  grade=$(printf '%s' "$1" | jq --argjson st "$status" \
    '{output: ($st.result // ""), arguments: .evaluator.arguments}')
  iii call "$(printf '%s' "$1" | jq -r .evaluator.function_id)" --json "$grade" |
    jq --arg h "$2" --argjson st "$status" \
      '{half: $h, status: $st.status, passed, reason} + if $st.result_error then {error: $st.result_error} else {} end'
}

for case in "$@"; do
  request=$(jq --rawfile c "$control" --rawfile t prompts/review.md \
    '.control.prompt = $c + .input | .treatment.prompt = $t + .input | del(.input)' "$case")
  if [ "$send" = no ]; then
    printf '%s\n' "$request"
  else
    echo "$case:" >&2
    half "$request" control
    half "$request" treatment
  fi
done
