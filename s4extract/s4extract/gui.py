"""Optional simple GUI (Tkinter, ships with Python) for s4extract.

Run:  python -m s4extract.gui
Lets you pick .package files, choose an output folder and outputs, and
extract with a progress log. Useful on Windows for drag-free clicking.
"""
from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from .extractor import Options, extract_package


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("s4extract — Sims 4 .package → FBX/PNG/Unity")
        self.geometry("760x560")
        self.packages: list[str] = []
        self.out_dir = tk.StringVar(value=os.path.abspath("extracted"))

        self._build()

    def _build(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Button(top, text="Add .package…", command=self.add_files).pack(side="left")
        ttk.Button(top, text="Add folder…", command=self.add_folder).pack(side="left", padx=5)
        ttk.Button(top, text="Clear", command=self.clear).pack(side="left")

        self.listbox = tk.Listbox(self, height=8)
        self.listbox.pack(fill="x", padx=10)

        out = ttk.Frame(self, padding=(10, 6))
        out.pack(fill="x")
        ttk.Label(out, text="Output:").pack(side="left")
        ttk.Entry(out, textvariable=self.out_dir).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(out, text="Browse…", command=self.pick_out).pack(side="left")

        opts = ttk.LabelFrame(self, text="Outputs", padding=10)
        opts.pack(fill="x", padx=10, pady=6)
        self.v_obj = tk.BooleanVar(value=True)
        self.v_fbx = tk.BooleanVar(value=True)
        self.v_png = tk.BooleanVar(value=True)
        self.v_mat = tk.BooleanVar(value=True)
        self.v_col = tk.BooleanVar(value=True)
        self.v_prefab = tk.BooleanVar(value=False)
        self.v_dynamic = tk.BooleanVar(value=True)
        self.v_raw = tk.BooleanVar(value=False)
        for text, var in [("OBJ", self.v_obj), ("FBX", self.v_fbx),
                          ("PNG", self.v_png), ("Material", self.v_mat),
                          ("Colliders (V-HACD)", self.v_col),
                          ("Prefab", self.v_prefab),
                          ("Dynamic (Rigidbody)", self.v_dynamic),
                          ("Raw dump", self.v_raw)]:
            ttk.Checkbutton(opts, text=text, variable=var).pack(side="left", padx=4)

        pl = ttk.Frame(self, padding=(10, 0))
        pl.pack(fill="x")
        ttk.Label(pl, text="Render pipeline:").pack(side="left")
        self.v_pipeline = tk.StringVar(value="builtin")
        for p in ("builtin", "urp", "hdrp"):
            ttk.Radiobutton(pl, text=p.upper(), value=p,
                            variable=self.v_pipeline).pack(side="left", padx=4)

        ttk.Button(self, text="Extract", command=self.run).pack(pady=4)

        self.log = tk.Text(self, height=14, wrap="word")
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def add_files(self):
        files = filedialog.askopenfilenames(
            title="Select .package files",
            filetypes=[("Sims 4 package", "*.package"), ("All", "*.*")])
        for f in files:
            if f not in self.packages:
                self.packages.append(f)
                self.listbox.insert("end", f)

    def add_folder(self):
        d = filedialog.askdirectory(title="Select folder with .package files")
        if not d:
            return
        import glob
        for f in sorted(glob.glob(os.path.join(d, "**", "*.package"), recursive=True)):
            if f not in self.packages:
                self.packages.append(f)
                self.listbox.insert("end", f)

    def clear(self):
        self.packages.clear()
        self.listbox.delete(0, "end")

    def pick_out(self):
        d = filedialog.askdirectory(title="Output folder")
        if d:
            self.out_dir.set(d)

    def _log(self, msg):
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.update_idletasks()

    def run(self):
        if not self.packages:
            self._log("No packages selected.")
            return
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        opt = Options(
            out_dir=self.out_dir.get(),
            raw=self.v_raw.get(),
            obj=self.v_obj.get(),
            fbx=self.v_fbx.get(),
            png=self.v_png.get(),
            unity_mat=self.v_mat.get(),
            mat_pipeline=self.v_pipeline.get(),
            colliders=self.v_col.get(),
            prefab=self.v_prefab.get(),
            dynamic=self.v_dynamic.get(),
        )
        for pkg in list(self.packages):
            self._log(f"\n=== {os.path.basename(pkg)} ===")
            try:
                rep = extract_package(pkg, opt)
            except Exception as e:
                self._log(f"  FAILED: {e}")
                continue
            self._log(f"  -> {rep['out_dir']}  ({rep['total_resources']} resources)")
            for m in rep["meshes"]:
                self._log(f"  mesh {m['name']}: {m['verts']}v/{m['faces']}f -> {', '.join(m['files'])}")
            for t in rep["textures"]:
                self._log(f"  tex  {t['file']}: {t['status']}")
            for mat in rep["materials"]:
                self._log(f"  mat  {mat['file']} [{mat.get('pipeline','?')}]")
            for pf in rep.get("prefabs", []):
                self._log(f"  prefab {pf['file']}: {pf['collider_method']} "
                          f"({pf['collider_parts']} parts), dynamic={pf['dynamic']}")
            for err in rep["errors"]:
                self._log(f"  ! {err}")
        self._log("\nDone.")


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
