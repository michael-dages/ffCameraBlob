# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Single-purpose Tkinter desktop app that triages **FastForward vision-camera CSV exports**: it reads
per-image blob-detection results, finds images whose blob count fell short of the expected maximum,
and moves/copies the corresponding `.bmp` files into timestamped folders. Shipped to end users as a
standalone Windows `.exe`.

There is no test suite, no linter config, and no package manifest — the repo is three files plus CI.

## Commands

```bash
pip install -r requirements.txt   # NOTE: only installs pyinstaller
pip install Pillow                # required separately — crop.py imports PIL but it is NOT in requirements.txt
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

## Input data format

The CSV is not ordinary CSV. `parse_csv()` encodes three assumptions that anything touching the
input must respect:

1. **Line 1 is a metadata header and is discarded** before `csv.DictReader` sees the file.
2. **The delimiter is `;`**, not a comma.
3. **It is wide-format**: one row can hold several blobs, spread across 1-indexed, zero-padded
   column families — `BlobArea01..N`, `ModelNumber01..N`, `BlobPositionX01..N`,
   `BlobPositionY01..N`. `BlobNumResults` says how many of those slots are populated for the row,
   and `BlobNumSearchMax` carries the expected count.

The first and last physical lines are typically malformed; the main row loop wraps its body in a
bare `try/except: continue` specifically to tolerate that. Be aware this also silently swallows
every other per-row error.

## Architecture

- **`ff_blob_checker_gui.py`** — everything: parsing, the `App(tk.Tk)` window, and the file-moving
  logic. All work happens in `App._run(execute: bool)`; both GUI buttons are thin wrappers
  (`analyze_only` / `analyze_and_execute`) that only differ by that flag. When `execute=False`
  nothing is written or moved, but blob cropping and stats still run.
- **`crop.py`** — the one extracted helper: `crop_image(input_path, output_path, box)`, a thin
  Pillow wrapper that returns `True`/`False` rather than raising. Its `__main__` block is a
  self-contained demo that writes `test_image.bmp` / `cropped_image.bmp` into the CWD.
- **`collect_failed.sh`** — post-hoc cleanup utility, independent of the Python code.

### The two-pass structure of `_run` (important)

`_run` walks the rows **twice**, and the passes do not share results:

1. **Per-row pass** — for each row it calls `extract_blob()` (crops each blob to
   `<failed_dir>/model<N>/`) and `parse_blob_area()` (accumulates areas for the median/min/max
   line in the results box). It also builds an `under_max` list here.
2. **Per-image pass** — rows are regrouped by `ImageName` via `defaultdict`, then `under_max` is
   **reassigned from scratch** (`under_max, passed = [], []`), discarding pass 1's list. The
   reported counts and every file that gets moved come from this pass only. An image fails if
   *any* of its rows is under max, and is eligible for the "passed" buckets only if *all* rows
   equal it.

Similarly, the `expected_var` value typed in the GUI is used only as an initial fallback — pass 1
overwrites `expected_max` from each row's `BlobNumSearchMax`, so the surviving value is whatever
the last successfully-parsed row carried.

### Path conventions

Defaults are derived from the selected CSV's location and are the behavior users depend on:

- **Source images** default to the folder *one level above* the CSV (the CSV sits in a subfolder of
  the image dump).
- **Failed destination** defaults to the CSV's own folder.
- Outputs land in `failed_<YYYYMMDD_HHMMSS>/`, or `<model>/failed_<ts>/` when "separate by model"
  is on. Passing images, when enabled, go to `passed_<ts>/<category>/` where category is
  `1-top`, `2-bottom`, or `mixed`, decided by the `ModelNumber*` values on the image's rows.
- Model identifiers are pulled from filenames by `extract_model_from_name()`, which matches the
  literal pattern `N\d{3}` (e.g. `N123`) and falls back to the model parsed from the CSV filename,
  else `"Unknown"`.

### Known rough edges in the current code

Worth knowing before editing; do not "fix" them incidentally unless that is the task:

- `parse_blob_area()` `return`s inside its `for` loop, so it records only the first blob per row.
  It also crashes on an empty `blobAreas` when the results box calls `median()`/`min()`/`max()`.
- `extract_blob()` builds paths with hard-coded `\` separators and calls `os.mkdir` (not
  `makedirs`), making it Windows-only and dependent on the destination's parent already existing.
  Its crop geometry is hard-coded (`posY=490`, 150×200 box) and `posX` is divided by 100; the
  comment marks this as deliberate shortcutting, not derived from the data.
- `crop_image` writes the output using the extension of `output_path`, which inherits the source
  `.bmp` name.
- The results pane prints "Log written to:" twice.
