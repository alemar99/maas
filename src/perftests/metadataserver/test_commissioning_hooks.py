# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Cost of parsing commissioning output with dicts (v2) vs Pydantic (v3).

`process_lxd_results` walks the output of the `50-maas-01-commissioning`
script as a plain dict, null-checking as it goes. The hardware profile
builder parses the same payload into `MachineResources` instead. Both parses
run for every commissioning machine today, so the question is what the
Pydantic one adds on top of the dict walking, and whether it matters next to
the database work `process_lxd_results` does around it.

The measured payloads are real commissioning output, one JSON file per
machine, supplied at run time with `--commissioning-data-dir`:

    bin/pytest --commissioning-data-dir /tmp/ps8-json \\
        src/perftests/metadataserver/test_commissioning_hooks.py

Real output carries serial numbers, MAC addresses and hostnames, so no
corpus is committed. Without the option the parsing tests are skipped, and
only `test_perf_process_lxd_results` runs, on generated data.

Like the rest of `src/perftests`, this records timing and query numbers and
asserts only that real work happened; there is no threshold anywhere in the
repo, so comparing two runs is a human judgement.
"""

import json

import pytest

from maasserver.testing.commissioning import FakeCommissioningData
from maasservicelayer.builders.hardwareprofile import HardwareProfileBuilder
from metadataserver.builtin_scripts import hooks
from provisioningserver.utils import lxd as v2_lxd


@pytest.fixture(scope="session")
def commissioning_payloads(pytestconfig):
    """Real commissioning output, one decoded payload per JSON file."""
    data_dir = pytestconfig.getoption("--commissioning-data-dir")
    if not data_dir:
        pytest.skip("--commissioning-data-dir was not given")
    paths = sorted(data_dir.glob("*.json"))
    assert paths, f"no *.json commissioning payloads in {data_dir}"
    return [json.loads(path.read_bytes()) for path in paths]


def _parse_with_dicts(payload):
    """The dict walking `process_lxd_results` performs, without the DB work.

    `parse_lxd_cpuinfo` hard-indexes `thread["numa_node"]`, which some real
    machines don't report, so it raises on payloads the Pydantic path parses
    fine. That is measured as it is rather than worked around, and the
    KeyError is left to fail the test when it happens.
    """
    resources = payload["resources"]
    v2_lxd.parse_lxd_cpuinfo(resources)
    v2_lxd.parse_lxd_networks(payload["networks"])
    hooks._condense_luns(
        [dict(disk) for disk in resources["storage"]["disks"]]
    )


def test_perf_parse_commissioning_output_with_dicts(
    perf, commissioning_payloads
):
    with perf.record("test_perf_parse_commissioning_output_with_dicts"):
        for payload in commissioning_payloads:
            _parse_with_dicts(payload)


def test_perf_parse_commissioning_output_with_pydantic(
    perf, commissioning_payloads
):
    with perf.record("test_perf_parse_commissioning_output_with_pydantic"):
        profiles = [
            HardwareProfileBuilder.from_commissioning_output(payload, 1)
            for payload in commissioning_payloads
        ]

    assert len(profiles) == len(commissioning_payloads)
    assert all(profile.cpu_cores > 0 for profile in profiles)


@pytest.mark.allow_transactions
def test_perf_process_lxd_results(perf, factory):
    """The whole hook, so the parsing cost can be read against the DB cost."""
    machine = factory.make_Machine()
    output = json.dumps(FakeCommissioningData().render()).encode("utf-8")

    with perf.record("test_perf_process_lxd_results"):
        hooks.process_lxd_results(machine, output, 0)

    machine.refresh_from_db()
    assert machine.cpu_count > 0
