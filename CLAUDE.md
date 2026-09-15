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

python ff_validate.py baseline <csv> -o baseline.json   # capture expected results
python ff_validate.py run <csv> -b baseline.json        # diff a later export against them
python ff_validate.py run <csv> -b baseline.json --verbose   # ...and show which blob went missing

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
`C:\Users\archa\Desktop\uc406_pictures\InspectionCamImages\cam3\FastForward_N944_3L\FastForward_N944_3L.csv`,
with its `Camera3_<epoch>.bmp` images in the parent `cam3\` folder — i.e. the exact "CSV one level
below the images" layout the GUI defaults assume. Images are 1280×1024 8-bit grayscale
(`mode="L"`). Use it to check parsing changes against reality.

**This is a curated golden corpus, not a raw production dump.** As of 2026-09-15 it holds 15 data
rows: 14 named images plus the trailer, and **every image has `BlobNumResults` = 8**. That makes
it the validation fixture — any image that comes back with a count other than 8 is a regression.
`BlobNumSearchMax` is `8` throughout (still a config input, not an expected yield).

Two image names carry **negative epochs** (`Camera3_-1546426502.bmp`, `Camera3_-1546454221.bmp`),
which parse and validate fine but mean the camera clock was unset or overflowed at capture. Don't
assume sorting these filenames yields chronological order.

Because every count is 8, a tab-1 run at Expected=8 flags nothing and files all 14 images into
`passed_<ts>/1-top/`; that is also the only Expected value at which the "passed" categorization
works (see the rough edges below). Expected=9 flags all 14.

Blobs sit in one horizontal band (`BlobPositionY` ≈ 489–533 px) and come in **two shape classes**
that are easy to mistake for a capture-condition difference but are not: of the 112 blobs,
`InnerCircleRadius` is ≈4050–5600 (~40–56 px) on 53 of them and ≈1550–3900 (~15–39 px) on 59,
while `BlobArea` stays ~900k–1M for both. Every image contains both classes at once, so this is
two feature types on the part, not lighting or a date split. The large-radius class also runs
higher on `BlobCircularity` and `Rectangularity`.

> An earlier 493-row / 492-image version of this export was replaced on 2026-09-15. Figures quoted
> anywhere against 492 images (blob-count distribution, Expected=1 → 48 flagged, etc.) describe
> that older dump and no longer reproduce.

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

- **`ff_blob_checker_gui.py`** — the `App(tk.Tk)` window, now a `ttk.Notebook` with two tabs.
  - **Blob Checker** (tab 1) — the original app: parsing, the form, and the file-moving logic.
    All work happens in `App._run(execute: bool)`; both buttons are thin wrappers
    (`analyze_only` / `analyze_and_execute`) that only differ by that flag. When `execute=False`
    nothing is moved, no log is written, and nothing else is emitted — "Analyze (no move/copy)"
    is genuinely read-only. Every rough edge listed below still lives here, untouched.
  - **Job Validation** (tab 2) — a front end over `ff_validate`, built by `_build_validation_tab`.
    It reads CSVs through `ffv.read_records`, **never** this module's own `parse_csv`: the latter
    returns raw wide rows including the `0` slot padding, does not canonicalize slot order, and
    hands back the trailer row. A regression gate built on it would report false drift and could
    silently skip rows. `_val_compute` deliberately contains no Tk calls, so it stays movable to
    a worker thread; `_val_render` owns all the widget updates.
  - The `guarded` decorator wraps every tab-2 handler, because Tk swallows callback exceptions
    silently (that is exactly how the `median(None)` bug below hides). Internal helpers raise
    plain `ValueError`; `guarded` turns them into a dialog at the boundary. Note it is applied
    only to the new handlers — `Tk.report_callback_exception` is deliberately *not* overridden,
    since that would change tab 1's observable behavior.
- **`ff_validate.py`** — regression validator for the vision job, modeled on the Cognex In-Sight
  "Job Validation" feature. Usable as a CLI and imported by the GUI's tab 2; the dependency is
  one-directional — `ff_validate` imports nothing from the GUI and stays CI-runnable on its own.
  `baseline` captures an export's per-image blob results as expected values; `run` re-reads a
  later export and reports E/A, exiting 1 on any regression.
  - **The verdict is `BlobNumResults`, compared exactly.** A blob is a feature that either
    registers or does not, so 8 detections dropping to 7 is a regression while a blob whose area
    moved 3% is not. `compare(..., metrics=False)` is the default; `--metrics` opts into
    tolerance-checked per-blob comparison, and `--fields` / `--tolerance` only apply with it.
    `DEFAULT_TOLERANCES` therefore does not affect a default run.
  - `describe_mismatch()` is the drill-down: on a count mismatch the blobs cannot be paired 1:1,
    so both sides are emitted positionally with `itertools.zip_longest`, and the short side pads
    with `None`. That is what shows *which* feature went missing. Rendered by `--verbose` in the
    CLI and as child rows in the GUI tree.
  - It re-implements `parse_csv` rather than sharing the GUI's, deliberately: only slots
    `1..BlobNumResults` are read so the `0` padding never enters a comparison, blob slot order is
    canonicalized by `BlobPositionX` (slot order is not guaranteed stable across runs), the
    trailer row is dropped by its empty `ImageName` rather than absorbed by a bare `except`, and
    the file is opened `utf-8-sig` so the BOM goes away.
  - Images join by `ImageName`, BMP content hash, or row index. The hash mode exists because
    `Camera3_<epoch>.bmp` names change on every re-capture, which breaks a name join.
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
  Because Tkinter swallows callback exceptions, the window stays up showing a report truncated
  after "Under-max count" with **no error dialog** — it looks like it merely finished quietly.
  Crops and the under-max tally are computed before this point and are unaffected.
  The current golden corpus has no zero-blob rows, so tab 1 completes normally on it and the bug
  stays latent; the old 492-image export had 48 such rows (first at index 55) and reproduced it.
  Tab 2 is immune — `ffv.read_records` handles a zero-blob row as simply an empty `blobs` list.
- **The "passed" categorization collapses to `mixed` whenever Expected < 8.** It collects every
  `ModelNumber*` column whose value is a non-empty string, but unused slots hold `"0"`, so
  `all(l == "1")` fails on any row with fewer than 8 blobs. It works correctly at Expected=8,
  where a passing image fills all eight slots and no padding is read — verified: a run at
  Expected=8 produced a `passed_<ts>/1-top/` holding exactly the 12 eight-blob images, all
  `ModelNumber` = `1`. Below 8 the padding poisons the label set and every passing image is
  filed as `mixed`. Separately, `2` never appears anywhere in the sample, so `2-bottom` is
  unreachable on this dataset regardless.
- Even on rows it survives, `parse_blob_area()` `return`s inside its `for` loop, so it records only
  the first blob per row — the median/min/max line describes first-blob areas, not all blobs.
- The results pane prints "Log written to:" twice.
