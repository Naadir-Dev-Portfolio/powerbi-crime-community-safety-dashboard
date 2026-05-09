from __future__ import annotations

import argparse
import json
import math
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT_DIR / "Source Data" / "raw"
CURATED_DIR = ROOT_DIR / "Curated Data"
SUMMARY_PATH = CURATED_DIR / "model_summary.json"

ARCHIVE_PATH = RAW_DIR / "crime_archive_latest.zip"
FORCES_PATH = RAW_DIR / "police_forces.json"

UNKNOWN_PREFIX = "UNK-"
WALES_FORCE_IDS = {
    "dyfed-powys",
    "gwent",
    "north-wales",
    "south-wales",
}
EXCLUDED_FORCE_IDS = {
    "british-transport-police",
    "police-service-of-northern-ireland",
    "btp",
    "northern-ireland",
}
CRIME_GROUPS = {
    "anti-social behaviour": "Community harm",
    "bicycle theft": "Property crime",
    "burglary": "Property crime",
    "criminal damage and arson": "Property crime",
    "drugs": "Public order and policing",
    "other crime": "Other",
    "other theft": "Property crime",
    "possession of weapons": "Violence and threat",
    "public order": "Public order and policing",
    "robbery": "Violence and threat",
    "shoplifting": "Property crime",
    "theft from the person": "Property crime",
    "vehicle crime": "Property crime",
    "violence and sexual offences": "Violence and threat",
}
CRIME_SORT_ORDER = {
    "violence and sexual offences": 1,
    "anti-social behaviour": 2,
    "burglary": 3,
    "vehicle crime": 4,
    "criminal damage and arson": 5,
    "public order": 6,
    "other theft": 7,
    "shoplifting": 8,
    "robbery": 9,
    "drugs": 10,
    "bicycle theft": 11,
    "theft from the person": 12,
    "possession of weapons": 13,
    "other crime": 14,
}
CSV_COLUMNS = {
    "Month": "month",
    "Reported by": "reported_by",
    "Falls within": "falls_within",
    "Longitude": "longitude",
    "Latitude": "latitude",
    "Crime type": "crime_type",
    "Last outcome category": "last_outcome_category",
    "LSOA code": "lsoa_code",
    "LSOA name": "lsoa_name",
}


def repo_relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return path.name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build curated Power BI model tables from the official police archive."
    )
    parser.add_argument(
        "--archive",
        default=str(ARCHIVE_PATH),
        help="Path to the downloaded police archive zip.",
    )
    parser.add_argument(
        "--forces",
        default=str(FORCES_PATH),
        help="Path to the downloaded police forces json file.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite curated CSV outputs if they already exist.",
    )
    return parser.parse_args()


def slug_to_title(slug: str) -> str:
    cleaned = slug.replace("-", " ").strip()
    return re.sub(r"\s+", " ", cleaned).title()


def short_force_name(force_name: str) -> str:
    name = force_name
    replacements = [
        (" Constabulary", ""),
        (" Police Service", " Police"),
        (" Metropolitan Police Service", " Metropolitan Police"),
    ]
    for old, new in replacements:
        if old in name:
            name = name.replace(old, new)
    return name


def normalise_force_id_from_name(force_name: str) -> str:
    text = force_name.strip().lower()
    text = text.replace("&", "and")
    text = text.replace("constabulary", "")
    text = text.replace("police service", "police")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if text == "metropolitan-police":
        return "metropolitan"
    return text


def load_force_lookup(forces_path: Path) -> dict[str, str]:
    if not forces_path.exists():
        return {}
    payload = json.loads(forces_path.read_text(encoding="utf-8"))
    return {entry["id"]: entry["name"] for entry in payload}


def ensure_writable_output(overwrite: bool) -> None:
    CURATED_DIR.mkdir(parents=True, exist_ok=True)
    if overwrite:
        return
    for filename in [
        "dim_date.csv",
        "dim_crime_type.csv",
        "dim_force.csv",
        "dim_local_area.csv",
        "fact_crime_monthly.csv",
    ]:
        path = CURATED_DIR / filename
        if path.exists():
            raise FileExistsError(
                f"{path} already exists. Re-run with --overwrite to replace curated outputs."
            )


def build_date_dimension(date_keys: list[int]) -> pd.DataFrame:
    min_key = min(date_keys)
    max_key = max(date_keys)
    min_date = pd.Timestamp(f"{str(min_key)[:4]}-{str(min_key)[4:]}-01")
    max_date = pd.Timestamp(f"{str(max_key)[:4]}-{str(max_key)[4:]}-01")
    months = pd.date_range(start=min_date, end=max_date, freq="MS")

    frame = pd.DataFrame({"MonthStart": months})
    frame["DateKey"] = frame["MonthStart"].dt.strftime("%Y%m").astype(int)
    frame["Year"] = frame["MonthStart"].dt.year
    frame["MonthNumber"] = frame["MonthStart"].dt.month
    frame["MonthName"] = frame["MonthStart"].dt.strftime("%B")
    frame["MonthShort"] = frame["MonthStart"].dt.strftime("%b")
    frame["YearMonth"] = frame["MonthStart"].dt.strftime("%Y-%m")
    frame["QuarterNumber"] = frame["MonthStart"].dt.quarter
    frame["YearQuarter"] = (
        frame["MonthStart"].dt.year.astype(str)
        + " Q"
        + frame["MonthStart"].dt.quarter.astype(str)
    )
    frame["IsLatestMonth"] = frame["DateKey"].eq(max_key)
    frame["MonthsFromLatest"] = (
        (max_date.year - frame["MonthStart"].dt.year) * 12
        + (max_date.month - frame["MonthStart"].dt.month)
    )
    frame["MonthStart"] = frame["MonthStart"].dt.strftime("%Y-%m-%d")
    return frame[
        [
            "DateKey",
            "MonthStart",
            "Year",
            "MonthNumber",
            "MonthName",
            "MonthShort",
            "YearMonth",
            "QuarterNumber",
            "YearQuarter",
            "IsLatestMonth",
            "MonthsFromLatest",
        ]
    ]


def build_crime_type_dimension(crime_types: list[str]) -> pd.DataFrame:
    rows = []
    for index, crime_type in enumerate(sorted(crime_types), start=1):
        lower_name = crime_type.lower()
        rows.append(
            {
                "CrimeTypeKey": index,
                "CrimeType": crime_type,
                "CrimeGroup": CRIME_GROUPS.get(lower_name, "Other"),
                "CrimeSortOrder": CRIME_SORT_ORDER.get(lower_name, 99),
            }
        )
    frame = pd.DataFrame(rows).sort_values(
        by=["CrimeSortOrder", "CrimeType"], kind="stable"
    )
    frame["CrimeTypeKey"] = range(1, len(frame) + 1)
    return frame


def round_or_none(value: float | None) -> float | None:
    if value is None or math.isnan(value):
        return None
    return round(value, 6)


def main() -> None:
    args = parse_args()
    archive_path = Path(args.archive)
    forces_path = Path(args.forces)

    if not archive_path.exists():
        raise FileNotFoundError(f"Archive file not found: {archive_path}")

    ensure_writable_output(overwrite=args.overwrite)
    force_lookup = load_force_lookup(forces_path)

    fact_parts: list[pd.DataFrame] = []
    lsoa_stats: dict[str, dict[str, Any]] = {}
    force_names_seen: dict[str, str] = {}
    total_rows = 0

    with zipfile.ZipFile(archive_path) as archive:
        street_members = sorted(
            member for member in archive.namelist() if member.endswith("-street.csv")
        )

        for member in street_members:
            filename = Path(member).name
            match = re.match(r"(?P<month>\d{4}-\d{2})-(?P<force>.+)-street\.csv$", filename)
            if not match:
                continue

            month = match.group("month")
            force_id = match.group("force")
            if force_id in EXCLUDED_FORCE_IDS:
                continue

            default_force_name = force_lookup.get(force_id, slug_to_title(force_id))
            force_names_seen[force_id] = default_force_name

            with archive.open(member) as handle:
                chunks = pd.read_csv(
                    handle,
                    dtype=str,
                    chunksize=250_000,
                    keep_default_na=False,
                )
                for chunk in chunks:
                    total_rows += len(chunk.index)
                    chunk = chunk.rename(columns=CSV_COLUMNS)
                    chunk = chunk[[column for column in CSV_COLUMNS.values() if column in chunk.columns]]
                    chunk["month"] = month
                    chunk["force_id"] = force_id
                    chunk["force_name"] = chunk["reported_by"].replace("", pd.NA).fillna(default_force_name)
                    chunk["crime_type"] = chunk["crime_type"].replace("", "Unknown")
                    chunk["lsoa_code"] = chunk["lsoa_code"].str.strip()
                    chunk["lsoa_name"] = chunk["lsoa_name"].str.strip()

                    missing_area = chunk["lsoa_code"].eq("")
                    chunk.loc[missing_area, "lsoa_code"] = f"{UNKNOWN_PREFIX}{force_id.upper()}"
                    chunk.loc[missing_area, "lsoa_name"] = (
                        f"Location withheld / unknown ({default_force_name})"
                    )

                    grouped = (
                        chunk.groupby(
                            ["month", "force_id", "force_name", "lsoa_code", "lsoa_name", "crime_type"],
                            dropna=False,
                        )
                        .size()
                        .reset_index(name="CrimeCount")
                    )
                    fact_parts.append(grouped)

                    coords = chunk.loc[~chunk["lsoa_code"].str.startswith(UNKNOWN_PREFIX)].copy()
                    if coords.empty:
                        continue

                    coords["latitude"] = pd.to_numeric(coords["latitude"], errors="coerce")
                    coords["longitude"] = pd.to_numeric(coords["longitude"], errors="coerce")
                    coords = coords.dropna(subset=["latitude", "longitude"])
                    if coords.empty:
                        continue

                    coord_group = (
                        coords.groupby(["lsoa_code", "lsoa_name", "force_id", "force_name"], dropna=False)
                        .agg(
                            LatitudeSum=("latitude", "sum"),
                            LongitudeSum=("longitude", "sum"),
                            PointCount=("latitude", "size"),
                        )
                        .reset_index()
                    )

                    for row in coord_group.itertuples(index=False):
                        record = lsoa_stats.setdefault(
                            row.lsoa_code,
                            {
                                "LocalAreaCode": row.lsoa_code,
                                "LocalAreaName": row.lsoa_name,
                                "ForceId": row.force_id,
                                "ForceName": row.force_name,
                                "LatitudeSum": 0.0,
                                "LongitudeSum": 0.0,
                                "PointCount": 0,
                            },
                        )
                        record["LocalAreaName"] = row.lsoa_name or record["LocalAreaName"]
                        record["ForceId"] = row.force_id or record["ForceId"]
                        record["ForceName"] = row.force_name or record["ForceName"]
                        record["LatitudeSum"] += float(row.LatitudeSum)
                        record["LongitudeSum"] += float(row.LongitudeSum)
                        record["PointCount"] += int(row.PointCount)

    fact_df = pd.concat(fact_parts, ignore_index=True)
    fact_df = (
        fact_df.groupby(
            ["month", "force_id", "force_name", "lsoa_code", "lsoa_name", "crime_type"],
            as_index=False,
            dropna=False,
        )["CrimeCount"]
        .sum()
        .sort_values(by=["month", "force_id", "lsoa_code", "crime_type"], kind="stable")
    )

    fact_df["DateKey"] = fact_df["month"].str.replace("-", "", regex=False).astype(int)
    date_dim = build_date_dimension(sorted(fact_df["DateKey"].unique().tolist()))
    crime_type_dim = build_crime_type_dimension(sorted(fact_df["crime_type"].unique().tolist()))
    crime_type_key_map = dict(
        zip(crime_type_dim["CrimeType"], crime_type_dim["CrimeTypeKey"], strict=True)
    )

    local_area_rows: list[dict[str, Any]] = []
    for code, stats in sorted(lsoa_stats.items()):
        point_count = stats["PointCount"]
        latitude = stats["LatitudeSum"] / point_count if point_count else None
        longitude = stats["LongitudeSum"] / point_count if point_count else None
        local_area_rows.append(
            {
                "LocalAreaCode": code,
                "LocalAreaName": stats["LocalAreaName"],
                "ForceId": stats["ForceId"],
                "ForceName": stats["ForceName"],
                "LocalAreaType": "LSOA",
                "Latitude": round_or_none(latitude),
                "Longitude": round_or_none(longitude),
                "HasCoordinates": point_count > 0,
            }
        )

    unknown_areas = (
        fact_df.loc[fact_df["lsoa_code"].str.startswith(UNKNOWN_PREFIX), ["lsoa_code", "lsoa_name", "force_id", "force_name"]]
        .drop_duplicates()
        .sort_values(by=["force_id", "lsoa_code"], kind="stable")
    )
    for row in unknown_areas.itertuples(index=False):
        local_area_rows.append(
            {
                "LocalAreaCode": row.lsoa_code,
                "LocalAreaName": row.lsoa_name,
                "ForceId": row.force_id,
                "ForceName": row.force_name,
                "LocalAreaType": "Unknown / withheld",
                "Latitude": None,
                "Longitude": None,
                "HasCoordinates": False,
            }
        )

    local_area_dim = pd.DataFrame(local_area_rows).drop_duplicates(subset=["LocalAreaCode"])
    local_area_dim = local_area_dim.sort_values(
        by=["ForceId", "LocalAreaType", "LocalAreaName"], kind="stable"
    ).reset_index(drop=True)
    local_area_dim["LocalAreaKey"] = range(1, len(local_area_dim) + 1)

    force_centroids = (
        local_area_dim.loc[local_area_dim["HasCoordinates"]]
        .groupby(["ForceId", "ForceName"], as_index=False, dropna=False)
        .agg(
            Latitude=("Latitude", "mean"),
            Longitude=("Longitude", "mean"),
        )
    )
    force_rows: list[dict[str, Any]] = []
    if force_lookup:
        for force_id, force_name in sorted(force_lookup.items()):
            if force_id in EXCLUDED_FORCE_IDS:
                continue
            force_rows.append({"ForceId": force_id, "ForceName": force_name})
    else:
        force_rows.extend(
            fact_df[["force_id", "force_name"]]
            .drop_duplicates()
            .rename(columns={"force_id": "ForceId", "force_name": "ForceName"})
            .to_dict("records")
        )

    force_dim = (
        pd.DataFrame(force_rows)
        .drop_duplicates(subset=["ForceId"])
        .sort_values(by=["ForceName"], kind="stable")
        .reset_index(drop=True)
    )
    force_dim["ForceShortName"] = force_dim["ForceName"].map(short_force_name)
    force_dim["Country"] = force_dim["ForceId"].map(
        lambda force_id: "Wales" if force_id in WALES_FORCE_IDS else "England"
    )
    force_dim = force_dim.merge(force_centroids, how="left", on=["ForceId", "ForceName"])
    force_dim["Latitude"] = force_dim["Latitude"].map(round_or_none)
    force_dim["Longitude"] = force_dim["Longitude"].map(round_or_none)
    force_dim["ForceKey"] = range(1, len(force_dim) + 1)

    force_key_map = dict(zip(force_dim["ForceId"], force_dim["ForceKey"], strict=True))
    local_area_key_map = dict(
        zip(local_area_dim["LocalAreaCode"], local_area_dim["LocalAreaKey"], strict=True)
    )

    fact_df["CrimeTypeKey"] = fact_df["crime_type"].map(crime_type_key_map)
    fact_df["ForceKey"] = fact_df["force_id"].map(force_key_map)
    fact_df["LocalAreaKey"] = fact_df["lsoa_code"].map(local_area_key_map)
    fact_df = fact_df.rename(
        columns={
            "month": "YearMonth",
            "crime_type": "CrimeType",
            "force_id": "ForceId",
            "force_name": "ForceName",
            "lsoa_code": "LocalAreaCode",
            "lsoa_name": "LocalAreaName",
        }
    )
    fact_df = fact_df[
        [
            "DateKey",
            "CrimeTypeKey",
            "ForceKey",
            "LocalAreaKey",
            "CrimeCount",
        ]
    ].sort_values(by=["DateKey", "ForceKey", "LocalAreaKey", "CrimeTypeKey"], kind="stable")

    local_area_dim = local_area_dim.merge(
        force_dim[["ForceId", "ForceKey"]],
        how="left",
        on="ForceId",
    )
    local_area_dim["AreaLabel"] = local_area_dim["LocalAreaName"] + " | " + local_area_dim["ForceName"]
    local_area_dim = local_area_dim[
        [
            "LocalAreaKey",
            "LocalAreaCode",
            "LocalAreaName",
            "AreaLabel",
            "LocalAreaType",
            "ForceKey",
            "ForceId",
            "ForceName",
            "Latitude",
            "Longitude",
            "HasCoordinates",
        ]
    ]
    force_dim = force_dim[
        [
            "ForceKey",
            "ForceId",
            "ForceName",
            "ForceShortName",
            "Country",
            "Latitude",
            "Longitude",
        ]
    ]

    date_dim.to_csv(CURATED_DIR / "dim_date.csv", index=False)
    crime_type_dim.to_csv(CURATED_DIR / "dim_crime_type.csv", index=False)
    force_dim.to_csv(CURATED_DIR / "dim_force.csv", index=False)
    local_area_dim.to_csv(CURATED_DIR / "dim_local_area.csv", index=False)
    fact_df.to_csv(CURATED_DIR / "fact_crime_monthly.csv", index=False)

    summary = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "source_archive": repo_relative_path(archive_path),
        "input_rows_processed": total_rows,
        "date_range": {
            "min_date_key": int(date_dim["DateKey"].min()),
            "max_date_key": int(date_dim["DateKey"].max()),
            "min_month_start": str(date_dim["MonthStart"].min()),
            "max_month_start": str(date_dim["MonthStart"].max()),
        },
        "tables": {
            "dim_date_rows": int(len(date_dim.index)),
            "dim_crime_type_rows": int(len(crime_type_dim.index)),
            "dim_force_rows": int(len(force_dim.index)),
            "dim_local_area_rows": int(len(local_area_dim.index)),
            "fact_crime_monthly_rows": int(len(fact_df.index)),
        },
        "coverage_note": "Detailed local monthly coverage is constrained by the current rolling 36-month police archive.",
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"Curated data written to {CURATED_DIR}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
