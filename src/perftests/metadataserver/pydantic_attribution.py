# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Where does the time in `MachineResources(**output)` go?

`maasservicelayer.utils.lxd.MachineResources` is the Pydantic model the
hardware profile builder parses the commissioning output into. This script
compares the ways of constructing it and dumps a `cProfile` attribution, so
the cost can be split between pydantic-core validation and the Python-level
validators defined in `maasservicelayer/utils/lxd.py`.

Run from the repo root:

    DJANGO_SETTINGS_MODULE=maasserver.djangosettings.development \\
        bin/py src/perftests/metadataserver/pydantic_attribution.py [output.json]

Like the perf tests, this only records numbers. Nothing here passes or fails.
"""

import cProfile
import json
import pstats
import statistics
import sys
import time

import django

django.setup()

from pydantic import TypeAdapter  # noqa: E402

from maasservicelayer.utils.lxd import MachineResources  # noqa: E402
from perftests.metadataserver.payloads import PAYLOAD_BUILDERS  # noqa: E402

REPEATS = 2000
PROFILE_ITERATIONS = 500


def bench(fn, repeats=REPEATS):
    """Return (median_us, p90_us) over `repeats` calls."""
    for _ in range(50):
        fn()
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1e6)
    samples.sort()
    return statistics.median(samples), samples[int(0.9 * len(samples))]


def construction_variants(data, output):
    # Pydantic compiles and caches the validator on the model class, so a
    # TypeAdapter is measured here to show whether hoisting one out of the
    # per-node path would buy anything.
    adapter = TypeAdapter(MachineResources)
    return {
        "MachineResources(**data)": lambda: MachineResources(**data),
        "MachineResources.model_validate(data)": (
            lambda: MachineResources.model_validate(data)
        ),
        "cached TypeAdapter.validate_python(data)": (
            lambda: adapter.validate_python(data)
        ),
        "cached TypeAdapter.validate_json(bytes)": (
            lambda: adapter.validate_json(output)
        ),
        # Shows the validation-free floor. Does not recurse, so the nested
        # values stay dicts and the result is not usable by the parse helpers.
        "model_construct(**data) (no validation)": (
            lambda: MachineResources.model_construct(**data)
        ),
    }


def main():
    results = {}
    for payload_name, builder in sorted(PAYLOAD_BUILDERS.items()):
        data = builder(1).render()
        output = json.dumps(data).encode("utf-8")

        payload_results = {}
        for name, fn in construction_variants(data, output).items():
            median, p90 = bench(fn)
            payload_results[name] = {
                "median_us": round(median, 2),
                "p90_us": round(p90, 2),
            }
            print(f"{payload_name:<14} {name:<48} median={median:8.2f}us")
        results[payload_name] = payload_results

        profiler = cProfile.Profile()
        profiler.enable()
        for _ in range(PROFILE_ITERATIONS):
            MachineResources(**data)
        profiler.disable()
        print(
            f"\n--- cProfile: {PROFILE_ITERATIONS}x "
            f"MachineResources(**data) [{payload_name}]"
        )
        pstats.Stats(profiler).sort_stats("tottime").print_stats(12)
        print()

    if len(sys.argv) > 1:
        with open(sys.argv[1], "w") as fh:
            json.dump(results, fh, indent=2, sort_keys=True)
        print(f"wrote {sys.argv[1]}")


if __name__ == "__main__":
    main()
