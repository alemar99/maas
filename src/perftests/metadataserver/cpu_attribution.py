# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""CPU-only attribution of commissioning parsing cost.

`process_lxd_results` is dominated by database work, which makes the parsing
cost invisible in the end-to-end perf test. This script measures the parsing
stages on their own, with no database involved, so the two can be reasoned
about separately.

It covers both parses that the commissioning path performs today:

* the dictionary walking in `metadataserver.builtin_scripts.hooks` and
  `provisioningserver.utils.lxd`, which populates nodes, interfaces and block
  devices;
* the Pydantic `MachineResources` parse in
  `maasservicelayer.builders.hardwareprofile`, which derives the hardware
  profile from the same payload.

Run from the repo root:

    DJANGO_SETTINGS_MODULE=maasserver.djangosettings.development \\
        bin/py src/perftests/metadataserver/cpu_attribution.py [output.json]

Like the perf tests, this only records numbers. Nothing here passes or fails.
"""

import json
import statistics
import sys
import time

import django

django.setup()

from maasservicelayer.builders.hardwareprofile import (  # noqa: E402
    HardwareProfileBuilder,
)
from maasservicelayer.utils import lxd_parsing  # noqa: E402
from maasservicelayer.utils.lxd import MachineResources  # noqa: E402
from metadataserver.builtin_scripts import hooks  # noqa: E402
from perftests.metadataserver.payloads import PAYLOAD_BUILDERS  # noqa: E402
from provisioningserver.utils import lxd as provisioning_lxd  # noqa: E402

REPEATS = 2000


def bench(fn, repeats=REPEATS):
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
            (
                device["vendor_id"],
                device["product_id"],
                device["pci_address"],
            )
        for device in resources.get("usb", {}).get("devices", []):
            usb_address = "{}:{}".format(
                device["bus_address"], device["device_address"]
            )
            (device["vendor_id"], device["product_id"], usb_address)
        for card in resources.get("gpu", {}).get("cards", []):
            card.get("pci_address") or card.get("usb_address")

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


def payload_shape(data, output):
    resources = data["resources"]
    return {
        "json_bytes": len(output),
        "disks": len(resources["storage"]["disks"]),
        "network_cards": len(resources["network"]["cards"]),
        "network_ports": sum(
            len(card.get("ports") or [])
            for card in resources["network"]["cards"]
        ),
        "gpu_cards": len(resources["gpu"]["cards"]),
        "pci_devices": len(resources["pci"]["devices"]),
        "usb_devices": len(resources["usb"]["devices"]),
        "networks": len(data["networks"]),
        "cpu_sockets": len(resources["cpu"]["sockets"]),
        "cpu_cores": resources["cpu"]["total"],
    }


def main():
    results = {}
    for payload_name, builder in sorted(PAYLOAD_BUILDERS.items()):
        data = builder(1).render()
        output = json.dumps(data).encode("utf-8")

        stages = {}
        stages.update(json_stage(output))
        stages.update(dict_stages(data))
        stages.update(pydantic_stages(data))

        payload_results = {}
        for name, fn in stages.items():
            median, p90, stdev = bench(fn)
            payload_results[name] = {
                "median_us": round(median, 2),
                "p90_us": round(p90, 2),
                "stdev_us": round(stdev, 2),
            }
            print(
                f"{payload_name:<14} {name:<50} "
                f"median={median:8.2f}us p90={p90:8.2f}us "
                f"stdev={stdev:7.2f}us"
            )
        payload_results["_payload_shape"] = payload_shape(data, output)
        results[payload_name] = payload_results
        print()

    if len(sys.argv) > 1:
        with open(sys.argv[1], "w") as fh:
            json.dump(results, fh, indent=2, sort_keys=True)
        print(f"wrote {sys.argv[1]}")


if __name__ == "__main__":
    main()
