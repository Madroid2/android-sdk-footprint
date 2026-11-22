# Android SDK Footprint

Compare two Android SDK releases before shipping. Measure the change in the AAR itself or the incremental cost of including the SDK in a release APK. A GitHub Action enforces a byte budget and writes a job summary.

| What you have | What to run | What you learn |
|---|---|---|
| Two local AARs | `aar-pair` or the Action in `aar` mode | Exact archive sizes, hashes and byte growth |
| Two local AARs and matching POMs | `run-local` twice and `gate` | Marginal cost after dependency resolution, R8 and resource shrinking |
| Published Maven versions | `run` or the Action in `apk` mode | The same publisher comparison using published artifacts |

## 1. Compare the two AARs on your machine

From the repository root in Android Studio's Terminal, set the paths to the actual files. Python 3.9+ is sufficient for this step.

```sh
V1="$HOME/Downloads/playtime-v1.aar"
V2="$HOME/Downloads/playtime-v2.aar"
python3 tools/footprint.py aar-pair "$V1" "$V2"
```

Read `aar_growth_bytes`. A positive value means v2 is larger. Identical hashes mean the inputs contain identical bytes. Archive size excludes dependencies delivered separately.

## 2. Measure the publisher impact

Use JDK 17+, Android SDK Platform 36 and Build Tools 35.0.0. Set `ANDROID_HOME` or configure the SDK location in Android Studio. On macOS you can use the bundled JDK with `export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"`. The first build needs repository access. No device or publisher account is needed.

Supply the matching Maven POM for each file. Choose the profile that exercises the SDK's API. This example uses Playtime 4.x.

```sh
POM1="$HOME/Downloads/playtime-v1.pom"
POM2="$HOME/Downloads/playtime-v2.pom"
PROFILE="profiles/playtime/profile.json"
mkdir -p reports
python3 tools/footprint.py run-local "$V1" "$POM1" --profile "$PROFILE" > reports/v1.json &&
python3 tools/footprint.py run-local "$V2" "$POM2" --profile "$PROFILE" > reports/v2.json &&
python3 tools/footprint.py gate reports/v1.json reports/v2.json --max-growth-bytes 102400
```

Gradle prints live progress to the terminal. The reports contain measurements for a platform Views host and a Compose Material 3 host. Each host gets a control APK and an SDK-integrated APK. `hosts.compose.incremental.apk_bytes` in v2 is the cost of integrating that version. `growth.compose.apk_bytes` in the gate is the additional cost of upgrading from v1 to v2. The gate checks identical control hashes and reports native payload changes per ABI, packaged byte categories and changes to runtime dependencies. Reported universal APK size differs from a device-specific Play download.

The local runner stages a POM-based publication in a temporary Maven repository. Use a POM with explicit group, artifact, version and AAR packaging. Dependencies must be resolvable. For Gradle Module Metadata, publish the complete module to a staging repository and compare version coordinates. If an upgrade changes the SDK's public API, select an integration path supported by both releases for a matched measurement.

## 3. Try the included release job

Open **Actions → SDK footprint → Run workflow**. The default example compares AndroidX ExifInterface 1.3.6 with 1.3.7. For Playtime, select **mode: aar**, **profile: playtime**, baseline **4.5.2** and candidate **4.5.3** to compare the published files. In APK mode, a hosted test of this Playtime baseline stopped in R8 because its dependency graph referenced missing FaceTec classes. This is an incomplete comparison. For a host that integrates FaceTec, supply its authorized runtime dependencies in the fixture and repeat the APK check. [Build log](https://github.com/Madroid2/android-sdk-footprint/actions/runs/45015357623) The default example also runs on pull requests, merge queues and pushes to main. Review the two host rows in the job summary and download `sdk-footprint-reports` for the JSON. The example budget is 102,400 bytes (100 KiB) per host. A delta equal to the budget passes. Exit 1 means over budget and exit 2 means the comparison could not complete.

Run the same example locally:

```sh
python3 tools/release_check.py --mode apk --baseline 1.3.6 --candidate 1.3.7 \
  --budget 102400 --output reports/exif
```

## 4. Gate an unpublished candidate in your own SDK repository

The composite Action can compare candidates produced earlier in the same job. Here is a complete job for a library called `sdk` with a Maven publication called `release`. Set repository variables `SDK_BASELINE_AAR_URL` to the previous published AAR and `SDK_FOOTPRINT_BUDGET_BYTES` to your byte budget. Adjust the module, publication and output paths to match your project.

```yaml
name: SDK pre-release
on: [pull_request, merge_group, workflow_dispatch]
permissions:
  contents: read
jobs:
  footprint:
    name: SDK footprint budget
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: '17'
      - uses: gradle/actions/setup-gradle@v4
      - name: Build candidate and fetch baseline
        env:
          BASELINE_URL: ${{ vars.SDK_BASELINE_AAR_URL }}
        run: |
          ./gradlew :sdk:assembleRelease
          curl --fail --location "$BASELINE_URL" --output "$RUNNER_TEMP/baseline.aar"
      - uses: Madroid2/android-sdk-footprint@v1
        id: size
        with:
          mode: aar
          baseline: ${{ runner.temp }}/baseline.aar
          candidate: sdk/build/outputs/aar/sdk-release.aar
          max-growth-bytes: ${{ vars.SDK_FOOTPRINT_BUDGET_BYTES }}
      - uses: actions/upload-artifact@v4
        if: ${{ always() && steps.size.outputs.reports != '' }}
        with:
          name: footprint-evidence
          path: ${{ steps.size.outputs.reports }}/*.json
```

For **publisher APK impact with a POM-only publication**, add `:sdk:generatePomFileForReleasePublication` to the build command, use a published baseline version as input and replace the Action's `with` block as shown below. Ensure Platform 36 and Build Tools 35.0.0 are installed. Both versions use the same profile. A local baseline can also be supplied with `baseline-pom`.

```yaml
with:
  mode: apk
  profile: benchmark/profile.json
  baseline: '1.2.0'
  candidate: sdk/build/outputs/aar/sdk-release.aar
  candidate-pom: sdk/build/publications/release/pom-default.xml
  max-growth-bytes: ${{ vars.SDK_FOOTPRINT_BUDGET_BYTES }}
```

For a Gradle publication that includes a `.module` sidecar, point the Action at the complete staging repository. For example, configure a Maven publishing repository named `Footprint` at `layout.buildDirectory.dir("footprint-maven")` in your SDK module, then run `./gradlew :sdk:publishReleasePublicationToFootprintRepository` in the producer step. Supply `candidate: '1.3.0-rc01'` (the actual published version) and `candidate-repo: sdk/build/footprint-maven` instead of the AAR and POM inputs. Gradle resolves the measured module from that local repository. Publish additional dependencies to configured dependency repositories when needed.

To enforce the decision, add **SDK footprint budget** as a required check in your target branch's ruleset after its first run. In a release workflow, have the publishing job declare `needs: footprint` and use the normal success condition. This keeps publication behind the size check.

## 5. Add another SDK

Archive mode accepts any Android AAR directly. For publisher APK measurements, copy `profiles/exif/` to a new directory and change two files.

1. Set `group` and `artifact` in `profile.json` to the Maven module. Optionally add `repository` for an additional Maven URL. Google Maven and Maven Central are configured by default.
2. Implement `dev.madroid.footprint.SdkEntry.open(Activity)` under the adjacent `java/` directory. Call a representative public API. The integrated release includes this source so the shrinker can see an actual SDK usage path.

Use `--profile path/to/profile.json` locally or the Action's `profile` input in CI. The repository includes profiles for [AndroidX ExifInterface](profiles/exif/) and [Playtime](profiles/playtime/). Private dependency repositories require the build runner to have appropriate access. For local host-supplied binary dependencies, place the appropriate standalone AAR/JAR and any required dependencies in a sibling `libs/` directory next to `profile.json`. The integrated host includes those binaries in its measured cost. Private libraries belong in the consuming CI workspace. Additional manifest entries, resources, Gradle plugins or a different minimum Android API may require adapting the host fixtures to match your SDK.

## Tests

```sh
python3 -m unittest discover -s tests -v
```
