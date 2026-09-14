import os
import re
import csv
import shutil
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from statistics import median, mean
from collections import defaultdict

APP_TITLE = "FastForward Blob Checker"

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
        self.geometry("900x700")
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

        # UI Layout
        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True)

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

    def _toggle_img_dir_controls(self):
        self.img_entry.configure(state="disabled" if self.img_from_parent_var.get() else "normal")

    def _toggle_failed_dir_controls(self):
        self.failed_entry.configure(state="disabled" if self.failed_same_as_csv_var.get() else "normal")

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
