from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Dict, List, Tuple, Optional

from src.core.items import canonicalise_item_key
from src.core.models import Recipe, RecipeBook
from src.core.oc import VOLTAGE_BY_TIER
from .widgets import AutocompleteEntry, make_search_provider


class AddEditRecipeDialog(tk.Toplevel):
    def __init__(self, master, book: RecipeBook, items_pairs: List[Tuple[str, str]], edit: Recipe | None = None, default_output: Optional[str] = None):
        super().__init__(master)
        self.title("Edit Recipe" if edit else "Add Recipe")
        self.resizable(False, False)
        self.book = book
        self.items_pairs = items_pairs
        self.result: Recipe | None = None
        self._editing_recipe = edit
        self._search_provider = make_search_provider(self.items_pairs)

        frm = ttk.Frame(self, padding=12)
        frm.grid(sticky="nsew")
        row = 0
        ttk.Label(frm, text="Recipe ID").grid(row=row, column=0, sticky="w")
        self.e_id = ttk.Entry(frm, width=40, state="readonly")
        self.e_id.grid(row=row, column=1, sticky="ew")
        row += 1
        ttk.Label(frm, text="Machine").grid(row=row, column=0, sticky="w")
        self.e_machine = ttk.Entry(frm, width=40)
        self.e_machine.grid(row=row, column=1, sticky="ew")
        row += 1
        # Outputs table
        ttk.Label(frm, text="Outputs").grid(row=row, column=0, sticky="w")
        outframe = ttk.Frame(frm)
        outframe.grid(row=row, column=1, sticky="ew")
        row += 1
        self.out_tree = ttk.Treeview(
            outframe,
            columns=("item_id", "item", "amt"),
            displaycolumns=("item", "amt"),
            show="headings",
            height=5,
        )
        self.out_tree.heading("item", text="Item")
        self.out_tree.heading("amt", text="Amount/op")
        self.out_tree.column("item_id", width=0, minwidth=0, stretch=False)
        self.out_tree.column("item", width=280)
        self.out_tree.column("amt", width=80, anchor="e")
        self.out_tree.pack(side="left", fill="both", expand=True)
        outsb = ttk.Scrollbar(outframe, orient="vertical", command=self.out_tree.yview)
        self.out_tree.configure(yscrollcommand=outsb.set)
        outsb.pack(side="right", fill="y")
        obtns = ttk.Frame(frm)
        obtns.grid(row=row, column=1, sticky="e")
        row += 1
        ttk.Button(obtns, text="Add", command=lambda: self._edit_row(self.out_tree, is_output=True)).pack(side="left", padx=2)
        ttk.Button(obtns, text="Edit", command=lambda: self._edit_selected(self.out_tree, is_output=True)).pack(side="left", padx=2)
        ttk.Button(obtns, text="Remove", command=lambda: self._remove_selected(self.out_tree, is_output=True)).pack(side="left", padx=2)

        # Inputs table
        ttk.Label(frm, text="Inputs").grid(row=row, column=0, sticky="w")
        inframe = ttk.Frame(frm)
        inframe.grid(row=row, column=1, sticky="ew")
        row += 1
        self.in_tree = ttk.Treeview(
            inframe,
            columns=("item_id", "item", "amt"),
            displaycolumns=("item", "amt"),
            show="headings",
            height=6,
        )
        self.in_tree.heading("item", text="Item")
        self.in_tree.heading("amt", text="Amount/op")
        self.in_tree.column("item_id", width=0, minwidth=0, stretch=False)
        self.in_tree.column("item", width=280)
        self.in_tree.column("amt", width=80, anchor="e")
        self.in_tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(inframe, orient="vertical", command=self.in_tree.yview)
        self.in_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        ibtns = ttk.Frame(frm)
        ibtns.grid(row=row, column=1, sticky="e")
        row += 1
        ttk.Button(ibtns, text="Add", command=lambda: self._edit_row(self.in_tree, is_output=False)).pack(side="left", padx=2)
        ttk.Button(ibtns, text="Edit", command=lambda: self._edit_selected(self.in_tree, is_output=False)).pack(side="left", padx=2)
        ttk.Button(ibtns, text="Remove", command=lambda: self._remove_selected(self.in_tree)).pack(side="left", padx=2)
        ttk.Label(frm, text="Base time (s)").grid(row=row, column=0, sticky="w")
        self.e_time = ttk.Entry(frm, width=12)
        self.e_time.insert(0, "1.0")
        self.e_time.grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(frm, text="Base EU/t (optional)").grid(row=row, column=0, sticky="w")
        self.e_eut = ttk.Entry(frm, width=12)
        self.e_eut.grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(frm, text="Base Voltage (meta)").grid(row=row, column=0, sticky="w")
        self.c_base_v = ttk.Combobox(frm, width=10, values=list(VOLTAGE_BY_TIER.keys()), state="readonly")
        self.c_base_v.set("LV")
        self.c_base_v.grid(row=row, column=1, sticky="w")
        row += 1
        self.var_gt = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="GregTech machine (apply OC)", variable=self.var_gt).grid(row=row, column=1, sticky="w")
        row += 1

        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=2, pady=(8, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="OK", command=self.on_ok).pack(side="right", padx=6)
        self.bind("<Return>", lambda e: self.on_ok())

        # If editing, populate fields
        if edit is not None:
            self._fill_from_recipe(edit)
        elif default_output:
            disp = self._display_for_item(default_output)
            self.out_tree.insert('', 'end', values=(default_output, disp, 1.0))

        self.grab_set()
        self.e_machine.focus_set()
        self._update_recipe_id()

    def _edit_selected(self, tree: ttk.Treeview, is_output: bool):
        sel = tree.selection()
        if not sel:
            return
        iid = sel[0]
        vals = tree.item(iid, 'values')
        if not vals:
            return
        if len(vals) >= 3:
            initial = (vals[0], vals[1], str(vals[2]))
        else:
            # Legacy fallback: values=(item_id, amount)
            item_id = vals[0]
            display_name = self._display_for_item(item_id)
            amt_val = vals[1] if len(vals) > 1 else "1"
            initial = (item_id, display_name, str(amt_val))
        self._edit_row(tree, is_output=is_output, iid=iid, initial=initial)

    def _remove_selected(self, tree: ttk.Treeview, is_output: bool = False):
        sel = tree.selection()
        for iid in sel:
            tree.delete(iid)
        if is_output:
            self._update_recipe_id()

    def _edit_row(self, tree: ttk.Treeview, is_output: bool, iid: str | None = None, initial: Tuple[str, str, str] | None = None):
        win = tk.Toplevel(self)
        win.title("Edit Output" if is_output else "Edit Input")
        ttk.Label(win, text="Item").grid(row=0, column=0, sticky="w")
        e_item = ttk.Entry(win, width=46)
        e_item.grid(row=0, column=1, sticky="ew")
        AutocompleteEntry(e_item, self._search_provider)
        ttk.Label(win, text="Amount/op").grid(row=1, column=0, sticky="w")
        e_amt = ttk.Entry(win, width=12)
        e_amt.grid(row=1, column=1, sticky="w")
        if initial:
            e_item.insert(0, initial[1])
            e_amt.insert(0, initial[2])
        btns = ttk.Frame(win)
        btns.grid(row=2, column=0, columnspan=2, pady=(6,0))
        def save():
            raw_display = e_item.get().strip()
            if not raw_display:
                win.destroy()
                return
            try:
                amt = float(e_amt.get().strip() or '1')
            except Exception:
                amt = 1.0
            try:
                item_id, display_name = self._resolve_item(raw_display)
            except ValueError:
                messagebox.showerror("Invalid Item", "Provide a display name for the item.")
                return
            if iid is None:
                tree.insert('', 'end', values=(item_id, display_name, amt))
            else:
                tree.item(iid, values=(item_id, display_name, amt))
            if is_output:
                self._update_recipe_id()
            win.destroy()
        ttk.Button(btns, text="OK", command=save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right", padx=6)

    def _fill_from_recipe(self, r: Recipe) -> None:
        self._set_recipe_id(r.id)
        self.e_machine.insert(0, r.machine)
        # Outputs
        for it, amt in r.outputs.items():
            disp = self._display_for_item(it)
            self.out_tree.insert('', 'end', values=(it, disp, amt))
        # Inputs
        for it, amt in r.inputs.items():
            disp = self._display_for_item(it)
            self.in_tree.insert('', 'end', values=(it, disp, amt))
        self.e_time.delete(0, tk.END)
        self.e_time.insert(0, str(r.time_s))
        if r.base_eut is not None:
            self.e_eut.insert(0, str(r.base_eut))
        if r.base_voltage:
            self.c_base_v.set(r.base_voltage)
        self.var_gt.set(r.gt_recipe)

    def _collect_rows(self, tree: ttk.Treeview) -> List[Tuple[str, float]]:
        rows: List[Tuple[str, float]] = []
        for iid in tree.get_children():
            values = tree.item(iid, 'values')
            if not values or len(values) < 3:
                continue
            item_id = str(values[0])
            try:
                amt = float(values[2])
                rows.append((item_id, amt))
            except Exception:
                continue
        return rows

    def _resolve_item(self, display_name: str) -> Tuple[str, str]:
        resolver = getattr(self.master, "resolve_display_to_item", None)
        if callable(resolver):
            try:
                return resolver(display_name)
            except ValueError:
                pass
        clean = display_name.strip()
        if not clean:
            raise ValueError("empty display name")
        item_id = canonicalise_item_key(clean)
        return item_id, clean

    def _display_for_item(self, item_id: str) -> str:
        resolver = getattr(self.master, "display_for_item", None)
        if callable(resolver):
            return resolver(item_id)
        return item_id

    def _set_recipe_id(self, rid: str) -> None:
        self.e_id.configure(state="normal")
        self.e_id.delete(0, tk.END)
        if rid:
            self.e_id.insert(0, rid)
        self.e_id.configure(state="readonly")

    def _update_recipe_id(self) -> None:
        if self._editing_recipe is not None:
            self._set_recipe_id(self._editing_recipe.id)
            return
        outputs = self._collect_rows(self.out_tree)
        if not outputs:
            self._set_recipe_id("")
            return
        base_output = outputs[0][0]
        rid = self.book.next_recipe_id(base_output)
        self._set_recipe_id(rid)

    def on_ok(self):
        self._update_recipe_id()
        rid = self.e_id.get().strip()
        if not rid:
            messagebox.showerror("Error", "Add at least one output to auto-generate an ID.")
            return
        machine = self.e_machine.get().strip() or "<unnamed>"
        # outputs from tree
        outputs: Dict[str, float] = {item: amt for item, amt in self._collect_rows(self.out_tree)}
        if not outputs:
            messagebox.showerror("Error", "At least one output is required.")
            return
        try:
            time_s = float(self.e_time.get().strip() or 1.0)
        except Exception:
            time_s = 1.0
        beut_text = self.e_eut.get().strip()
        base_eut = float(beut_text) if beut_text else None
        base_v = self.c_base_v.get() or None

        # Collect inputs
        inputs: Dict[str, float] = {item: amt for item, amt in self._collect_rows(self.in_tree)}

        recipe = Recipe(
            id=rid,
            machine=machine,
            time_s=time_s,
            inputs=inputs,
            outputs=outputs,
            base_eut=base_eut,
            base_voltage=base_v,  # type: ignore[arg-type]
            gt_recipe=bool(self.var_gt.get()),
        )

        self.book.upsert_recipe(recipe, make_active=True)
        self.result = recipe
        self.destroy()


class ManageActivesDialog(tk.Toplevel):
    def __init__(self, master, book: RecipeBook):
        super().__init__(master)
        self.title("Manage Active Recipes by Output")
        self.resizable(True, True)
        self.book = book

        frm = ttk.Frame(self, padding=8)
        frm.grid(sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        cols = ("item_id", "output", "recipe")
        self.tree = ttk.Treeview(frm, columns=cols, displaycolumns=("output", "recipe"), show="headings")
        self.tree.heading("output", text="Output")
        self.tree.heading("recipe", text="Recipe ID")
        self.tree.column("item_id", width=0, minwidth=0, stretch=False)
        self.tree.column("output", width=260, stretch=True)
        self.tree.column("recipe", width=160, stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb = ttk.Scrollbar(frm, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        ysb.grid(row=0, column=1, sticky="ns")
        frm.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)

        # Populate
        resolver = getattr(master, "display_for_item", None)
        for out, rid in sorted(self.book.active_by_output.items()):
            display = resolver(out) if callable(resolver) else out
            self.tree.insert("", "end", values=(out, display, rid))


class TierOverridesDialog(tk.Toplevel):
    def __init__(self, master, items: List[str], default_tier: str, overrides: Dict[str, str], base_map: Dict[str, str | None]):
        super().__init__(master)
        self.title("Tier Overrides")
        self.resizable(True, True)
        self.result: Dict[str, str] | None = None
        self._overrides = dict(overrides)
        frm = ttk.Frame(self, padding=8)
        frm.grid(sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        canvas = tk.Canvas(frm)
        vsb = ttk.Scrollbar(frm, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set, height=360, width=520)
        canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        frm.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)

        ttk.Label(inner, text=f"Default tier: {default_tier}").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))

        self._boxes: Dict[str, ttk.Combobox] = {}
        all_tiers = list(VOLTAGE_BY_TIER.keys())
        for i, it in enumerate(items, start=1):
            base_v = base_map.get(it)
            label_text = it if not base_v else f"{it} (base: {base_v})"
            if base_v and base_v in VOLTAGE_BY_TIER:
                allowed = [t for t in all_tiers if VOLTAGE_BY_TIER[t] >= VOLTAGE_BY_TIER[base_v]]
            else:
                allowed = all_tiers
            ttk.Label(inner, text=label_text).grid(row=i, column=0, sticky="w")
            cb = ttk.Combobox(inner, width=10, state="readonly", values=["(default)"] + allowed)
            cur = self._overrides.get(it)
            if cur and cur in allowed:
                cb.set(cur)
            else:
                cb.set("(default)")
            cb.grid(row=i, column=1, sticky="w", padx=6, pady=2)
            self._boxes[it] = cb

        btns = ttk.Frame(frm)
        btns.grid(row=1, column=0, columnspan=2, sticky="e", pady=(6,0))
        def save():
            out: Dict[str, str] = {}
            for it, cb in self._boxes.items():
                val = cb.get()
                if val and val != "(default)":
                    out[it] = val
            self.result = out
            self.destroy()
        ttk.Button(btns, text="OK", command=save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=6)
