# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Commissioning payload builders for the parsing performance tests.

Two shapes are produced:

``sampledata``
    Matches what ``src/maasserver/testing/sampledata`` seeds today: seven
    SATA disks with no serial/by-id information, four physical NICs plus a
    bridge, a VLAN and a bond, a single CPU socket and no GPUs.

``large-server``
    A realistic dual-socket accelerated server: 24 NVMe/SAS disks, eight of
    which are multipath LUNs reachable over two paths each, 8 NICs across
    4 cards, 4 GPUs, 2 sockets and 128 cores.
"""

from maasserver.testing.commissioning import (
    FakeCommissioningData,
    GB,
    LXDAddress,
)

MULTIPATH_LUNS = 8
MULTIPATH_PATHS_PER_LUN = 2


def _add_addresses(network, base):
    network.addresses = [LXDAddress(base, 24)]


def make_sampledata_info(index: int = 1) -> FakeCommissioningData:
    """Reproduce the payload `make_machine_infos` + sampledata produce."""
    info = FakeCommissioningData(
        server_name=f"perf-sampledata-{index:05}",
        kernel_architecture="x86_64",
        cores=(index % 10) + 1,
        memory=((index % 10) + 1) * 1024,
    )
    # sampledata/network.py: 4 VLANs per fabric -> physical + bridge,
    # physical + vlan, 2 physicals + bond, physical with an address.
    physical = info.create_physical_network()
    bridge = info.create_bridge_network(
        mac_address=physical.hwaddr, parents=[physical]
    )
    _add_addresses(bridge, "10.0.0.10")
    physical2 = info.create_physical_network()
    vlan_iface = info.create_vlan_network(vid=10, parent=physical2)
    _add_addresses(vlan_iface, "10.0.1.10")
    physical3 = info.create_physical_network()
    physical4 = info.create_physical_network()
    bond = info.create_bond_network(parents=[physical3, physical4])
    _add_addresses(bond, "10.0.2.10")
    # sampledata/storage.py adds sda..sdf on top of the default sda.
    for name in ("sda", "sdb", "sdc", "sdd", "sde", "sdf"):
        info.add_disk(name, size=1000 * GB)
    return info


def make_large_server_info(index: int = 1) -> FakeCommissioningData:
    """A many-disk, many-NIC, dual-socket, GPU-equipped server."""
    info = FakeCommissioningData(
        server_name=f"perf-large-{index:05}",
        kernel_architecture="x86_64",
        cores=128,
        memory=1024 * 1024,
        sockets=2,
        numa_nodes=2,
        socket_name="AMD EPYC 9554 64-Core Processor @ 3.10GHz",
        disks=[],
    )

    # Boot pair: NVMe, by-id links present.
    for slot in range(2):
        info.add_disk(
            f"nvme{slot}n1",
            size=1920 * GB,
            type="nvme",
            block_size=4096,
            serial=f"S6EUNA0T{slot:06}",
            device_id=f"nvme-SAMSUNG_MZQL21T9HCJR_S6EUNA0T{slot:06}",
            device_path=f"pci-0000:{0x40 + slot:02x}:00.0-nvme-1",
            model="SAMSUNG MZQL21T9HCJR-00A07",
            firmware_version="GDC5902Q",
            numa_node=slot,
        )

    # Local SAS spinners.
    for slot in range(6):
        info.add_disk(
            f"sd{chr(ord('a') + slot)}",
            size=8000 * GB,
            type="sas",
            rpm=7200,
            block_size=512,
            serial=f"5000C500{slot:08X}",
            device_id=f"scsi-35000c500{slot:08x}",
            device_path=f"pci-0000:c1:00.0-sas-phy{slot}-lun-0",
            model="ST8000NM017B",
            firmware_version="EN01",
            numa_node=slot % 2,
        )

    # Multipath fibre-channel LUNs: each LUN shows up once per path.
    letter = ord("g")
    for lun in range(MULTIPATH_LUNS):
        for path in range(MULTIPATH_PATHS_PER_LUN):
            info.add_disk(
                f"sd{chr(letter)}",
                size=2000 * GB,
                type="scsi",
                block_size=512,
                serial=f"3600a098038303{lun:05X}",
                # Only the first path exposes a by-id link, which is what
                # forces the model_copy branch in condense_luns().
                device_id=(
                    f"wwn-0x600a098038303{lun:05x}" if path == 0 else ""
                ),
                device_path=(
                    f"pci-0000:{0x81 + path:02x}:00.0-fc-0x5001438"
                    f"{path:07x}-lun-{lun}"
                ),
                model="NETAPP LUN C-Mode",
                firmware_version="9111",
                numa_node=lun % 2,
            )
            letter += 1

    # A read-only cdrom and a tiny loop device: both must be filtered out.
    info.add_disk("sr0", size=0, type="cdrom", read_only=True)
    info.add_disk("loop0", size=1024 * 1024, type="loop")

    # 4 dual-port NICs.
    for card_index in range(4):
        card = info.create_network_card()
        for _ in range(2):
            network = info.create_physical_network(card=card)
            _add_addresses(network, f"10.{card_index}.{len(info.networks)}.10")

    for _ in range(4):
        info.add_gpu_card()

    return info


PAYLOAD_BUILDERS = {
    "sampledata": make_sampledata_info,
    "large-server": make_large_server_info,
}
