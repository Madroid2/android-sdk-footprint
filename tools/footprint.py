"""Measure APK payloads and compare the marginal cost of an Android SDK integration."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from zipfile import BadZipFile, ZipFile

DEX = re.compile(r"classes(?:[2-9]|[1-9]\d+)?\.dex$")
NATIVE = re.compile(r"(?:lib|jni)/([^/]+)/([^/]+\.so)$")
HOSTS = ("lean", "compose")
GROUPS = ("dex", "native", "resources", "assets", "other_entries", "archive_overhead")


def digest(stream):
    sha = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        sha.update(chunk)
    return sha.hexdigest()


def bucket(name):
    if DEX.fullmatch(name):
        return "dex"
    if name.startswith("lib/") and NATIVE.fullmatch(name):
        return "native"
    if name == "resources.arsc" or name.startswith("res/"):
        return "resources"
    if name.startswith("assets/"):
        return "assets"
    return "other_entries"


def native_sources(aars):
    sources = defaultdict(set)
    for path in aars:
        with ZipFile(path) as archive:
            for item in archive.infolist():
                match = NATIVE.fullmatch(item.filename)
                if match and item.filename.startswith("jni/"):
                    with archive.open(item) as stream:
                        sources[(*match.groups(), digest(stream))].add(path.name)
    return sources


def inspect_apk(path, sources=None):
    groups = Counter(dict.fromkeys(GROUPS, 0))
    native = []
    with ZipFile(path) as apk:
        names = set(apk.namelist())
        if "AndroidManifest.xml" not in names or "classes.dex" not in names:
            raise ValueError(f"{path.name} must be a standalone APK with code and a manifest")
        if len(names) != len(apk.infolist()):
            raise ValueError(f"{path.name} has duplicate ZIP entries")
        for item in apk.infolist():
            groups[bucket(item.filename)] += item.compress_size
            match = NATIVE.fullmatch(item.filename)
            if match and item.filename.startswith("lib/"):
                abi, name = match.groups()
                with apk.open(item) as stream:
                    sha = digest(stream)
                native.append(dict(abi=abi, library=name, uncompressed_bytes=item.file_size,
                                   packaged_bytes=item.compress_size, sha256=sha,
                                   matching_aars=sorted((sources or {}).get((abi, name, sha), []))))
    size = path.stat().st_size
    groups["archive_overhead"] = size - sum(groups.values())
    with path.open("rb") as stream:
        return dict(file=path.name, sha256=digest(stream), apk_bytes=size,
                    packaged_bytes=dict(groups), native=sorted(native, key=lambda row: (row["abi"], row["library"])))


def difference(control, treatment):
    def by_abi(report):
        if "native_bytes_by_abi" in report:
            return report["native_bytes_by_abi"]
        result = Counter()
        for library in report["native"]:
            result[library["abi"]] += library["packaged_bytes"]
        return result

    before, after = by_abi(control), by_abi(treatment)
    abi_delta = {abi: after.get(abi, 0) - before.get(abi, 0) for abi in sorted(before.keys() | after.keys())}
    return dict(apk_bytes=treatment["apk_bytes"] - control["apk_bytes"], native_bytes_by_abi=abi_delta,
                packaged_bytes={k: treatment["packaged_bytes"][k] - control["packaged_bytes"][k]
                                for k in GROUPS})


def variant_apk(outputs, host, integration):
    variant = host + integration.capitalize()
    matches = []
    for path in outputs.rglob("output-metadata.json"):
        metadata = json.loads(path.read_text())
        if metadata.get("variantName") == variant + "Release":
            matches.append((path.parent, metadata))
    if len(matches) != 1:
        raise ValueError(f"Expected one {variant}Release output under {outputs}, found {len(matches)}")
    folder, metadata = matches[0]
    elements = metadata["elements"]
    if len(elements) != 1 or elements[0]["filters"]:
        raise ValueError(f"{variant} requires one unsplit release APK")
    return inspect_apk(folder / elements[0]["outputFile"])


def build_matrix(outputs):
    hosts = {}
    for host in HOSTS:
        control = variant_apk(outputs, host, "control")
        treatment = variant_apk(outputs, host, "integrated")
        hosts[host] = dict(control=control, integrated=treatment, incremental=difference(control, treatment))
    return dict(schema=1, hosts=hosts)


def dependencies_changed(before, after):
    return {key: {"from": before.get(key), "to": after.get(key)}
            for key in sorted(before.keys() | after.keys()) if before.get(key) != after.get(key)}


def aar_info(path):
    with ZipFile(path) as archive:
        if path.suffix.lower() != ".aar" or "AndroidManifest.xml" not in archive.namelist():
            raise ValueError(f"{path.name} must be an Android library archive (.aar)")
    with path.open("rb") as stream:
        return dict(file=path.name, aar_bytes=path.stat().st_size, sha256=digest(stream))


def benchmark_local(root, aar, pom, offline, profile_path):
    profile = read_profile(profile_path)
    info = aar_info(aar)
    metadata = pom.read_bytes()
    project = ET.fromstring(metadata)
    version = project.findtext("{*}version", "").strip()
    if (project.findtext("{*}groupId") != profile["group"]
            or project.findtext("{*}artifactId") != profile["artifact"]
            or project.findtext("{*}packaging") != "aar"
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version)):
        raise ValueError("Use the matching SDK profile and AAR publication POM with a literal version")
    if b"published-with-gradle-metadata" in metadata:
        raise ValueError("This publication requires Gradle module metadata. Use run --sdk-version with its full published repository.")
    with tempfile.TemporaryDirectory(prefix="integrated-maven-") as directory:
        repository = Path(directory)
        module = repository.joinpath(*profile["group"].split("."), profile["artifact"], version)
        module.mkdir(parents=True)
        name = f'{profile["artifact"]}-{version}'
        shutil.copyfile(aar, module / (name + ".aar"))
        (module / (name + ".pom")).write_bytes(metadata)
        result = benchmark(root, version, offline, profile_path, repository)
    result["local_aar"] = info
    result["pom_sha256"] = hashlib.sha256(metadata).hexdigest()
    return result


def read_profile(path):
    profile = json.loads(path.read_text())
    for key in ("group", "artifact"):
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*", profile[key]):
            raise ValueError(f"Invalid Maven {key} in {path}")
    if not (path.parent / "java").is_dir():
        raise ValueError(f"Missing SDK integration source directory next to {path}")
    return profile


def benchmark(root, version, offline, profile_path, repository=None):
    profile = read_profile(profile_path)
    coordinate = f'{profile["group"]}:{profile["artifact"]}'
    wrapper = "gradlew.bat" if sys.platform == "win32" else "gradlew"
    tasks = [f":app:assemble{host.capitalize()}{kind}Release"
             for host in HOSTS for kind in ("Control", "Integrated")]
    cmd = [str(root / wrapper), *tasks, ":app:footprintDependencies", f"-PsdkVersion={version}",
           f"-PsdkProfile={profile_path.resolve()}"]
    if repository is not None:
        cmd.append(f"-PlocalSdkRepo={repository.as_uri()}")
    if offline:
        cmd.append("--offline")
    subprocess.run(cmd, cwd=root, stdout=sys.stderr, check=True)
    result = build_matrix(root / "app/build/outputs/apk")
    versions = json.loads((root / "app/build/reports/footprint/dependencies.json").read_text())
    for host in HOSTS:
        control = versions[host + "ControlRelease"]
        integrated = versions[host + "IntegratedRelease"]
        if integrated.get(coordinate) != version:
            raise ValueError(f"Unexpected SDK resolution for {host}")
        result["hosts"][host]["dependencies_changed"] = dependencies_changed(control, integrated)
    result["sdk_coordinate"] = coordinate
    result["sdk_version"] = version
    return result


def changes(previous, current):
    if previous["schema"] != 1 or current["schema"] != 1:
        raise ValueError("Unsupported matrix schema")
    if previous.get("sdk_coordinate") != current.get("sdk_coordinate"):
        raise ValueError("SDK coordinates differ between reports")
    growth = {}
    for host in HOSTS:
        before, after = previous["hosts"][host], current["hosts"][host]
        if before["control"]["sha256"] != after["control"]["sha256"]:
            raise ValueError(f"{host} control APK changed. Rebuild both SDK versions with matched inputs.")
        old_cost = difference(before["control"], before["integrated"])
        new_cost = difference(after["control"], after["integrated"])
        growth[host] = difference(old_cost, new_cost)
        if "dependencies_changed" in before and "dependencies_changed" in after:
            growth[host]["changed_dependency_effects"] = dependencies_changed(
                before["dependencies_changed"], after["dependencies_changed"])
    return growth


def nonnegative(value):
    value = int(value)
    if value < 0:
        raise argparse.ArgumentTypeError("Budget must be nonnegative")
    return value


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="action", required=True)
    archives = commands.add_parser("aar-pair", help="Compare two local AAR file sizes and hashes")
    archives.add_argument("v1", type=Path)
    archives.add_argument("v2", type=Path)
    local = commands.add_parser("run-local", help="Build the publisher fixtures using a local SDK AAR and its POM")
    local.add_argument("aar", type=Path)
    local.add_argument("pom", type=Path)
    local.add_argument("--offline", action="store_true")
    inspect = commands.add_parser("inspect", help="Inspect one built APK and match native bytes to input AARs")
    inspect.add_argument("apk", type=Path)
    inspect.add_argument("--aar", action="append", type=Path, default=[])
    pair = commands.add_parser("pair", help="Measure two standalone APKs built under matched conditions")
    pair.add_argument("control", type=Path)
    pair.add_argument("treatment", type=Path)
    matrix = commands.add_parser("matrix", help="Compare the four fixture release variants")
    matrix.add_argument("outputs", nargs="?", type=Path, default=Path("app/build/outputs/apk"))
    run = commands.add_parser("run", help="Build release fixtures and include resolved dependency changes")
    run.add_argument("--sdk-version", required=True)
    run.add_argument("--offline", action="store_true")
    gate = commands.add_parser("gate", help="Compare two matrix reports with identical control APKs")
    gate.add_argument("previous", type=Path)
    gate.add_argument("candidate", type=Path)
    gate.add_argument("--max-growth-bytes", type=nonnegative, required=True)
    for parser in (local, run):
        parser.add_argument("--profile", type=Path, default=Path(__file__).resolve().parents[1] / "profiles/exif/profile.json")
    args = cli.parse_args(argv)
    failed = False
    try:
        if args.action == "aar-pair":
            first, second = aar_info(args.v1), aar_info(args.v2)
            result = dict(v1=first, v2=second, aar_growth_bytes=second["aar_bytes"] - first["aar_bytes"])
        elif args.action == "run-local":
            result = benchmark_local(Path(__file__).resolve().parents[1], args.aar, args.pom, args.offline, args.profile)
        elif args.action == "inspect":
            result = inspect_apk(args.apk, native_sources(args.aar))
        elif args.action == "pair":
            control, treatment = inspect_apk(args.control), inspect_apk(args.treatment)
            result = dict(control=control, treatment=treatment, incremental=difference(control, treatment))
        elif args.action == "run":
            result = benchmark(Path(__file__).resolve().parents[1], args.sdk_version, args.offline, args.profile)
        elif args.action == "matrix":
            result = build_matrix(args.outputs)
        else:
            growth = changes(json.loads(args.previous.read_text()), json.loads(args.candidate.read_text()))
            offenders = [host for host, row in growth.items() if row["apk_bytes"] > args.max_growth_bytes]
            result = dict(growth=growth, budget_bytes=args.max_growth_bytes, over_budget=offenders)
            failed = bool(offenders)
    except subprocess.CalledProcessError as error:
        cli.exit(2, f"Release build failed (Gradle exit {error.returncode}).\n")
    except (OSError, BadZipFile, ValueError, KeyError, TypeError, ET.ParseError) as error:
        cli.exit(2, f"Invalid inputs: {error}\n")
    print(json.dumps(result, indent=2))
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
