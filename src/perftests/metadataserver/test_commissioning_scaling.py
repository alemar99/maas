# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""How the query count of `process_lxd_results` scales with machine size.

A real commissioning payload costs a few thousand queries, while the default
`FakeCommissioningData` costs a couple of hundred. The difference is entirely
in the per-item loops, so each test here scales exactly one attribute of the
machine and records the resulting query count.

Every parametrisation is measured against `BASELINE`, the smallest machine the
fake can describe, which lets the marginal cost of a single item be derived as
`(queries - baseline_queries) / count`.

    bin/pytest src/perftests/metadataserver/test_commissioning_scaling.py \\
        --perf-output-dir perf-tests-out

The per-item breakdown is written to `scaling.json` in the output directory.
"""

import json

import django
import pytest

from maasserver.testing.commissioning import (
    FakeCommissioningData,
    LXDAddress,
    LXDDisk,
)

django.setup()

from metadataserver.builtin_scripts import hooks  # noqa: E402

GB = 1000 * 1000 * 1000


class MachineShape:
    """The attributes of a fake machine that drive per-item database work."""

    def __init__(
        self,
        pci_devices=0,
        usb_devices=0,
        disks=1,
        interfaces=0,
        addresses_per_interface=0,
        cores=1,
    ):
        self.pci_devices = pci_devices
        self.usb_devices = usb_devices
        self.disks = disks
        self.interfaces = interfaces
        self.addresses_per_interface = addresses_per_interface
        self.cores = cores

    def replace(self, **changes):
        attributes = dict(vars(self))
        attributes.update(changes)
        return MachineShape(**attributes)

    def render(self):
        data = FakeCommissioningData(
            cores=self.cores,
            disks=[
                LXDDisk(f"sd{index:03d}", size=250 * GB)
                for index in range(self.disks)
            ],
        )
        for index in range(self.interfaces):
            network = data.create_physical_network(name=f"eth{index}")
            for address_index in range(self.addresses_per_interface):
                network.addresses.append(
                    LXDAddress(f"10.0.{index}.{10 + address_index}", 16)
                )
        for _ in range(self.pci_devices):
            data.create_pci_device(
                data.allocate_pci_address(),
                "1234",
                "My Corporation",
                "5678",
                "My PCI Device",
                "mydriver",
                "1.2.3",
            )
        for index in range(self.usb_devices):
            data.create_usb_device(
                f"{index // 128}:{index % 128}",
                "1234",
                "My Corporation",
                "5678",
                "My USB Device",
                "mydriver",
                "1.2.3",
            )
        return data.render()


def actual_counts(payload):
    """What the rendered payload really contains.

    `create_physical_network` implicitly adds a network card and a PCI device,
    so the requested shape is not what `process_lxd_results` sees. Reporting
    the rendered totals keeps the correlation honest.
    """
    resources = payload["resources"]
    return {
        "pci_devices": resources["pci"]["total"],
        "usb_devices": resources["usb"]["total"],
        "disks": resources["storage"]["total"],
        "network_cards": resources["network"]["total"],
        "networks": len(payload["networks"]),
        "addresses": sum(
            len(network["addresses"])
            for network in payload["networks"].values()
        ),
        "cores": resources["cpu"]["total"],
    }


BASELINE = MachineShape()

SCALES = {
    "pci_devices": [1, 8, 32, 128, 256],
    "usb_devices": [1, 8, 32, 128],
    "disks": [2, 8, 32, 128],
    "interfaces": [1, 2, 4, 8],
    "addresses_per_interface": [1, 2, 4],
    "cores": [8, 64, 256],
}

SCALED_SHAPES = [
    pytest.param(
        f"{attribute}={count}", attribute, count, id=f"{attribute}-{count}"
    )
    for attribute, counts in SCALES.items()
    for count in counts
]

# Addresses only exist on an interface, so that dimension needs a NIC.
ADDRESS_BASE = BASELINE.replace(interfaces=1)


@pytest.fixture(scope="session")
def scaling_report(perf, pytestconfig):
    """Collect one row per measured machine shape, keyed by record name."""
    rows = []
    yield rows
    outdir = pytestconfig.getoption("--perf-output-dir")
    report = json.dumps(rows, indent=2, sort_keys=True)
    if outdir is None:
        print("\n" + report)
    else:
        (perf.outdir / "scaling.json").write_text(report)


def measure(perf, factory, scaling_report, name, shape):
    machine = factory.make_Machine()
    payload = shape.render()
    encoded = json.dumps(payload).encode("utf-8")

    with perf.record(name):
        hooks.process_lxd_results(machine, encoded, 0)

    row = {"name": name, **actual_counts(payload)}
    row.update(perf.results["tests"][name])
    scaling_report.append(row)

    machine.refresh_from_db()
    assert machine.cpu_count == shape.cores


@pytest.mark.allow_transactions
def test_baseline(perf, factory, scaling_report):
    measure(perf, factory, scaling_report, "baseline", BASELINE)


@pytest.mark.allow_transactions
@pytest.mark.parametrize("name,attribute,count", SCALED_SHAPES)
def test_scaled_attribute(
    perf, factory, scaling_report, name, attribute, count
):
    base = ADDRESS_BASE if attribute == "addresses_per_interface" else BASELINE
    measure(
        perf, factory, scaling_report, name, base.replace(**{attribute: count})
    )
