from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests


ROOT_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT_DIR / "Source Data" / "raw"
MANIFEST_PATH = RAW_DIR / "source_manifest.json"

CRIME_ARCHIVE_URL = "https://data.police.uk/data/archive/latest.zip"
FORCES_URL = "https://data.police.uk/api/forces"
BOUNDARIES_PAGE_URL = "https://data.police.uk/data/boundaries/"
USER_AGENT = "crime-community-safety-dashboard/1.0"


def repo_relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return path.name


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_text(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response.text


def discover_force_boundaries_url(session: requests.Session) -> str | None:
    html = fetch_text(session, BOUNDARIES_PAGE_URL)
    match = re.search(r'href="([^"]+)"[^>]*>\s*Force boundaries\s*<', html, re.IGNORECASE)
    if not match:
        return None
    return urljoin(BOUNDARIES_PAGE_URL, match.group(1))


def download_to_path(
    session: requests.Session,
    url: str,
    destination: Path,
    overwrite: bool,
) -> dict[str, Any]:
    if destination.exists() and not overwrite:
        return {
            "path": repo_relative_path(destination),
            "url": url,
            "downloaded": False,
            "size_bytes": destination.stat().st_size,
            "sha256": sha256_file(destination),
            "updated_utc": datetime.now(UTC).isoformat(),
        }

    response = session.get(url, stream=True, timeout=120)
    response.raise_for_status()
    destination.parent.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    with destination.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            handle.write(chunk)
            digest.update(chunk)

    return {
        "path": repo_relative_path(destination),
        "url": url,
        "downloaded": True,
        "size_bytes": destination.stat().st_size,
        "sha256": digest.hexdigest(),
        "updated_utc": datetime.now(UTC).isoformat(),
    }


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    session.trust_env = False
    return session


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download official source files for the crime dashboard project."
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Redownload files even if they already exist.",
    )
    parser.add_argument(
        "--skip-boundaries",
        action="store_true",
        help="Skip the optional police force boundaries KML download.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    session = build_session()
    manifest: dict[str, Any] = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "sources": {},
    }

    manifest["sources"]["crime_archive"] = download_to_path(
        session=session,
        url=CRIME_ARCHIVE_URL,
        destination=RAW_DIR / "crime_archive_latest.zip",
        overwrite=args.overwrite,
    )

    forces_response = session.get(FORCES_URL, timeout=60)
    forces_response.raise_for_status()
    force_path = RAW_DIR / "police_forces.json"
    force_path.write_text(forces_response.text + "\n", encoding="utf-8")
    manifest["sources"]["police_forces"] = {
        "path": repo_relative_path(force_path),
        "url": FORCES_URL,
        "downloaded": True,
        "size_bytes": force_path.stat().st_size,
        "sha256": sha256_file(force_path),
        "updated_utc": datetime.now(UTC).isoformat(),
    }

    if not args.skip_boundaries:
        boundaries_url = discover_force_boundaries_url(session)
        if boundaries_url:
            manifest["sources"]["force_boundaries"] = download_to_path(
                session=session,
                url=boundaries_url,
                destination=RAW_DIR / "police_force_boundaries.kml",
                overwrite=args.overwrite,
            )
        else:
            manifest["sources"]["force_boundaries"] = {
                "path": repo_relative_path(RAW_DIR / "police_force_boundaries.kml"),
                "url": None,
                "downloaded": False,
                "warning": "Force boundaries link could not be discovered from the official page.",
                "updated_utc": datetime.now(UTC).isoformat(),
            }

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote manifest to {repo_relative_path(MANIFEST_PATH)}")
    for source_name, details in manifest["sources"].items():
        print(f"{source_name}: {details.get('path')}")


if __name__ == "__main__":
    main()
