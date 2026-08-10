# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Load commissioning output payloads from a directory of JSON files.

Used by `test_commissioning_hooks.py` when `--commissioning-data-dir` is
given, so the perf test can run against captured `50-maas-01-commissioning`
output instead of generated data. One file is one machine.

Nothing here generates or repairs data. A file that cannot be read, cannot be
decoded, or is not a JSON object is an error naming that file, because
quietly dropping it would shrink the measured set without anyone noticing.
"""

import json
from pathlib import Path


class CommissioningCorpusError(Exception):
    """The supplied commissioning data directory cannot be used as given."""


class CommissioningPayload:
    """One machine's commissioning output, as read from disk.

    `output` is the raw bytes, passed to `process_lxd_results` unchanged so
    that the JSON decoding it performs is measured exactly as it is in
    production.
    """

    def __init__(self, path: Path, output: bytes):
        self.path = path
        self.output = output

    @property
    def name(self) -> str:
        return self.path.name

    def __repr__(self) -> str:
        return f"<CommissioningPayload {self.path}>"


def load_corpus(data_dir: Path) -> list[CommissioningPayload]:
    """Return one payload per `*.json` file in `data_dir`, sorted by name.

    Sorted so that repeated runs process the same payloads in the same order
    and are therefore comparable.
    """
    if not data_dir.exists():
        raise CommissioningCorpusError(
            f"commissioning data directory does not exist: {data_dir}"
        )
    if not data_dir.is_dir():
        raise CommissioningCorpusError(
            f"commissioning data directory is not a directory: {data_dir}"
        )

    paths = sorted(data_dir.glob("*.json"))
    if not paths:
        raise CommissioningCorpusError(
            f"no *.json commissioning payloads found in {data_dir}"
        )

    payloads = []
    for path in paths:
        try:
            output = path.read_bytes()
        except OSError as error:
            raise CommissioningCorpusError(
                f"cannot read commissioning payload {path}: {error}"
            ) from error

        # Decoded here purely to fail before anything is measured. The bytes,
        # not this result, are what gets processed.
        try:
            decoded = json.loads(output.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise CommissioningCorpusError(
                f"commissioning payload {path} is not valid JSON: {error}"
            ) from error

        if not isinstance(decoded, dict):
            raise CommissioningCorpusError(
                f"commissioning payload {path} is not a JSON object, "
                f"got {type(decoded).__name__}"
            )

        payloads.append(CommissioningPayload(path, output))

    return payloads
