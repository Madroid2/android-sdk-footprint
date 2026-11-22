import copy
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from tools import footprint as fp


class FootprintTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, path, entries):
        path.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(path, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, data, ZIP_STORED if name.endswith(".so") else ZIP_DEFLATED)
        return path

    def apk(self, path, **extra):
        return self.archive(path, {"AndroidManifest.xml": b"fixture", "classes.dex": b"fixture", **extra})

    def test_compressed_totals_reconcile_to_physical_bytes(self):
        path = self.apk(self.root / "sample.apk", **{"classes12.dex": b"x"*4096, "assets/sample": b"data"})
        report = fp.inspect_apk(path)
        self.assertEqual(sum(report["packaged_bytes"].values()), path.stat().st_size)
        with ZipFile(path) as archive:
            expected = sum(archive.getinfo(name).compress_size for name in ("classes.dex", "classes12.dex"))
        self.assertEqual(expected, report["packaged_bytes"]["dex"])
        self.assertGreater(report["packaged_bytes"]["archive_overhead"], 0)

    def test_attribution_requires_matching_bytes_abi_and_name(self):
        path = self.apk(self.root / "sample.apk", **{"lib/arm64-v8a/libsample.so": b"payload"})
        good = self.archive(self.root / "good.aar", {"jni/arm64-v8a/libsample.so": b"payload"})
        other = self.archive(self.root / "other.aar", {"jni/x86/libsample.so": b"payload",
                             "jni/arm64-v8a/libsample.so": b"different"})
        row, = fp.inspect_apk(path, fp.native_sources([other, good]))["native"]
        self.assertEqual(row["matching_aars"], ["good.aar"])
        self.assertEqual(row["packaged_bytes"], 7)

    def test_local_aar_and_metadata_are_staged_together(self):
        aar = self.archive(self.root / "candidate.aar", {"AndroidManifest.xml": b"manifest"})
        pom = self.root / "candidate.pom"
        pom.write_text('<project xmlns="http://maven.apache.org/POM/4.0.0">'
                       '<groupId>io.adjoe</groupId><artifactId>adjoe-sdk-android</artifactId>'
                       '<version>4.5.3</version><packaging>aar</packaging></project>')
        def verify(root, version, offline, profile, repo):
            base = repo / "io/adjoe/adjoe-sdk-android/4.5.3"
            self.assertEqual((base / "adjoe-sdk-android-4.5.3.aar").read_bytes(), aar.read_bytes())
            self.assertEqual((base / "adjoe-sdk-android-4.5.3.pom").read_bytes(), pom.read_bytes())
            self.assertEqual(version, "4.5.3")
            return {}
        with patch.object(fp, "benchmark", side_effect=verify):
            result = fp.benchmark_local(self.root, aar, pom, False, Path(__file__).resolve().parents[1] / "profiles/playtime/profile.json")
        self.assertEqual(result["local_aar"], fp.aar_info(aar))

    def test_archive_comparison_accepts_two_local_aars(self):
        v1 = self.archive(self.root / "old.aar", {"AndroidManifest.xml": b"manifest"})
        v2 = self.archive(self.root / "new.aar", {"AndroidManifest.xml": b"manifest", "assets/new": b"new"})
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(fp.main(["aar-pair", str(v1), str(v2)]), 0)
        self.assertEqual(json.loads(out.getvalue())["aar_growth_bytes"],
                         v2.stat().st_size - v1.stat().st_size)

    def test_invalid_apk_rejected(self):
        path = self.archive(self.root / "base.apk", {"resources.arsc": b"resource-only split"})
        with self.assertRaises(ValueError):
            fp.inspect_apk(path)

    def matrix(self):
        for host in fp.HOSTS:
            for integration in ("Control", "Integrated"):
                variant = host + integration + "Release"
                folder = self.root / variant
                native = {"lib/arm64-v8a/libsample.so": b"payload"} if integration == "Integrated" else {}
                self.apk(folder / "one.apk", **native)
                (folder / "output-metadata.json").write_text(json.dumps({
                    "variantName": variant, "elements": [{"filters": [], "outputFile": "one.apk"}]}))
        return fp.build_matrix(self.root)

    def test_matrix_per_host_and_abi(self):
        report = self.matrix()
        for host in fp.HOSTS:
            delta = report["hosts"][host]["incremental"]
            self.assertEqual(delta["native_bytes_by_abi"], {"arm64-v8a": 7})
            self.assertEqual(delta["packaged_bytes"]["native"], 7)
            self.assertEqual(delta["apk_bytes"], sum(delta["packaged_bytes"].values()))

    def test_growth_gate_exit_codes_at_budget(self):
        before = self.matrix()
        candidate = copy.deepcopy(before)
        candidate["hosts"]["lean"]["integrated"]["apk_bytes"] += 11
        candidate["hosts"]["lean"]["integrated"]["packaged_bytes"]["dex"] += 11
        files = [self.root / name for name in ("before.json", "after.json")]
        for f, result in zip(files, [before, candidate]):
            f.write_text(json.dumps(result))
        with redirect_stdout(io.StringIO()) as capture:
            self.assertEqual(fp.main(["gate", *map(str, files), "--max-growth-bytes", "10"]), 1)
        self.assertEqual(json.loads(capture.getvalue())["over_budget"], ["lean"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(fp.main(["gate", *map(str, files), "--max-growth-bytes", "11"]), 0)

    def test_changed_control_rejected(self):
        before = self.matrix()
        candidate = copy.deepcopy(before)
        candidate["hosts"]["compose"]["control"]["sha256"] = "different"
        with self.assertRaisesRegex(ValueError, "control APK changed"):
            fp.changes(before, candidate)

    def test_ambiguous_split_and_missing_variant_rejected(self):
        self.matrix()
        metadata = self.root / "composeIntegratedRelease/output-metadata.json"
        obj = json.loads(metadata.read_text())
        obj["elements"][0]["filters"] = ["x86"]
        metadata.write_text(json.dumps(obj))
        with self.assertRaisesRegex(ValueError, "unsplit"):
            fp.build_matrix(self.root)
        metadata.unlink()
        with self.assertRaisesRegex(ValueError, "found 0"):
            fp.build_matrix(self.root)

    def test_dependency_diff_distinguishes_mediation_from_new_modules(self):
        before = {"shared": "1", "fixed": "2"}
        after = {"shared": "3", "new": "1", "fixed": "2"}
        self.assertEqual(fp.dependencies_changed(before, after), {
            "new": {"from": None, "to": "1"}, "shared": {"from": "1", "to": "3"}})
