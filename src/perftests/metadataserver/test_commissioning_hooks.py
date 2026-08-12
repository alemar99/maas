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
"""

import json
import statistics
import time

import pytest

from maasservicelayer.builders.hardwareprofile import HardwareProfileBuilder
from maasservicelayer.utils import lxd_parsing
from maasservicelayer.utils.lxd import MachineResources
from metadataserver.builtin_scripts import hooks
from provisioningserver.utils import lxd as provisioning_lxd


def bench(fn, repeats=2000):
    """Return (median_us, p90_us, stdev_us) over `repeats` calls."""
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1e6)
    samples.sort()
    return (
        statistics.median(samples),
        samples[int(0.9 * len(samples))],
        statistics.stdev(samples),
    )


def json_stage(output):
    def loads():
        json.loads(output.decode("utf-8"))

    return {"json.loads": loads}


def dict_stages(data):
    """The pure-Python parsing `hooks.py` performs today, stage by stage."""
    resources = data["resources"]

    def cpu():
        provisioning_lxd.parse_lxd_cpuinfo(resources)

    def networks_state():
        provisioning_lxd.parse_lxd_networks(data["networks"])

    def storage_condense():
        # _condense_luns mutates its input, so hand it a fresh copy.
        hooks._condense_luns([dict(d) for d in resources["storage"]["disks"]])

    def storage_condense_and_fields():
        for disk in hooks._condense_luns(
            [dict(d) for d in resources["storage"]["disks"]]
        ):
            if disk["read_only"] or disk["type"] == "cdrom":
                continue
            if hooks._is_virtual_bcache_holder(disk):
                continue
            id_path = disk.get("device_id", "")
            if id_path:
                id_path = f"/dev/disk/by-id/{id_path}"
            if not id_path or not disk.get("serial"):
                id_path = "/dev/" + disk.get("id")
            hooks._get_tags_from_block_info(disk)

    def node_devices():
        for device in resources.get("pci", {}).get("devices", []):
            _, _, _ = (
                device["vendor_id"],
                device["product_id"],
                device["pci_address"],
            )
        for device in resources.get("usb", {}).get("devices", []):
            usb_address = "{}:{}".format(
                device["bus_address"], device["device_address"]
            )
            _ = (device["vendor_id"], device["product_id"], usb_address)
        for card in resources.get("gpu", {}).get("cards", []):
            _ = card.get("pci_address") or card.get("usb_address")

    return {
        "dict: cpu (parse_lxd_cpuinfo)": cpu,
        "dict: networks state (parse_lxd_networks)": networks_state,
        "dict: storage condense_luns": storage_condense,
        "dict: storage condense+fields": storage_condense_and_fields,
        "dict: node devices key build": node_devices,
    }


def pydantic_stages(data):
    """The Pydantic parse that derives the hardware profile."""
    machine_resources = MachineResources(**data)
    resources = machine_resources.resources

    def construct():
        MachineResources(**data)

    def construct_without_validation():
        # Does not recurse, so the nested values stay dicts. Measured only to
        # show how much of the cost above is validation.
        MachineResources.model_construct(**data)

    def helpers_storage():
        lxd_parsing.parse_storage(resources.storage)

    def helpers_network():
        lxd_parsing.parse_network(resources.network)

    def helpers_accelerators():
        lxd_parsing.parse_accelerators(resources.gpu)

    def helpers_cpu_memory_architecture():
        lxd_parsing.parse_cpu(resources.cpu)
        lxd_parsing.parse_memory_mb(resources.memory)
        lxd_parsing.parse_architecture(machine_resources)

    def from_commissioning_output():
        HardwareProfileBuilder.from_commissioning_output(data, 1)

    return {
        "pydantic: MachineResources(**data)": construct,
        "pydantic: model_construct (no validation)": (
            construct_without_validation
        ),
        "pydantic: parse_storage helper": helpers_storage,
        "pydantic: parse_network helper": helpers_network,
        "pydantic: parse_accelerators helper": helpers_accelerators,
        "pydantic: parse_cpu/memory/architecture helpers": (
            helpers_cpu_memory_architecture
        ),
        "pydantic: from_commissioning_output (parse+build)": (
            from_commissioning_output
        ),
    }


@pytest.fixture(scope="session")
def commissioning_payloads(pytestconfig):
    """Real commissioning output, one encoded payload per JSON file."""
    data_dir = pytestconfig.getoption("--commissioning-data-dir")
    if not data_dir:
        pytest.skip("--commissioning-data-dir was not given")
    paths = sorted(data_dir.glob("*.json"))
    assert paths, f"no *.json commissioning payloads in {data_dir}"
    return [path.read_bytes() for path in paths]


@pytest.fixture
def cpu_time_outfile(pytestconfig):
    """Real commissioning output, one encoded payload per JSON file."""
    file = pytestconfig.getoption("--cpu-time-outfile")
    if not file:
        pytest.skip("--cpu-time-outfile was not given")
    return file


def test_cpu_time(cpu_time_outfile, commissioning_payloads):
    results = []
    for payload in commissioning_payloads:
        json_payload = json.loads(payload)

        stages = {}
        stages.update(json_stage(payload))
        stages.update(dict_stages(json_payload))
        stages.update(pydantic_stages(json_payload))

        payload_results = {}
        for name, fn in stages.items():
            median, p90, stdev = bench(fn)
            payload_results[name] = {
                "median_us": round(median, 2),
                "p90_us": round(p90, 2),
                "stdev_us": round(stdev, 2),
            }
        results.append(payload_results)

    with open(cpu_time_outfile, "w") as fh:
        json.dump(results, fh, indent=2, sort_keys=True)
