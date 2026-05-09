# Source Data

This folder contains the ingestion pipeline and the locally downloaded official source files for the crime and community safety dashboard.

## Official sources used

- `https://data.police.uk/data/archive/latest.zip`
  - Official rolling archive of monthly street-level crime and ASB data.
  - As downloaded for this project on 23 April 2026, the archive covers March 2023 to February 2026.
- `https://data.police.uk/api/forces`
  - Official force reference list used to keep the England and Wales force dimension complete.
- `https://data.police.uk/data/boundaries/`
  - Official police force boundary KML source. The raw KML is stored locally for future shape-map work.

## Scripts

- `scripts/download_source_data.py`
  - Downloads the raw archive, force list JSON, and force boundary KML into `Source Data/raw`.
- `scripts/build_curated_layer.py`
  - Aggregates the raw archive into Power BI model-ready CSV files in `Curated Data`.
- `scripts/run_pipeline.py`
  - Convenience wrapper that runs both steps with overwrite enabled.

## Rebuild commands

```powershell
python "Source Data\scripts\download_source_data.py" --overwrite
python "Source Data\scripts\build_curated_layer.py" --overwrite
```

## Notes

- The detailed police archive is currently a rolling 36-month source, so this project's local-area monthly history runs from `2023-03-01` to `2026-02-01`.
- The current archive does not contain Greater Manchester rows, even though the official force list still includes the force. The semantic model keeps Greater Manchester in the force dimension with blank measures where the archive has no facts.
