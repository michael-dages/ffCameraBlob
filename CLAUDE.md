# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Single-purpose Tkinter desktop app that triages **FastForward vision-camera CSV exports**: it reads
per-image blob-detection results, finds images whose blob count fell short of the expected maximum,
and moves/copies the corresponding `.bmp` files into timestamped folders. Shipped to end users as a
standalone Windows `.exe`.

There is no test suite, no linter config, and no package manifest — the repo is two files plus CI.

## Commands

```bash
pip install -r requirements.txt   # pyinstaller only; the app itself is pure stdlib + tkinter
python ff_blob_checker_gui.py     # run the GUI

./collect_failed.sh               # bash only; consolidates failed_2025*/ BMPs into aggregate_failed/ and deletes them
```

Building the EXE locally (same command CI uses):

```bash
pyinstaller --noconsole --clean --onefile --name ff_blob_checker_gui ff_blob_checker_gui.py
```

Releases are tag-driven: pushing a `v*` tag runs `.github/workflows/release-exe.yaml`, which
generates a `version.py` from the tag name, builds the EXE, zips it with README/LICENSE, and
attaches both to a GitHub Release. `version.py` exists only inside CI — nothing in the source
imports it.

## Sample data

A real export lives outside the repo at
`C:\Users\archa\Desktop\uc406_pictures\InspectionCamImages\cam3\FastForward_N944_3L\FastForward_N944_3L.csv`
(493 data rows), with its 492 `Camera3_<epoch>.bmp` images in the parent `cam3\` folder — i.e. the
exact "CSV one level below the images" layout the GUI defaults assume. Images are 1280×1024
8-bit grayscale (`mode="L"`). Use it to check parsing changes against reality.

Blob counts over its 492 named rows, useful as a regression fixture — set the Expected field to
*N* and the under-max count should come out as the running total below *N*:

| BlobNumResults | 0 | 1 | 2 | 3 | 4 | 6 | 8 |
|---|---|---|---|---|---|---|---|
| images | 48 | 134 | 115 | 178 | 4 | 1 | 12 |

So Expected=1 → 48 flagged, Expected=3 → 297, Expected=8 → 480. Blobs are ~110 px circles
(`InnerCircleRadius` ≈ 5500 → 55 px) sitting in one horizontal band, `BlobPositionY` ≈ 480–540 px.

## Input data format

The CSV is not ordinary CSV. `parse_csv()` encodes three assumptions that anything touching the
input must respect:

1. **Line 1 is prose metadata and is discarded** before `csv.DictReader` sees the file — in the
   sample it reads `Vision application executed up to selected vision function 'Blob'.` and
   carries a UTF-8 BOM. Line 2 is the real header.
2. **The delimiter is `;`**, not a comma.
3. **It is wide-format with a fixed 8 blob slots.** Every per-blob metric is a 1-indexed,
   zero-padded column family running `01`–`08`: `ModelNumber`, `BlobArea`, `BlobLength`,
   `BlobWidth`, `BlobCircularity`, `Rectangularity`, `Anisometry`, `CircleX`, `CircleY`,
   `InnerCircleRadius`, `BlobPositionX`, `BlobPositionY`. `BlobNumResults` says how many slots
   are populated; **unused slots are filled with `0`, not left empty** (this matters — see the
   rough edges below). `VA OUTPUTS` (col 6) and `VA INPUTS` (col 108) are empty section-marker
   columns, and which side of them a field falls on is meaningful: `BlobNumResults` (col 10) is a
   measured **output**, while `BlobNumSearchMax` (col 110) is a configuration **input** — the
   ceiling the vision app was told to search up to (`8` throughout the sample), *not* the number
   of features a good part should have. Do not treat it as an expected yield.

**One row per image.** In the sample all 493 rows have distinct `ImageName`s; blob multiplicity is
expressed across the `01`–`08` slots, never across rows. The group-by-`ImageName` pass therefore
operates on groups of size 1 in practice, though it is written to tolerate more.

**The final line is a trailer**, literally `Successfully completed`. It parses into a row whose
`ImageDirectory` is that string and whose every other field is `None`, so `int(None)` raises. That
is what the main loop's bare `try/except: continue` exists to absorb (the code comment says "first
and last line", but only the last row actually throws — the first is skipped outright). Be aware
the same bare except silently swallows every other per-row error.

**Positions are fixed-point hundredths of a pixel.** `BlobPositionX01` values like `48038` mean
x≈480 px, and the observed range maxes near `117668` → 1176 px, consistent with the 1280-wide
images. Y values cluster in 48000–54000 (480–540 px), a narrow horizontal band.

## Architecture

- **`ff_blob_checker_gui.py`** — everything: parsing, the `App(tk.Tk)` window, and the file-moving
  logic. All work happens in `App._run(execute: bool)`; both GUI buttons are thin wrappers
  (`analyze_only` / `analyze_and_execute`) that only differ by that flag. When `execute=False`
  nothing is moved, no log is written, and nothing else is emitted — "Analyze (no move/copy)"
  is genuinely read-only.
- **`collect_failed.sh`** — post-hoc cleanup utility, independent of the Python code.

### The two-pass structure of `_run` (important)

`_run` walks the rows **twice**, and the passes do not share results:

1. **Per-row pass** — for each row it calls `parse_blob_area()` (accumulates areas for the
   median/min/max line in the results box). It also builds an `under_max` list here.
2. **Per-image pass** — rows are regrouped by `ImageName` via `defaultdict` (rows with a falsy
   `ImageName`, such as the trailer, are dropped), then `under_max` is **reassigned from scratch**
   (`under_max, passed = [], []`), discarding pass 1's list. The reported counts and every file
   that gets moved come from this pass only. An image fails if *any* of its rows is under max, and
   is eligible for the "passed" buckets only if *all* rows equal it.

The threshold is the GUI's "Expected BlobNumResults (max)" field, and nothing overrides it. Until
recently pass 1 reassigned `expected_max` from each row's `BlobNumSearchMax` (added in `3d96d5b`),
which silently discarded whatever the user typed and compared results against a config ceiling
instead of an expected yield — on the sample that flagged 480 of 492 images. That line is gone; if
you reintroduce CSV-driven thresholds, use a column from the `VA OUTPUTS` side.

### Path conventions

Defaults are derived from the selected CSV's location and are the behavior users depend on:

- **Source images** default to the folder *one level above* the CSV (the CSV sits in a subfolder of
  the image dump, alongside the `.visionapplication` file).
- **Failed destination** defaults to the CSV's own folder.
- Outputs land in `failed_<YYYYMMDD_HHMMSS>/`, or `<model>/failed_<ts>/` when "separate by model"
  is on. Passing images, when enabled, go to `passed_<ts>/<category>/` where category is
  `1-top`, `2-bottom`, or `mixed`.
- Model identifiers are pulled from filenames by `extract_model_from_name()`, which matches the
  literal pattern `N\d{3}` (e.g. `N944`) and falls back to the model parsed from the CSV filename,
  else `"Unknown"`. Note the sample's image names (`Camera3_1752187934.bmp`) contain no `N###`, so
  every image inherits the CSV-derived fallback.

### Known rough edges in the current code

Confirmed against the sample export. Worth knowing before editing; do not "fix" them incidentally
unless that is the task:

- **The blob-area stats are dead on any export containing a zero-blob row, and take the tail of
  the report with them.** `parse_blob_area()` only `return`s from *inside* its `for` loop, so a row
  with `BlobNumResults == 0` falls off the end and returns `None`. `_run` assigns that straight
  back (`blobAreas = self.parse_blob_area(...)`), permanently poisoning the accumulator: every
  later row raises `AttributeError` on `None.append` into the bare except, and the results box
  finally dies on `median(None)` with `TypeError: 'NoneType' object is not iterable`.
  The sample export has 48 such rows, the first at index 55. Because Tkinter swallows callback
  exceptions, the window stays up showing a report truncated after "Under-max count" with **no
  error dialog** — it looks like it merely finished quietly. Crops and the under-max tally are
  computed before this point and are unaffected.
- **The "passed" categorization always yields `mixed`.** It collects every `ModelNumber*` column
  whose value is a non-empty string, but unused slots hold `"0"`, so `all(l == "1")` can never be
  true on a row with fewer than 8 blobs. In the sample the only values present anywhere are `1`
  (1016 slots) and `0` (2920 padding slots) — `2` never appears, so `2-bottom` is unreachable too.
- Even on rows it survives, `parse_blob_area()` `return`s inside its `for` loop, so it records only
  the first blob per row — the median/min/max line describes first-blob areas, not all blobs.
- The results pane prints "Log written to:" twice.
