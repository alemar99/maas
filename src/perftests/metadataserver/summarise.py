# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Aggregate the perf-test result JSONs produced by run_matrix.sh.

    bin/py src/perftests/metadataserver/summarise.py /tmp/perf/runs

Prints a per-node table and writes summary.json alongside the runs. These are
recorded numbers only; src/perftests has no pass/fail threshold, so comparing
two summaries is a judgement call for a human.
"""

import json
import pathlib
import statistics
import sys

RUNS_DIR = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/perf/runs")


def parse_cell_name(name):
    """Split "<state>-<payload>-<nodes>-<rep>"; the payload may contain "-"."""
    state, remainder = name.split("-", 1)
    payload, nodes, rep = remainder.rsplit("-", 2)
    return state, payload, nodes, rep


def collect(runs_dir):
    records = {}
    for results_file in sorted(runs_dir.glob("*/results.json")):
        state, payload, nodes, _rep = parse_cell_name(results_file.parent.name)
        data = json.loads(results_file.read_text())
        for test_name, metrics in data["tests"].items():
            single = "single_node" in test_name
            key = (payload, 1 if single else int(nodes), state)
            records.setdefault(key, []).append(metrics)
    return records


def summarise(samples, field):
    values = [sample[field] for sample in samples]
    return statistics.median(values), min(values), max(values)


def build_rows(records):
    rows = []
    for payload, nodes, state in sorted(records):
        samples = records[(payload, nodes, state)]
        duration_median, duration_min, duration_max = summarise(
            samples, "duration"
        )
        query_count, _, _ = summarise(samples, "query_count")
        query_time, _, _ = summarise(samples, "query_time")
        rows.append(
            {
                "payload": payload,
                "nodes": nodes,
                "state": state,
                "runs": len(samples),
                "duration_median_s": round(duration_median, 4),
                "duration_min_s": round(duration_min, 4),
                "duration_max_s": round(duration_max, 4),
                "per_node_ms": round(1000 * duration_median / nodes, 3),
                "query_count": query_count,
                "queries_per_node": round(query_count / nodes, 1),
                "query_time_median_s": round(query_time, 4),
                "query_time_per_node_ms": round(1000 * query_time / nodes, 3),
                "non_db_per_node_ms": round(
                    1000 * (duration_median - query_time) / nodes, 3
                ),
            }
        )
    return rows


COLUMNS = (
    ("payload", "payload", "<14", "{}"),
    ("nodes", "nodes", ">6", "{}"),
    ("state", "state", ">12", "{}"),
    ("runs", "runs", ">5", "{}"),
    ("duration_median_s", "dur_med_s", ">11", "{:.4f}"),
    ("duration_min_s", "dur_min", ">9", "{:.4f}"),
    ("duration_max_s", "dur_max", ">9", "{:.4f}"),
    ("per_node_ms", "ms/node", ">10", "{:.3f}"),
    ("queries_per_node", "q/node", ">9", "{:.1f}"),
    ("query_time_per_node_ms", "qtime_ms/node", ">15", "{:.3f}"),
    ("non_db_per_node_ms", "nonDB_ms/node", ">15", "{:.3f}"),
)


def main():
    rows = build_rows(collect(RUNS_DIR))

    header = "".join(format(title, align) for _, title, align, _ in COLUMNS)
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            "".join(
                format(fmt.format(row[field]), align)
                for field, _, align, fmt in COLUMNS
            )
        )

    summary_path = RUNS_DIR / "summary.json"
    with open(summary_path, "w") as fh:
        json.dump(rows, fh, indent=2)
    print(f"\nwrote {summary_path}")


if __name__ == "__main__":
    main()
