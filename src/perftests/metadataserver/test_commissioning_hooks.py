# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Performance tests for commissioning output processing.

`process_lxd_results` is the hook that turns the output of the
`50-maas-01-commissioning` script into nodes, NUMA nodes, interfaces, block
devices, node devices and node metadata. It runs once per commissioned node,
so its cost scales directly with fleet size, but nothing in `src/perftests`
covered it until now.

Structure copied from `src/perftests/maasserver/models/test_machine.py` and
`src/perftests/maasserver/dns/test_config.py`.

Like the rest of `src/perftests`, these tests record timing, SQL query count
and SQL query time; they do **not** assert on any of them. There is no
threshold and no regression gate anywhere in the repo, so the numbers are for
a human to compare between two runs. The assertions that are present only
check that the measured call did real work, so a run that silently processed
nothing is not mistaken for a fast one.

Three states are selectable with `MAAS_PERF_HOOKS_STATE`, so the cost of the
hardware profile feature can be separated from the cost of the commissioning
parsing it sits next to:

``base``
    `_populate_hardware_profile` disabled entirely. Approximates the code
    before the hardware profile feature was added.
``full`` (default)
    Unmodified `process_lxd_results`, exactly as it ships.
``build_only``
    The hardware profile is built from the commissioning output as usual, but
    the resulting row is not written. The difference between this and ``full``
    is the feature's database cost; the difference between this and ``base``
    is its parsing cost.

The payload shape is selected with `MAAS_PERF_PAYLOAD` (`sampledata` or
`large-server`, see `payloads.py`) and the node count with `MAAS_PERF_NODES`.
"""

import json
import os

import pytest

from maasserver.models import Machine
from metadataserver.builtin_scripts import hooks
from perftests.metadataserver.payloads import PAYLOAD_BUILDERS

STATE = os.environ.get("MAAS_PERF_HOOKS_STATE", "full")
PAYLOAD = os.environ.get("MAAS_PERF_PAYLOAD", "sampledata")
NODE_COUNT = int(os.environ.get("MAAS_PERF_NODES", "10"))


def _make_payloads(count):
    builder = PAYLOAD_BUILDERS[PAYLOAD]
    return [
        json.dumps(builder(index).render()).encode("utf-8")
        for index in range(1, count + 1)
    ]


def _apply_state(monkeypatch):
    """Patch `hooks` for the selected state and return `process_lxd_results`."""
    if STATE == "full":
        return hooks.process_lxd_results

    if STATE == "base":
        monkeypatch.setattr(
            hooks, "_populate_hardware_profile", lambda node_id, data: None
        )
        return hooks.process_lxd_results

    if STATE == "build_only":

        def build_without_writing(node_id, data):
            from maasservicelayer.builders.hardwareprofile import (
                HardwareProfileBuilder,
            )

            HardwareProfileBuilder.from_commissioning_output(data, node_id)

        monkeypatch.setattr(
            hooks, "_populate_hardware_profile", build_without_writing
        )
        return hooks.process_lxd_results

    raise AssertionError(
        f"unknown MAAS_PERF_HOOKS_STATE {STATE!r}, "
        f"expected one of: base, full, build_only"
    )


@pytest.mark.allow_transactions
def test_perf_process_lxd_results_single_node(perf, factory, monkeypatch):
    process_lxd_results = _apply_state(monkeypatch)
    output = _make_payloads(1)[0]
    machine = factory.make_Machine()

    with perf.record(
        f"test_perf_process_lxd_results_single_node[{STATE}-{PAYLOAD}]"
    ):
        process_lxd_results(machine, output, 0)

    machine.refresh_from_db()
    assert machine.cpu_count > 0
    assert machine.physicalblockdevice_set.count() > 0


@pytest.mark.allow_transactions
def test_perf_process_lxd_results_many_nodes(perf, factory, monkeypatch):
    process_lxd_results = _apply_state(monkeypatch)
    outputs = _make_payloads(NODE_COUNT)
    machines = [factory.make_Machine() for _ in range(NODE_COUNT)]

    with perf.record(
        f"test_perf_process_lxd_results_many_nodes"
        f"[{STATE}-{PAYLOAD}-{NODE_COUNT}]"
    ):
        for machine, output in zip(machines, outputs, strict=True):
            process_lxd_results(machine, output, 0)

    processed = Machine.objects.filter(
        id__in=[machine.id for machine in machines], cpu_count__gt=0
    ).count()
    assert processed == NODE_COUNT
