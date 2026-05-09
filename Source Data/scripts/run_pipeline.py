from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def run_step(script_name: str, *extra_args: str) -> None:
    command = [sys.executable, str(SCRIPT_DIR / script_name), *extra_args]
    print("Running:", " ".join(command))
    subprocess.run(command, check=True)


def main() -> None:
    shared_args = ["--overwrite"]
    run_step("download_source_data.py", *shared_args)
    run_step("build_curated_layer.py", *shared_args)


if __name__ == "__main__":
    main()
