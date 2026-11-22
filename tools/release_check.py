"""Run a two-version comparison and write the evidence for release review."""
import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import BadZipFile

import footprint as fp

ROOT = Path(__file__).resolve().parents[1]


def emit(path, result):
    path.write_text(json.dumps(result, indent=2) + "\n")


def compare(args):
    reports = args.output
    reports.mkdir(parents=True, exist_ok=True)
    if args.mode == "aar":
        before, after = fp.aar_info(Path(args.baseline)), fp.aar_info(Path(args.candidate))
        growth = {"AAR": after["aar_bytes"] - before["aar_bytes"]}
        detail = {"growth_bytes": growth}
    else:
        def measure(source, pom):
            if pom:
                return fp.benchmark_local(ROOT, Path(source), Path(pom), False, args.profile)
            return fp.benchmark(ROOT, source, False, args.profile)

        before = measure(args.baseline, args.baseline_pom)
        emit(reports / "baseline.json", before)
        after = measure(args.candidate, args.candidate_pom)
        emit(reports / "candidate.json", after)
        changes = fp.changes(before, after)
        growth = {host: change["apk_bytes"] for host, change in changes.items()}
        detail = {"growth": changes}
    emit(reports / "baseline.json", before)
    emit(reports / "candidate.json", after)
    over = [name for name, delta in growth.items() if delta > args.budget]
    detail.update(budget_bytes=args.budget, over_budget=over)
    emit(reports / "growth.json", detail)
    decision = "Over budget" if over else "Within budget"
    lines = [f"## SDK footprint — {decision}", "", "| Measurement | Growth (bytes) | Budget (bytes) |",
             "|---|---:|---:|"]
    lines.extend(f"| {name} | {value:+,} | {args.budget:,} |" for name, value in growth.items())
    summary = "\n".join(lines) + "\n"
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as out:
            out.write(summary)
    return int(bool(over))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("aar", "apk"), required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--baseline-pom")
    parser.add_argument("--candidate-pom")
    parser.add_argument("--profile", type=Path, default=ROOT / "profiles/exif/profile.json")
    parser.add_argument("--budget", type=fp.nonnegative, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        sys.exit(compare(args))
    except (OSError, BadZipFile, ValueError, KeyError, TypeError, ET.ParseError, subprocess.CalledProcessError) as error:
        sys.exit(parser.exit(2, f"Comparison incomplete: {error}\n"))
