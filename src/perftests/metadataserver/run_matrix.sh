#!/bin/bash
# Run the commissioning parsing perf test across states, payload shapes, node
# counts and repetitions, writing one results.json per cell.
#
# Results land in $OUT/<state>-<payload>-<nodes>-<rep>/results.json, which
# summarise.py aggregates. Existing cells are skipped, so an interrupted run
# can simply be restarted.
#
#   OUT=/tmp/perf/runs REPS=3 ./src/perftests/metadataserver/run_matrix.sh
#   bin/py src/perftests/metadataserver/summarise.py /tmp/perf/runs
#
# src/perftests has no pass/fail threshold; the output is for human comparison
# between two runs.
set -u
cd "$(dirname "$0")/../../.." || exit 1

OUT=${OUT:-/tmp/perf/runs}
REPS=${REPS:-3}
STATES=${STATES:-"base full build_only"}
PAYLOADS=${PAYLOADS:-"sampledata large-server"}
COUNTS=${COUNTS:-"10 100"}
PYTEST=${PYTEST:-bin/pytest}

mkdir -p "$OUT"
for rep in $(seq 1 "$REPS"); do
  for payload in $PAYLOADS; do
    for count in $COUNTS; do
      for state in $STATES; do
        dir="$OUT/$state-$payload-$count-$rep"
        [ -f "$dir/results.json" ] && continue
        echo "== $state $payload n=$count rep=$rep"
        MAAS_PERF_HOOKS_STATE=$state \
        MAAS_PERF_PAYLOAD=$payload \
        MAAS_PERF_NODES=$count \
          "$PYTEST" -q -p no:randomly \
            --perf-output-dir "$dir" \
            src/perftests/metadataserver/test_commissioning_hooks.py \
            >"$dir.log" 2>&1 || echo "FAILED $dir, see $dir.log"
      done
    done
  done
done
