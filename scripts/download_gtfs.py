#!/usr/bin/env python3
"""
Download a GTFS feed from the 511 Open Data API.

Examples
--------
# Historic regional feed for August 2025, WITH stop_observations.txt
# (this is what you want first for delay analysis)
python scripts/download_gtfs.py --historic 2025-08 --with-observations

# Current/active regional static GTFS (no observed times)
python scripts/download_gtfs.py --current

# A single operator instead of the whole region (operator IDs come from
# https://api.511.org/transit/gtfsoperators?api_key=...)
python scripts/download_gtfs.py --current --operator SF
"""
import argparse
import os
import sys
import zipfile
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://api.511.org/transit/datafeeds"
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def build_params(api_key: str, operator: str, historic: str | None, with_obs: bool) -> dict:
    params = {"api_key": api_key, "operator_id": operator}
    if historic:
        params["historic"] = f"{historic}-so" if with_obs else historic
    return params


def download(params: dict, out_name: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    resp = requests.get(API_BASE, params=params, timeout=120)
    resp.raise_for_status()
    ctype = resp.headers.get("Content-Type", "")
    if "zip" not in ctype and not resp.content[:2] == b"PK":
        # 511 returns XML/JSON error bodies with HTTP 200 on bad requests
        sys.exit(f"Expected a zip file but got Content-Type={ctype!r}. "
                 f"Response body (truncated): {resp.text[:500]}")
    zip_path = RAW_DIR / f"{out_name}.zip"
    zip_path.write_bytes(resp.content)
    return zip_path


def extract(zip_path: Path) -> Path:
    dest = zip_path.with_suffix("")  # strip .zip
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    return dest


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--historic", metavar="YYYY-MM", help="Historic monthly feed, e.g. 2025-08")
    group.add_argument("--current", action="store_true", help="Current active feed")
    ap.add_argument("--with-observations", action="store_true",
                     help="Include stop_observations.txt (only valid with --historic)")
    ap.add_argument("--operator", default="RG",
                     help="Operator ID, default RG = consolidated regional feed")
    args = ap.parse_args()

    if args.with_observations and not args.historic:
        ap.error("--with-observations requires --historic")

    api_key = os.environ.get("MTC_511_API_KEY")
    if not api_key or api_key == "your_key_here":
        sys.exit("Set MTC_511_API_KEY in your .env first. "
                  "Request a key at https://511.org/open-data/token")

    params = build_params(api_key, args.operator, args.historic, args.with_observations)
    label = args.historic or "current"
    suffix = "_so" if args.with_observations else ""
    out_name = f"{args.operator}_{label}{suffix}"

    print(f"Downloading {out_name} ...")
    zip_path = download(params, out_name)
    print(f"Saved {zip_path}")
    dest = extract(zip_path)
    print(f"Extracted to {dest}")
    print("Next: python scripts/load_gtfs.py --path", dest)


if __name__ == "__main__":
    main()
