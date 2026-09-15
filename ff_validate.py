#!/usr/bin/env python3
"""Regression validation for FastForward vision-camera CSV exports.

Mimics the Cognex In-Sight "Job Validation" workflow. Capture a known-good
export as a baseline (their "Accept All" step), then diff later exports against
it per image, reporting expected (E) vs actual (A).

The verdict is BlobNumResults: each blob is a feature that either registers or
does not, so a part dropping from 8 detections to 7 is a regression while a
blob whose area moved 3% is not. Per-blob metrics are diagnostic -- see
--verbose to drill into a failure, or --metrics to let drift fail a run.

    python ff_validate.py baseline good_run.csv -o baseline.json
    python ff_validate.py run    new_run.csv  -b baseline.json

Exit codes: 0 all tests passed, 1 one or more failed, 2 usage/IO error.
Pure stdlib, same as the GUI.
"""

import argparse
import csv
import hashlib
import itertools
import json
import math
import os
import sys
import time
from collections import OrderedDict

# Per-blob column families. Each is a 1-indexed, zero-padded run 01-08.
BLOB_FIELDS = [
    "ModelNumber", "BlobArea", "BlobLength", "BlobWidth", "BlobCircularity",
    "Rectangularity", "Anisometry", "CircleX", "CircleY", "InnerCircleRadius",
    "BlobPositionX", "BlobPositionY",
]
COUNT_FIELD = "BlobNumResults"
MAX_SLOTS = 8

# Defaults tuned to the N944 sample export. Positions are hundredths of a
# pixel, so abs:500 is a 5 px budget. Override per baseline by editing the
# "tolerances" block of the JSON, or per run with --tolerance.
DEFAULT_TOLERANCES = {
    COUNT_FIELD:          {"abs": 0},
    "ModelNumber":        {"abs": 0},
    "BlobArea":           {"rel": 0.02},
    "BlobLength":         {"rel": 0.02},
    "BlobWidth":          {"rel": 0.02},
    "BlobCircularity":    {"abs": 2},
    "Rectangularity":     {"abs": 2},
    "Anisometry":         {"abs": 2},
    "CircleX":            {"abs": 500},
    "CircleY":            {"abs": 500},
    "InnerCircleRadius":  {"rel": 0.05},
    "BlobPositionX":      {"abs": 500},
    "BlobPositionY":      {"abs": 500},
}

FORMAT = "ffvalidate/1"


# --------------------------------------------------------------------------
# CSV reading
# --------------------------------------------------------------------------

def parse_csv(path):
    """Yield raw dict rows. Line 1 is prose metadata (with a BOM) and is
    discarded before DictReader sees the file; line 2 is the real header."""
    with open(path, "r", newline="", encoding="utf-8-sig", errors="replace") as f:
        if not f.readline():
            raise ValueError("%s is empty" % path)
        reader = csv.DictReader(f, delimiter=";")
        if not reader.fieldnames:
            raise ValueError("%s: no header row found on line 2" % path)
        for row in reader:
            yield row


def _num(text):
    """Parse a cell to int/float, or None when blank or unparseable."""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return None


def read_records(path, order="position"):
    """Parse an export into per-image records.

    Rows with no ImageName are skipped -- that silently absorbs the trailer
    line ("Successfully completed"), which parses into a row whose every
    field but ImageDirectory is None. Only slots 1..BlobNumResults are read,
    because unused slots are filled with 0 rather than left empty.
    """
    records = []
    for row in parse_csv(path):
        name = (row.get("ImageName") or "").strip()
        if not name:
            continue
        count = _num(row.get(COUNT_FIELD))
        if count is None:
            continue
        count = int(count)
        blobs = []
        for slot in range(1, min(count, MAX_SLOTS) + 1):
            blobs.append({f: _num(row.get("%s%02d" % (f, slot))) for f in BLOB_FIELDS})
        if order == "position":
            # Slot order is not guaranteed stable between runs; sort into a
            # canonical left-to-right order so slot shuffling is not a diff.
            blobs.sort(key=lambda b: (b.get("BlobPositionX") or 0,
                                      b.get("BlobPositionY") or 0))
        records.append({
            "index": len(records),
            "image": name,
            COUNT_FIELD: count,
            "blobs": blobs,
        })
    return records


# --------------------------------------------------------------------------
# Join keys
# --------------------------------------------------------------------------

def default_images_dir(csv_path):
    """The GUI's convention: images live one level above the CSV."""
    return os.path.dirname(os.path.dirname(os.path.abspath(csv_path)))


def file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def key_for(record, mode, images_dir, cache):
    """Return the join key for a record, or None if it cannot be keyed."""
    if mode == "imagename":
        return record["image"]
    if mode == "index":
        return "#%04d" % record["index"]
    if mode == "hash":
        path = os.path.join(images_dir, record["image"])
        if path in cache:
            return cache[path]
        cache[path] = file_digest(path) if os.path.isfile(path) else None
        return cache[path]
    raise ValueError("unknown join key mode: %s" % mode)


def key_records(records, mode, images_dir):
    """Map join key -> record. Returns (mapping, unkeyed, duplicates)."""
    mapping, unkeyed, duplicates, cache = OrderedDict(), [], [], {}
    for record in records:
        key = key_for(record, mode, images_dir, cache)
        if key is None:
            unkeyed.append(record["image"])
            continue
        if key in mapping:
            duplicates.append(record["image"])
            continue
        mapping[key] = record
    return mapping, unkeyed, duplicates


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------

def within(expected, actual, tol):
    if expected is None or actual is None:
        return expected == actual
    return math.isclose(float(actual), float(expected),
                        rel_tol=float(tol.get("rel", 0.0) or 0.0),
                        abs_tol=float(tol.get("abs", 0.0) or 0.0))


def compare(expected, actual, tolerances, fields, metrics=False):
    """Return a list of (path, E, A) failures for one image.

    The verdict is the blob count. Each blob is a feature that either
    registers or does not, so a part that went from 8 detections to 7 is a
    regression while a blob whose area moved 3% is not. Per-blob metrics are
    diagnostic -- see describe_mismatch() -- and only affect the verdict when
    metrics=True.
    """
    exp_n, act_n = expected[COUNT_FIELD], actual[COUNT_FIELD]
    if not within(exp_n, act_n, tolerances.get(COUNT_FIELD, {})):
        # Blob slots are not meaningfully comparable once the count moves.
        return [(COUNT_FIELD, exp_n, act_n)]
    if not metrics:
        return []
    fails = []
    for i, (be, ba) in enumerate(zip(expected["blobs"], actual["blobs"]), start=1):
        for field in BLOB_FIELDS:
            if field not in fields:
                continue
            e, a = be.get(field), ba.get(field)
            if not within(e, a, tolerances.get(field, {})):
                fails.append(("blob[%d].%s" % (i, field), e, a))
    return fails


# Fields shown when drilling into a failure -- enough to identify which
# physical feature stopped registering, without dumping all twelve metrics.
DETAIL_FIELDS = [("x", "BlobPositionX"), ("y", "BlobPositionY"),
                 ("area", "BlobArea"), ("r", "InnerCircleRadius")]


def describe_mismatch(expected, actual):
    """Pair up the expected and actual blob slots for a failing image.

    When the count differs the blobs cannot be diffed 1:1, so both sides are
    emitted positionally (blobs are already sorted left-to-right by
    read_records) and the short side pads with None. That makes it visible
    *which* feature went missing rather than merely that one did.
    """
    rows = []
    exp_blobs = expected.get("blobs") or []
    act_blobs = actual.get("blobs") or []
    for i, (be, ba) in enumerate(itertools.zip_longest(exp_blobs, act_blobs), start=1):
        rows.append(("slot %d" % i, _describe_blob(be), _describe_blob(ba)))
    return rows


def _describe_blob(blob):
    if blob is None:
        return None
    return " ".join("%s=%s" % (label, fmt(blob.get(field)))
                    for label, field in DETAIL_FIELDS)


def merge_tolerances(baseline_tolerances, overrides):
    tols = {k: dict(v) for k, v in DEFAULT_TOLERANCES.items()}
    for source in (baseline_tolerances or {}, overrides or {}):
        for field, spec in source.items():
            tols.setdefault(field, {}).update(spec)
    return tols


def parse_tolerance_args(values):
    """--tolerance BlobArea=rel:0.05 --tolerance CircleX=abs:250"""
    out = {}
    for item in values or []:
        if "=" not in item:
            raise ValueError("bad --tolerance %r, want FIELD=rel:0.05" % item)
        field, spec = item.split("=", 1)
        kind, _, amount = spec.partition(":")
        if kind not in ("rel", "abs") or not amount:
            raise ValueError("bad --tolerance %r, want rel:N or abs:N" % item)
        out.setdefault(field.strip(), {})[kind] = float(amount)
    return out


def fmt(value):
    if value is None:
        return "-"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_baseline(args):
    records = read_records(args.csv, order=args.order)
    if not records:
        print("No data rows found in %s" % args.csv, file=sys.stderr)
        return 2
    images_dir = args.images or default_images_dir(args.csv)
    mapping, unkeyed, duplicates = key_records(records, args.key, images_dir)

    doc = {
        "format": FORMAT,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source_csv": os.path.abspath(args.csv),
        "join_key": args.key,
        "blob_order": args.order,
        "note": ("Expected values captured from source_csv as-is. This records "
                 "what the job did, not what it should do -- baseline a run you "
                 "have actually confirmed is good."),
        "tolerances": {},
        "images": OrderedDict(
            (k, {"image": r["image"], COUNT_FIELD: r[COUNT_FIELD], "blobs": r["blobs"]})
            for k, r in mapping.items()
        ),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")

    print("Baseline written to %s" % args.out)
    print("  source      %s" % args.csv)
    print("  images      %d  (join key: %s, blob order: %s)"
          % (len(mapping), args.key, args.order))
    if unkeyed:
        print("  unkeyed     %d  (no BMP found under %s)" % (len(unkeyed), images_dir))
        for name in unkeyed[:5]:
            print("                %s" % name)
    if duplicates:
        print("  duplicates  %d  (same key seen twice, later rows dropped)" % len(duplicates))
    print("")
    print("Tolerances are defaults; edit the \"tolerances\" block of the JSON to")
    print("set per-field limits for this validation set.")
    return 0


def cmd_run(args):
    with open(args.baseline, "r", encoding="utf-8") as f:
        try:
            doc = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError("%s is not valid JSON (%s)" % (args.baseline, exc))
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        print("%s is not a %s baseline" % (args.baseline, FORMAT), file=sys.stderr)
        return 2

    join_key = args.key or doc.get("join_key", "imagename")
    order = args.order or doc.get("blob_order", "position")
    tolerances = merge_tolerances(doc.get("tolerances"), parse_tolerance_args(args.tolerance))
    fields = set(args.fields.split(",")) if args.fields else set(BLOB_FIELDS)

    records = read_records(args.csv, order=order)
    images_dir = args.images or default_images_dir(args.csv)
    actual, unkeyed, _ = key_records(records, join_key, images_dir)
    expected = doc.get("images", {})

    passed, failed, missing = [], [], []
    for key, exp in expected.items():
        act = actual.get(key)
        if act is None:
            missing.append(exp.get("image", key))
            continue
        fails = compare(exp, act, tolerances, fields, metrics=args.metrics)
        if fails:
            failed.append((act["image"], fails, exp, act))
        else:
            passed.append((act["image"], fails))
    unexpected = [r["image"] for k, r in actual.items() if k not in expected]

    report = {
        "csv": os.path.abspath(args.csv),
        "baseline": os.path.abspath(args.baseline),
        "join_key": join_key,
        "blob_order": order,
        "passed": len(passed),
        "failed": len(failed),
        "missing": missing,
        "unexpected": unexpected,
        "unkeyed": unkeyed,
        "failures": [{"image": img, "tests": [
            {"field": p, "expected": e, "actual": a} for p, e, a in fails]}
            for img, fails, _exp, _act in failed],
    }

    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print("Job Validation - %s" % os.path.basename(args.csv))
        print("Baseline: %s  (%d images, captured %s)"
              % (os.path.basename(args.baseline), len(expected), doc.get("created", "?")))
        print("Join key: %s   Blob order: %s" % (join_key, order))
        print("")
        shown = failed if args.max_failures <= 0 else failed[:args.max_failures]
        for image, fails, exp, act in shown:
            print("FAIL  %s" % image)
            for path, e, a in fails:
                print("        %-28s E:%-14s A:%s" % (path, fmt(e), fmt(a)))
            if args.verbose:
                for label, e, a in describe_mismatch(exp, act):
                    print("        %-8s E: %-38s A: %s"
                          % (label, e if e is not None else "-",
                             a if a is not None else "-"))
        if len(shown) < len(failed):
            print("      ... and %d more failing images (--max-failures 0 for all)"
                  % (len(failed) - len(shown)))
        if failed:
            print("")
        print("  passed      %d" % len(passed))
        print("  failed      %d" % len(failed))
        if missing:
            print("  missing     %d   in baseline, absent from this export" % len(missing))
            for name in missing[:5]:
                print("                %s" % name)
        if unexpected:
            print("  unexpected  %d   in this export, absent from baseline" % len(unexpected))
            for name in unexpected[:5]:
                print("                %s" % name)
        if unkeyed:
            print("  unkeyed     %d   could not be joined (no BMP under %s)"
                  % (len(unkeyed), images_dir))
        print("")
        print("VALIDATED" if not failed else "INVALIDATED")

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
            f.write("\n")
        if not args.json:
            print("Report written to %s" % args.report)

    if failed:
        return 1
    if missing and not args.allow_missing:
        return 1
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="ff_validate",
        description="Regression validation for FastForward vision-camera CSV exports.")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("baseline", help="capture an export as the expected results")
    b.add_argument("csv")
    b.add_argument("-o", "--out", default="baseline.json")
    b.add_argument("--key", choices=("imagename", "index", "hash"), default="imagename",
                   help="how to match an image across runs (default: imagename)")
    b.add_argument("--order", choices=("position", "slot"), default="position",
                   help="canonicalize blob order before comparing (default: position)")
    b.add_argument("--images", help="folder holding the BMPs (default: one level above the CSV)")
    b.set_defaults(func=cmd_baseline)

    r = sub.add_parser("run", help="diff an export against a baseline")
    r.add_argument("csv")
    r.add_argument("-b", "--baseline", default="baseline.json")
    r.add_argument("--key", choices=("imagename", "index", "hash"),
                   help="override the baseline's join key")
    r.add_argument("--order", choices=("position", "slot"),
                   help="override the baseline's blob order")
    r.add_argument("--images", help="folder holding the BMPs (default: one level above the CSV)")
    r.add_argument("--metrics", action="store_true",
                   help="also fail on per-blob metric drift (area, radius, position). "
                        "By default only BlobNumResults decides the verdict: a blob "
                        "either registers or it does not, so metric drift is "
                        "diagnostic, not a regression.")
    r.add_argument("--verbose", action="store_true",
                   help="under each failure, list the expected and actual blob slots "
                        "side by side so you can see which feature went missing")
    r.add_argument("--fields", help="comma-separated blob fields to test "
                                    "(only applies with --metrics; default: all)")
    r.add_argument("--tolerance", action="append", metavar="FIELD=rel:N|abs:N",
                   help="override one field's tolerance; repeatable "
                        "(only applies with --metrics)")
    r.add_argument("--max-failures", type=int, default=25,
                   help="cap the failing images printed; 0 for all (default: 25)")
    r.add_argument("--allow-missing", action="store_true",
                   help="do not fail when baseline images are absent from the export")
    r.add_argument("--json", action="store_true", help="emit the report as JSON on stdout")
    r.add_argument("--report", help="also write the JSON report to this path")
    r.set_defaults(func=cmd_run)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
