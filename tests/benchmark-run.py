#!/usr/bin/env python3
"""Time fresh and updated native payloads on an enrolled disposable host."""

import argparse
import csv
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=15)
    args = parser.parse_args()
    if not os.environ.get("CATCH_HOST") or args.samples < 1:
        parser.error("set CATCH_HOST and choose a positive sample count")

    client = os.environ.get("YEET_CLI", "yeet")
    prefix = "bench-" + secrets.token_hex(4)
    writer = csv.writer(sys.stdout)
    writer.writerow(["sample", "case", "seconds"])
    with tempfile.TemporaryDirectory() as work:

        def cli(*argv):
            result = subprocess.run(
                [client, *argv],
                cwd=work,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            if result.returncode:
                raise RuntimeError(result.stdout)
            return result.stdout.strip()

        path = Path(work, "worker.sh")
        for index in range(-2, args.samples):
            service = f"{prefix}-{index + 2}"
            try:
                for case in ("create", "update"):
                    marker = f"{case}{index}"
                    path.write_text(
                        f'#!/bin/sh\necho {marker} > "$HOME/bench-proof"\nexec sleep 86400\n'
                    )
                    path.chmod(0o755)
                    start = time.perf_counter()
                    cli("run", service, "./worker.sh")
                    elapsed = time.perf_counter() - start
                    for attempt in range(20):
                        if (
                            cli("--no-tty", "ssh", service, "--", "cat", "bench-proof")
                            == marker
                        ):
                            break
                        time.sleep(0.1)
                    else:
                        raise RuntimeError(f"{service} did not produce {marker}")
                    if index >= 0:
                        writer.writerow([index, case, f"{elapsed:.6f}"])
                        sys.stdout.flush()
            finally:
                cli("rm", "--clean", "-y", service)


if __name__ == "__main__":
    main()
