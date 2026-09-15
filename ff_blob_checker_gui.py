import os
import re
import csv
import json
import shutil
import subprocess
import sys
import time
import functools
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from statistics import median, mean
from collections import defaultdict, OrderedDict

import ff_validate as ffv

APP_TITLE = "FastForward Blob Checker"


def guarded(fn):
    """Surface exceptions raised inside a Tkinter callback.

    Tk swallows callback exceptions silently -- the window simply stays up
    showing a half-finished result. Every Job Validation handler is wrapped so
    a failure produces a dialog instead of nothing. Internal helpers stay
    unwrapped and raise plain ValueError; this formats them at the boundary.
    """
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except Exception as exc:
            messagebox.showerror(
                APP_TITLE,
                "%s: %s\n\n%s" % (type(exc).__name__, exc, traceback.format_exc()))
    return wrapper

def parse_csv(csv_path):
    """Parse CSV exported by the vision application. First line is metadata and must be skipped."""
    rows = []
    with open(csv_path, "r", newline="", encoding="utf-8", errors="ignore") as f:
        _ = f.readline()  # skip metadata
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            rows.append(row)
    return rows

def to_float(val, default=float("nan")):
    try:
        return float(val)
    except Exception:
        return default

def extract_model_from_name(name, fallback):
    """Extract N### from a string, else fallback."""
    if not name:
        return fallback
    m = re.search(r"N\d{3}", str(name))
    return m.group(0) if m else fallback

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("900x760")
        self.minsize(820, 600)
        self.resizable(True, True)

        # Vars
        self.csv_path_var = tk.StringVar()
        self.expected_var = tk.StringVar(value="9")

        self.img_from_parent_var = tk.BooleanVar(value=True)
        self.img_dir_var = tk.StringVar()

        self.failed_same_as_csv_var = tk.BooleanVar(value=True)
        self.failed_dir_var = tk.StringVar()

        self.action_mode_var = tk.StringVar(value="move")
        self.save_logs_var = tk.BooleanVar(value=False)

        self.sep_model_var = tk.BooleanVar(value=False)
        self.save_passed_var = tk.BooleanVar(value=False)

        self.minArea = 0
        self.maxArea = 0

        # Job Validation tab. Declared before _build_ui because that tab's
        # widgets bind these at construction time.
        self.val_csv_var = tk.StringVar()
        self.val_baseline_var = tk.StringVar()
        self.val_baseline_info_var = tk.StringVar(value="No baseline selected.")
        self.val_images_auto_var = tk.BooleanVar(value=True)
        self.val_images_var = tk.StringVar()
        self.val_key_var = tk.StringVar(value="imagename")
        self.val_summary_var = tk.StringVar(value="No validation run yet.")
        self.val_tree = None      # ttk.Treeview, set in _build_validation_tab
        self.val_report = None    # last report dict, for Export Report…
        self.val_images_dir = ""  # where the tree's rows resolve their BMPs

        # UI Layout
        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        frm = ttk.Frame(self.nb)
        self.nb.add(frm, text="Blob Checker")

        # CSV
        ttk.Label(frm, text="CSV file to analyze:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.csv_path_var, width=70).grid(row=0, column=1, sticky="we", **pad)
        ttk.Button(frm, text="Browse…", command=self.browse_csv).grid(row=0, column=2, **pad)

        # Expected
        ttk.Label(frm, text="Expected BlobNumResults (max):").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.expected_var, width=10).grid(row=1, column=1, sticky="w", **pad)

        ttk.Separator(frm).grid(row=2, column=0, columnspan=3, sticky="we", padx=10)

        # Images
        ttk.Label(frm, text="Source images folder (where the BMPs live):").grid(row=3, column=0, columnspan=3, sticky="w", **pad)
        ttk.Checkbutton(frm, text="Use folder ONE LEVEL ABOVE the CSV (default)",
                        variable=self.img_from_parent_var,
                        command=self._toggle_img_dir_controls).grid(row=4, column=0, columnspan=3, sticky="w", **pad)
        ttk.Label(frm, text="Or specify a custom images folder:").grid(row=5, column=0, sticky="w", **pad)
        self.img_entry = ttk.Entry(frm, textvariable=self.img_dir_var, width=70, state="disabled")
        self.img_entry.grid(row=5, column=1, sticky="we", **pad)
        ttk.Button(frm, text="Browse…", command=self.browse_img_dir).grid(row=5, column=2, **pad)

        ttk.Separator(frm).grid(row=6, column=0, columnspan=3, sticky="we", padx=10)

        # Failed
        ttk.Label(frm, text="Destination 'failed' folder:").grid(row=7, column=0, columnspan=3, sticky="w", **pad)
        ttk.Checkbutton(frm, text="Use the SAME folder as the CSV (default)",
                        variable=self.failed_same_as_csv_var,
                        command=self._toggle_failed_dir_controls).grid(row=8, column=0, columnspan=3, sticky="w", **pad)
        ttk.Label(frm, text="Or specify a custom failed folder:").grid(row=9, column=0, sticky="w", **pad)
        self.failed_entry = ttk.Entry(frm, textvariable=self.failed_dir_var, width=70, state="disabled")
        self.failed_entry.grid(row=9, column=1, sticky="we", **pad)
        ttk.Button(frm, text="Browse…", command=self.browse_failed_dir).grid(row=9, column=2, **pad)

        ttk.Separator(frm).grid(row=10, column=0, columnspan=3, sticky="we", padx=10)

        # Actions
        ttk.Label(frm, text="When handling images:").grid(row=11, column=0, sticky="w", **pad)
        actions_frame = ttk.Frame(frm)
        actions_frame.grid(row=11, column=1, columnspan=2, sticky="w", **pad)
        ttk.Radiobutton(actions_frame, text="Move (destructive)", value="move", variable=self.action_mode_var).pack(side="left", padx=6)
        ttk.Radiobutton(actions_frame, text="Copy (non-destructive)", value="copy", variable=self.action_mode_var).pack(side="left", padx=6)

        ttk.Checkbutton(frm, text="Save CSV logs (disabled by default)", variable=self.save_logs_var).grid(row=12, column=0, columnspan=3, sticky="w", **pad)
        ttk.Checkbutton(frm, text="Separate failed folders by model number", variable=self.sep_model_var).grid(row=13, column=0, columnspan=3, sticky="w", **pad)
        ttk.Checkbutton(frm, text="Also save passing images (categorized by 1-top / 2-bottom / mixed)", variable=self.save_passed_var).grid(row=14, column=0, columnspan=3, sticky="w", **pad)

        # Buttons
        actions2 = ttk.Frame(frm)
        actions2.grid(row=15, column=0, columnspan=3, sticky="we", **pad)
        ttk.Button(actions2, text="Analyze (no move/copy)", command=self.analyze_only).pack(side="left", padx=6)
        ttk.Button(actions2, text="Analyze & Execute", command=self.analyze_and_execute).pack(side="left", padx=6)

        # Results
        ttk.Label(frm, text="Results:").grid(row=16, column=0, sticky="w", **pad)
        self.text = tk.Text(frm, height=16, wrap="word")
        self.text.grid(row=17, column=0, columnspan=3, sticky="nsew", **pad)

        frm.rowconfigure(17, weight=1)
        frm.columnconfigure(1, weight=1)

        self._toggle_img_dir_controls()
        self._toggle_failed_dir_controls()

        self._build_validation_tab(pad)

    def _toggle_img_dir_controls(self):
        self.img_entry.configure(state="disabled" if self.img_from_parent_var.get() else "normal")

    def _toggle_failed_dir_controls(self):
        self.failed_entry.configure(state="disabled" if self.failed_same_as_csv_var.get() else "normal")

    # ------------------------------------------------------------------
    # Job Validation tab
    #
    # The verdict is BlobNumResults: a blob is a feature that either
    # registers or does not, so 8 detections dropping to 7 is a regression
    # while a blob whose area moved 3% is not. Per-blob metrics appear only
    # as drill-down detail under a failing image.
    #
    # Everything here reads CSVs through ffv.read_records, never this
    # module's own parse_csv -- see CLAUDE.md for why.
    # ------------------------------------------------------------------

    def _build_validation_tab(self, pad):
        vf = ttk.Frame(self.nb)
        self.nb.add(vf, text="Job Validation")

        style = ttk.Style(self)
        style.configure("ValHint.TLabel", foreground="#666666")
        style.configure("ValPass.TLabel", foreground="#1a7f37",
                        font=("TkDefaultFont", 10, "bold"))
        style.configure("ValFail.TLabel", foreground="#b00020",
                        font=("TkDefaultFont", 10, "bold"))
        style.configure("ValIdle.TLabel", font=("TkDefaultFont", 10, "bold"))

        # CSV
        ttk.Label(vf, text="CSV export to validate:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(vf, textvariable=self.val_csv_var, width=70).grid(row=0, column=1, sticky="we", **pad)
        ttk.Button(vf, text="Browse…", command=self.browse_val_csv).grid(row=0, column=2, **pad)

        # Baseline
        ttk.Label(vf, text="Baseline JSON:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(vf, textvariable=self.val_baseline_var, width=70).grid(row=1, column=1, sticky="we", **pad)
        ttk.Button(vf, text="Browse…", command=self.browse_val_baseline).grid(row=1, column=2, **pad)
        ttk.Label(vf, textvariable=self.val_baseline_info_var, style="ValHint.TLabel").grid(
            row=2, column=1, columnspan=2, sticky="w", padx=10)

        ttk.Separator(vf).grid(row=3, column=0, columnspan=3, sticky="we", padx=10, pady=4)

        # Images (only consulted for the hash join key)
        ttk.Checkbutton(vf, text="Images are in the folder ONE LEVEL ABOVE the CSV (default)",
                        variable=self.val_images_auto_var,
                        command=self._val_toggle_images).grid(row=4, column=0, columnspan=3, sticky="w", **pad)
        ttk.Label(vf, text="Or specify an images folder:").grid(row=5, column=0, sticky="w", **pad)
        self.val_img_entry = ttk.Entry(vf, textvariable=self.val_images_var, width=70, state="disabled")
        self.val_img_entry.grid(row=5, column=1, sticky="we", **pad)
        ttk.Button(vf, text="Browse…", command=self.browse_val_images).grid(row=5, column=2, **pad)

        # Join key
        ttk.Label(vf, text="Match images across runs by:").grid(row=6, column=0, sticky="w", **pad)
        ttk.Combobox(vf, textvariable=self.val_key_var, state="readonly", width=14,
                     values=("imagename", "hash", "index")).grid(row=6, column=1, sticky="w", **pad)
        ttk.Label(vf, text="hash = SHA-256 of the BMP; use it when filenames change between captures",
                  style="ValHint.TLabel").grid(row=7, column=1, columnspan=2, sticky="w", padx=10)

        ttk.Separator(vf).grid(row=8, column=0, columnspan=3, sticky="we", padx=10, pady=4)

        # Actions
        btns = ttk.Frame(vf)
        btns.grid(row=9, column=0, columnspan=3, sticky="we", **pad)
        ttk.Button(btns, text="Capture Baseline…", command=self.capture_baseline).pack(side="left", padx=4)
        ttk.Button(btns, text="Run Validation", command=self.run_validation).pack(side="left", padx=4)
        ttk.Button(btns, text="Export Report…", command=self.export_report).pack(side="left", padx=4)
        ttk.Button(btns, text="Expand All", command=self.val_expand_all).pack(side="left", padx=4)
        ttk.Button(btns, text="Collapse All", command=self.val_collapse_all).pack(side="left", padx=4)
        ttk.Button(btns, text="Clear", command=self.clear_validation).pack(side="left", padx=4)

        self.val_summary = ttk.Label(vf, textvariable=self.val_summary_var, style="ValIdle.TLabel")
        self.val_summary.grid(row=10, column=0, columnspan=3, sticky="w", **pad)

        # Results
        tree_frame = ttk.Frame(vf)
        tree_frame.grid(row=11, column=0, columnspan=3, sticky="nsew", **pad)
        self.val_tree = ttk.Treeview(tree_frame, columns=("status", "expected", "actual", "delta"),
                                     show="tree headings", selectmode="browse", height=16)
        self.val_tree.heading("#0", text="Image / detail  (double-click to open)")
        self.val_tree.column("#0", width=250, minwidth=160, stretch=True)
        # Expected/Actual hold the drill-down blob descriptions
        # ("x=117541 y=50625 area=887700 r=2050"), so they need real width.
        for col, label, width, anchor in (("status", "Status", 70, "w"),
                                          ("expected", "Expected", 235, "w"),
                                          ("actual", "Actual", 235, "w"),
                                          ("delta", "Delta", 55, "center")):
            self.val_tree.heading(col, text=label)
            self.val_tree.column(col, width=width, anchor=anchor, stretch=False)
        for tag, color in (("pass", "#1a7f37"), ("fail", "#b00020"),
                           ("missing", "#b26a00"), ("unexpected", "#6a4fbf"),
                           ("unkeyed", "#888888"), ("detail", "#555555")):
            self.val_tree.tag_configure(tag, foreground=color)

        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.val_tree.yview)
        self.val_tree.configure(yscrollcommand=sb.set)
        self.val_tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        # Open the BMP behind a row. Double-click acts on image rows only;
        # detail rows fall through so the tree keeps its normal behavior.
        self.val_tree.bind("<Double-1>", self.on_tree_double_click)
        self.val_menu = tk.Menu(self, tearoff=0)
        self.val_menu.add_command(label="Open image", command=self.open_selected_image)
        self.val_menu.add_command(label="Show in folder", command=self.reveal_selected_image)
        self.val_menu.add_separator()
        self.val_menu.add_command(label="Copy image path", command=self.copy_selected_image_path)
        self.val_tree.bind("<Button-3>", self.on_tree_right_click)

        vf.rowconfigure(11, weight=1)
        vf.columnconfigure(1, weight=1)

        self._val_toggle_images()

    def _val_toggle_images(self):
        self.val_img_entry.configure(
            state="disabled" if self.val_images_auto_var.get() else "normal")

    @guarded
    def browse_val_csv(self):
        path = filedialog.askopenfilename(title="Select the CSV export to validate",
                                          filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        self.val_csv_var.set(path)
        self.val_images_auto_var.set(True)
        self.val_images_var.set(ffv.default_images_dir(path))
        self._val_toggle_images()
        if not self.val_baseline_var.get().strip():
            # Baselines live next to the CSV they describe.
            stem = os.path.splitext(os.path.basename(path))[0]
            self.val_baseline_var.set(os.path.join(os.path.dirname(path), stem + ".baseline.json"))
            if os.path.isfile(self.val_baseline_var.get()):
                self._val_load_baseline_header()
            else:
                self.val_baseline_info_var.set("Baseline does not exist yet — Capture Baseline… to create it.")

    @guarded
    def browse_val_baseline(self):
        path = filedialog.askopenfilename(title="Select a baseline JSON",
                                          filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not path:
            return
        self.val_baseline_var.set(path)
        self._val_load_baseline_header()

    @guarded
    def browse_val_images(self):
        path = filedialog.askdirectory(title="Select the folder containing the BMP images")
        if not path:
            return
        self.val_images_var.set(path)
        self.val_images_auto_var.set(False)
        self._val_toggle_images()

    def _val_load_baseline_header(self):
        """Read a baseline's metadata into the info label and adopt its join key."""
        path = self.val_baseline_var.get().strip()
        doc = self._val_read_baseline(path)
        self.val_key_var.set(doc.get("join_key", "imagename"))
        self.val_baseline_info_var.set(
            "%s · %d images · key=%s · captured %s"
            % (doc.get("format", "?"), len(doc.get("images", {})),
               doc.get("join_key", "?"), doc.get("created", "?")))
        return doc

    def _val_read_baseline(self, path):
        if not path or not os.path.isfile(path):
            raise ValueError("Baseline file not found:\n%s" % path)
        with open(path, "r", encoding="utf-8") as f:
            try:
                doc = json.load(f)
            except json.JSONDecodeError as exc:
                raise ValueError("%s is not valid JSON:\n%s" % (path, exc))
        if not isinstance(doc, dict) or doc.get("format") != ffv.FORMAT:
            raise ValueError("%s is not a %s baseline." % (path, ffv.FORMAT))
        return doc

    def _val_collect_options(self, need_baseline=True):
        """Validate the form. Raises ValueError with user-facing text."""
        csv_path = self.val_csv_var.get().strip()
        if not csv_path or not os.path.isfile(csv_path):
            raise ValueError("Please select a CSV export to validate.")
        baseline = self.val_baseline_var.get().strip()
        if need_baseline and not baseline:
            raise ValueError("Please select a baseline JSON, or capture one first.")

        key = self.val_key_var.get()
        images_dir = (ffv.default_images_dir(csv_path) if self.val_images_auto_var.get()
                      else self.val_images_var.get().strip())
        # Only the hash key touches the filesystem, so don't block the others
        # on an images folder the user never needed to set.
        if key == "hash" and not os.path.isdir(images_dir):
            raise ValueError("The 'hash' join key needs the BMP folder, but this is "
                             "not a directory:\n%s" % images_dir)
        return {"csv": csv_path, "baseline": baseline, "images_dir": images_dir, "key": key}

    def _val_compute(self, opts):
        """Run a validation. Contains no Tk calls, so it stays movable to a
        worker thread if a much larger corpus ever makes hashing too slow."""
        doc = self._val_read_baseline(opts["baseline"])
        order = doc.get("blob_order", "position")
        key = opts["key"] or doc.get("join_key", "imagename")

        records = ffv.read_records(opts["csv"], order=order)
        actual, unkeyed, _dupes = ffv.key_records(records, key, opts["images_dir"])
        expected = doc.get("images", {})
        tolerances = ffv.merge_tolerances(doc.get("tolerances"), None)
        fields = set(ffv.BLOB_FIELDS)

        rows, n_pass, n_fail = [], 0, 0
        for k, exp in expected.items():
            act = actual.get(k)
            if act is None:
                rows.append({"image": exp.get("image", k), "status": "MISSING",
                             "expected": exp.get(ffv.COUNT_FIELD), "actual": None,
                             "detail": []})
                continue
            fails = ffv.compare(exp, act, tolerances, fields, metrics=False)
            status = "FAIL" if fails else "PASS"
            n_fail, n_pass = (n_fail + 1, n_pass) if fails else (n_fail, n_pass + 1)
            rows.append({
                "image": act["image"], "status": status,
                "expected": exp.get(ffv.COUNT_FIELD), "actual": act.get(ffv.COUNT_FIELD),
                "detail": ffv.describe_mismatch(exp, act) if fails else [],
            })
        for k, rec in actual.items():
            if k not in expected:
                rows.append({"image": rec["image"], "status": "UNEXPECTED",
                             "expected": None, "actual": rec.get(ffv.COUNT_FIELD),
                             "detail": []})
        for name in unkeyed:
            rows.append({"image": name, "status": "UNKEYED",
                         "expected": None, "actual": None, "detail": []})

        missing = [r["image"] for r in rows if r["status"] == "MISSING"]
        unexpected = [r["image"] for r in rows if r["status"] == "UNEXPECTED"]
        # Same top-level keys cmd_run builds, so an exported report is
        # diffable against `ff_validate run --report`.
        return {
            "csv": os.path.abspath(opts["csv"]),
            "baseline": os.path.abspath(opts["baseline"]),
            "join_key": key,
            "blob_order": order,
            "passed": n_pass,
            "failed": n_fail,
            "missing": missing,
            "unexpected": unexpected,
            "unkeyed": unkeyed,
            "failures": [{"image": r["image"], "tests": [
                {"field": ffv.COUNT_FIELD, "expected": r["expected"], "actual": r["actual"]}]}
                for r in rows if r["status"] == "FAIL"],
            "rows": rows,
            "baseline_images": len(expected),
            "images_dir": opts["images_dir"],
        }

    @guarded
    def run_validation(self):
        opts = self._val_collect_options()
        self.val_summary_var.set("Running validation…")
        self.val_summary.configure(style="ValIdle.TLabel")
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            report = self._val_compute(opts)
        finally:
            self.configure(cursor="")
        self.val_report = report
        self._val_render(report)
        verdict = "VALIDATED" if not report["failed"] and not report["missing"] else "INVALIDATED"
        messagebox.showinfo(APP_TITLE, "%s\n\n%d passed, %d failed, %d missing, %d unexpected."
                            % (verdict, report["passed"], report["failed"],
                               len(report["missing"]), len(report["unexpected"])))

    def _val_render(self, report):
        tree = self.val_tree
        tree.delete(*tree.get_children())
        self.val_images_dir = report.get("images_dir", "")

        rows = report["rows"]
        order = {"FAIL": 0, "MISSING": 1, "UNEXPECTED": 2, "UNKEYED": 3, "PASS": 4}
        rows = sorted(rows, key=lambda r: order.get(r["status"], 9))
        n_failing = sum(1 for r in rows if r["status"] == "FAIL")

        for r in rows:
            e, a = r["expected"], r["actual"]
            delta = ""
            if isinstance(e, (int, float)) and isinstance(a, (int, float)):
                d = a - e
                delta = "%+d" % d if d else ""
            iid = tree.insert("", "end", text=r["image"],
                              values=(r["status"], ffv.fmt(e), ffv.fmt(a), delta),
                              tags=(r["status"].lower(),),
                              open=(r["status"] == "FAIL" and n_failing <= 25))
            for label, exp_desc, act_desc in r["detail"]:
                tree.insert(iid, "end", text=label,
                            values=("", exp_desc or "-", act_desc or "-", ""),
                            tags=("detail",))

        failed, missing = report["failed"], len(report["missing"])
        verdict = "VALIDATED" if not failed and not missing else "INVALIDATED"
        bits = ["%d passed" % report["passed"], "%d failed" % failed]
        if missing:
            bits.append("%d missing" % missing)
        if report["unexpected"]:
            bits.append("%d unexpected" % len(report["unexpected"]))
        if report["unkeyed"]:
            bits.append("%d unkeyed" % len(report["unkeyed"]))
        self.val_summary_var.set(
            "%s — %s  ·  key=%s  ·  %s (%d images)"
            % (verdict, ", ".join(bits), report["join_key"],
               os.path.basename(report["baseline"]), report["baseline_images"]))
        self.val_summary.configure(
            style="ValPass.TLabel" if verdict == "VALIDATED" else "ValFail.TLabel")

    @guarded
    def capture_baseline(self):
        opts = self._val_collect_options(need_baseline=False)
        records = ffv.read_records(opts["csv"], order="position")
        if not records:
            raise ValueError("No data rows found in:\n%s" % opts["csv"])

        if not messagebox.askokcancel(
                APP_TITLE,
                "Capture %d images from this export as the expected results?\n\n"
                "This records what the job DID, not what it should do. Only "
                "baseline a run you have confirmed is good." % len(records)):
            return

        mapping, unkeyed, duplicates = ffv.key_records(records, opts["key"], opts["images_dir"])
        suggested = self.val_baseline_var.get().strip()
        out = filedialog.asksaveasfilename(
            title="Save baseline as", defaultextension=".json",
            initialfile=os.path.basename(suggested) if suggested else "baseline.json",
            initialdir=os.path.dirname(suggested) if suggested else os.path.dirname(opts["csv"]),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not out:
            return

        # Same schema cmd_baseline writes, so GUI- and CLI-produced baselines
        # are interchangeable.
        doc = {
            "format": ffv.FORMAT,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source_csv": os.path.abspath(opts["csv"]),
            "join_key": opts["key"],
            "blob_order": "position",
            "note": ("Expected values captured from source_csv as-is. This records "
                     "what the job did, not what it should do -- baseline a run you "
                     "have actually confirmed is good."),
            "tolerances": {},
            "images": OrderedDict(
                (k, {"image": r["image"], ffv.COUNT_FIELD: r[ffv.COUNT_FIELD],
                     "blobs": r["blobs"]})
                for k, r in mapping.items()),
        }
        with open(out, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")

        self.val_baseline_var.set(out)
        self._val_load_baseline_header()
        self.val_report = None
        self.val_tree.delete(*self.val_tree.get_children())
        self.val_images_dir = opts["images_dir"]
        for key, rec in mapping.items():
            self.val_tree.insert("", "end", text=rec["image"],
                                 values=("CAPTURED", ffv.fmt(rec[ffv.COUNT_FIELD]), "", ""),
                                 tags=("pass",))
        bits = ["%d images captured" % len(mapping)]
        if unkeyed:
            bits.append("%d unkeyed (no BMP found)" % len(unkeyed))
        if duplicates:
            bits.append("%d duplicate keys dropped" % len(duplicates))
        self.val_summary_var.set("Baseline saved — %s  ·  key=%s" % (", ".join(bits), opts["key"]))
        self.val_summary.configure(style="ValIdle.TLabel")
        messagebox.showinfo(APP_TITLE, "Baseline written to:\n%s\n\n%s" % (out, ", ".join(bits)))

    @guarded
    def export_report(self):
        if self.val_report is None:
            raise ValueError("Run a validation first — there is no report to export.")
        out = filedialog.asksaveasfilename(
            title="Export report as", defaultextension=".json", initialfile="validation_report.json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not out:
            return
        payload = {k: v for k, v in self.val_report.items() if k != "rows"}
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        messagebox.showinfo(APP_TITLE, "Report written to:\n%s" % out)

    # --- opening the image behind a row --------------------------------

    def _val_image_path(self, iid):
        """Resolve a tree row to its BMP on disk.

        Detail rows ("slot 3") carry no image of their own, so walk up to the
        top-level row, whose text is the image name. Raises ValueError with
        user-facing text when there is nothing to open.
        """
        if not iid:
            raise ValueError("Select an image row first.")
        while self.val_tree.parent(iid):
            iid = self.val_tree.parent(iid)
        name = self.val_tree.item(iid, "text").strip()
        if not name:
            raise ValueError("That row has no image associated with it.")
        if not self.val_images_dir:
            raise ValueError("No images folder is known yet — run a validation first.")
        path = os.path.join(self.val_images_dir, name)
        if not os.path.isfile(path):
            raise ValueError("Image not found on disk:\n%s\n\nCheck the images folder "
                             "setting — it is currently:\n%s" % (path, self.val_images_dir))
        return path

    def _open_path(self, path):
        """Hand a path to the OS default handler."""
        if hasattr(os, "startfile"):          # Windows, which is what ships
            os.startfile(path)                # noqa: S606 - intended
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    @guarded
    def on_tree_double_click(self, event):
        iid = self.val_tree.identify_row(event.y)
        if not iid or self.val_tree.parent(iid):
            return None                       # detail row: let the tree do its thing
        self._open_path(self._val_image_path(iid))
        return "break"                        # don't also toggle the row open

    @guarded
    def on_tree_right_click(self, event):
        iid = self.val_tree.identify_row(event.y)
        if not iid:
            return
        self.val_tree.selection_set(iid)
        self.val_tree.focus(iid)
        self.val_menu.tk_popup(event.x_root, event.y_root)

    @guarded
    def open_selected_image(self):
        self._open_path(self._val_image_path(self.val_tree.focus()))

    @guarded
    def reveal_selected_image(self):
        path = self._val_image_path(self.val_tree.focus())
        if sys.platform == "win32":
            # /select, needs a native path and must not be quoted by the shell
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            self._open_path(os.path.dirname(path))

    @guarded
    def copy_selected_image_path(self):
        path = self._val_image_path(self.val_tree.focus())
        self.clipboard_clear()
        self.clipboard_append(path)
        self.val_summary_var.set("Copied to clipboard: %s" % path)

    @guarded
    def val_expand_all(self):
        for iid in self.val_tree.get_children():
            self.val_tree.item(iid, open=True)

    @guarded
    def val_collapse_all(self):
        for iid in self.val_tree.get_children():
            self.val_tree.item(iid, open=False)

    @guarded
    def clear_validation(self):
        self.val_tree.delete(*self.val_tree.get_children())
        self.val_report = None
        self.val_summary_var.set("No validation run yet.")
        self.val_summary.configure(style="ValIdle.TLabel")

    def browse_csv(self):
        path = filedialog.askopenfilename(title="Select CSV file",
                                          filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if path:
            self.csv_path_var.set(path)
            csv_dir = os.path.dirname(path)
            parent_dir = os.path.dirname(csv_dir)
            self.img_from_parent_var.set(True)
            self.img_dir_var.set(parent_dir)
            self.failed_same_as_csv_var.set(True)
            self.failed_dir_var.set(csv_dir)
            self._toggle_img_dir_controls()
            self._toggle_failed_dir_controls()

    def browse_img_dir(self):
        path = filedialog.askdirectory(title="Select SOURCE folder containing BMP images")
        if path:
            self.img_dir_var.set(path)
            self.img_from_parent_var.set(False)
            self._toggle_img_dir_controls()

    def browse_failed_dir(self):
        path = filedialog.askdirectory(title="Select destination folder for FAILED images")
        if path:
            self.failed_dir_var.set(path)
            self.failed_same_as_csv_var.set(False)
            self._toggle_failed_dir_controls()

    def analyze_only(self):
        self._run(execute=False)

    def analyze_and_execute(self):
        self._run(execute=True)

    def _resolve_dirs(self):
        csv_path = self.csv_path_var.get().strip()
        if not csv_path or not os.path.exists(csv_path):
            raise ValueError("Please select a valid CSV file.")
        csv_dir = os.path.dirname(csv_path)
        parent_dir = os.path.dirname(csv_dir)

        img_src_dir = parent_dir if self.img_from_parent_var.get() else self.img_dir_var.get().strip()
        if not img_src_dir or not os.path.isdir(img_src_dir):
            raise ValueError("Invalid source images folder.")

        failed_dir = csv_dir if self.failed_same_as_csv_var.get() else self.failed_dir_var.get().strip()
        if not failed_dir:
            raise ValueError("Please select a destination folder for FAILED images.")

        return csv_path, img_src_dir, failed_dir
    
    def parse_blob_area(self, num_results,r,areas):
        
        for i in range(num_results):
            area = int(r.get("BlobArea0"+str(i+1),""))
            areas.append(area)
            if area < self.minArea:
                self.minArea = area
            if area > self.maxArea:
                self.maxArea = area
            return areas
        
    def _run(self, execute=False):
        self.text.delete("1.0", tk.END)

        try:
            csv_path, img_src_dir, failed_dir = self._resolve_dirs()
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))
            return

        try:
            expected_max = int(self.expected_var.get().strip())
        except Exception:
            messagebox.showerror(APP_TITLE, "Expected max must be an integer.")
            return

        try:
            rows = parse_csv(csv_path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Failed to parse CSV:\n{e}")
            return

        base_model = extract_model_from_name(os.path.basename(csv_path), "Unknown")
        total_rows = len(rows)
        under_max = []
        blobAreas = []
        for r in rows:           
            try:
                val = to_float(r.get("BlobNumResults", ""))
                if val != val:  # NaN
                    continue
                if val < expected_max:
                    under_max.append((r.get("ImageName", ""), val))
                blobAreas = self.parse_blob_area(int(val),r,blobAreas)
            except:
                ## without this try/except the last and first line of the csv will throw a fault
                continue

        # Group rows by image
        grouped = defaultdict(list)
        for r in rows:
            img_name = r.get("ImageName") or ""
            if img_name:  # skip rows with missing image name
                grouped[img_name].append(r)

        under_max, passed = [], []

        for img, recs in grouped.items():
            model = extract_model_from_name(img, base_model) if self.sep_model_var.get() else ""
            blob_vals = [to_float(r.get("BlobNumResults", "")) for r in recs if r.get("BlobNumResults", "")]
            if any(v < expected_max for v in blob_vals):
                under_max.append((img, min(blob_vals), model))
            elif all(v == expected_max for v in blob_vals):
                if self.save_passed_var.get():
                    # Collect ModelNumber## values
                    labels = []
                    for r in recs:
                        for k, v in r.items():
                            if k.startswith("ModelNumber") and v.strip():
                                labels.append(v.strip())
                    if labels and all(l == "1" for l in labels):
                        cat = "1-top"
                    elif labels and all(l == "2" for l in labels):
                        cat = "2-bottom"
                    else:
                        cat = "mixed"
                    passed.append((img, expected_max, model, cat))

        ts = time.strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(os.path.dirname(csv_path), f"analysis_log_{ts}.csv") if self.save_logs_var.get() else None

        moved_count, missing_count = 0, 0
        action_desc = self.action_mode_var.get()

        if execute:
            os.makedirs(failed_dir, exist_ok=True)

        def handle_failed(file_list):
            nonlocal moved_count, missing_count
            results = []
            for img_name, val, model in file_list:
                action, note = "", ""
                if execute:
                    sub = os.path.join(failed_dir, model, f"failed_{ts}") if self.sep_model_var.get() else os.path.join(failed_dir, f"failed_{ts}")
                    os.makedirs(sub, exist_ok=True)
                    src, dst = os.path.join(img_src_dir, img_name), os.path.join(sub, img_name)
                    if os.path.exists(src):
                        try:
                            if action_desc == "move":
                                shutil.move(src, dst); action = "moved"
                            else:
                                shutil.copy2(src, dst); action = "copied"
                            moved_count += 1
                        except Exception as e:
                            action, note = "error", str(e)
                    else:
                        action, note = "missing", "source-missing"; missing_count += 1
                results.append([img_name, val, model, "failed", action, note])
            return results

        def handle_passed(file_list):
            nonlocal moved_count, missing_count
            results = []
            for img_name, val, model, cat in file_list:
                action, note = "", ""
                if execute:
                    sub = os.path.join(failed_dir, model, f"passed_{ts}", cat) if self.sep_model_var.get() else os.path.join(failed_dir, f"passed_{ts}", cat)
                    os.makedirs(sub, exist_ok=True)
                    src, dst = os.path.join(img_src_dir, img_name), os.path.join(sub, img_name)
                    if os.path.exists(src):
                        try:
                            if action_desc == "move":
                                shutil.move(src, dst); action = "moved"
                            else:
                                shutil.copy2(src, dst); action = "copied"
                            moved_count += 1
                        except Exception as e:
                            action, note = "error", str(e)
                    else:
                        action, note = "missing", "source-missing"; missing_count += 1
                results.append([img_name, val, model, f"passed-{cat}", action, note])
            return results

        failed_results = handle_failed(under_max)
        passed_results = handle_passed(passed) if self.save_passed_var.get() else []

        if log_path:
            with open(log_path, "w", newline="", encoding="utf-8") as lf:
                w = csv.writer(lf)
                w.writerow(["ImageName", "BlobNumResults", "Model", "Kind", "Action", "Note"])
                w.writerows(failed_results + passed_results)

        # UI
        self.text.insert(tk.END, f"CSV: {csv_path}\nTotal rows: {total_rows}\nExpected max: {expected_max}\n")
        self.text.insert(tk.END, f"Under-max count: {len(under_max)}\n")
        self.text.insert(tk.END, f"Median blob size: " + str(median(blobAreas)) + " Min blob size: " + str(min(blobAreas)) + " Max blob size: " + str(max(blobAreas))+"\n\n")
        
        self.text.insert(tk.END, f"Log written to: {log_path}\n")
        if self.save_passed_var.get():
            self.text.insert(tk.END, f"Passed count: {len(passed)}\n")
        if log_path:
            self.text.insert(tk.END, f"Log written to: {log_path}\n")
        else:
            self.text.insert(tk.END, "Log saving disabled.\n")
        if execute:
            self.text.insert(tk.END, f"Action: {action_desc}\nProcessed: {moved_count}, Missing: {missing_count}\n")

        if under_max:
            self.text.insert(tk.END, "\nUnder-max examples:\n")
            for img_name, val, _ in under_max[:200]:
                self.text.insert(tk.END, f"  {img_name} -> {val}\n")

        messagebox.showinfo(APP_TITLE, f"Done. Under-max: {len(under_max)}, Passed: {len(passed)}")

if __name__ == "__main__":
    App().mainloop()
