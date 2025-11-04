from __future__ import annotations

import csv
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

try:
    import ttkbootstrap as tb  # optional
except Exception:  # pragma: no cover
    tb = None  # type: ignore

try:
    import orjson as _json
except Exception:  # pragma: no cover
    import json as _json  # type: ignore

from src.core.items import (
    ITEMS_DB_PATH,
    build_items_index,
    canonicalise_item_key,
    load_items,
    save_items_index,
)
from src.core.models import Recipe, RecipeBook
from src.core.plan import Planner
from src.core.recipes import load_recipe_book, save_recipe_book
from src.core.oc import VOLTAGE_BY_TIER
from .dialogs import AddEditRecipeDialog, ManageActivesDialog
from .widgets import AutocompleteEntry, make_search_provider
from .views import PlanTree, ChainCanvas


DEFAULT_ITEMS_PATHS = [str(ITEMS_DB_PATH), str(Path("data") / "items_cache.txt"), str(Path("data") / "items_cache.csv")]
DEFAULT_RECIPES_PATH = str(Path("data") / "recipes.json")
DEFAULT_AUTOSAVE_PATH = str(Path("plans") / "_autosave_last_plan.json")
DEFAULT_HISTORY_PATH = str(Path("plans") / "_history.json")
DEFAULT_RECIPE_IMPORT_TEMPLATE = Path("data") / "recipe_import_template.csv"

ITEM_DESC_RE = re.compile(r"^\s*(?P<amount>-?\d+(?:\.\d+)?)\s*(?:[x×]\s*)?(?P<name>.+?)\s*$")
COMMENT_PREFIX = "#"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Nomifactory Production Planner")
        self.geometry("1200x800")
        if tb is not None:
            try:
                tb.Style("cosmo")
            except Exception:
                pass

        # Data
        self.items_pairs: List[Tuple[str, str]] = []  # (display, id)
        self.items_map = {}
        self.item_lookup: dict[str, str] = {}
        self._item_pairs_set: set[Tuple[str, str]] = set()
        self.rb: RecipeBook = RecipeBook()
        self.recipes_path = DEFAULT_RECIPES_PATH

        # Status (create early so loaders can report)
        self.status = tk.StringVar(value="Starting up…")
        self.tier_overrides: dict[str, str] = {}
        self._last_nodes_flat: List[str] = []
        self.history_entries: List[dict] = []

        self._load_recipes()
        self._rebuild_items_from_recipes()
        self._load_items()
        self._load_history()
        self._ensure_import_template()

        # Top bar
        top = ttk.Frame(self, padding=8)
        top.pack(side="top", fill="x")
        ttk.Label(top, text="Target Item").pack(side="left")
        self.e_item = ttk.Entry(top, width=56)
        self.e_item.pack(side="left", padx=(6, 12))
        AutocompleteEntry(self.e_item, make_search_provider(self.items_pairs))
        ttk.Label(top, text="Rate (/s)").pack(side="left")
        self.e_rate = ttk.Entry(top, width=10)
        self.e_rate.insert(0, "1.0")
        self.e_rate.pack(side="left", padx=(6, 12))
        ttk.Label(top, text="Default Tier").pack(side="left")
        self.c_tier = ttk.Combobox(top, width=12, state="readonly")
        self.c_tier["values"] = list(VOLTAGE_BY_TIER.keys())
        self.c_tier.set("LV")
        self.c_tier.pack(side="left", padx=(6, 12))
        ttk.Button(top, text="Build Plan", command=self.build_plan).pack(side="left")
        self.btn_overrides = ttk.Button(top, text="Tier Overrides…", command=self.open_tier_overrides, state="disabled")
        self.btn_overrides.pack(side="left", padx=(6, 0))
        ttk.Button(top, text="History…", command=self.open_history).pack(side="left", padx=(6, 0))

        # Splitter body
        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=8)

        left = PlanTree(body)
        body.add(left, weight=1)
        self.plan_tree = left
        self.plan_tree.tree.bind("<Button-3>", self._on_plan_tree_right_click)

        right = ChainCanvas(body)
        body.add(right, weight=1)
        self.chain_canvas = right

        # Menu
        self._build_menu()

        # Status bar
        self.total_eut_var = tk.StringVar(value="Total EU/t: 0")
        ttk.Label(self, textvariable=self.total_eut_var, anchor="w").pack(fill="x", side="bottom")
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w").pack(fill="x", side="bottom")

    # Data loading
    def _load_items(self):
        self._reset_item_indexes()
        for p in DEFAULT_ITEMS_PATHS:
            path = Path(p)
            if path.exists():
                rows, _ = load_items(p)
                for row in rows:
                    key = (row.item_id or row.registry or "").strip()
                    if not key:
                        continue
                    display = row.display or key
                    self._register_item(key, display)
                    for alias in getattr(row, "aliases", []) or []:
                        alias = alias.strip()
                        if not alias or alias == key:
                            continue
                        self._register_alias(alias, key)
                    registry = (row.registry or "").strip()
                    if registry and registry != key:
                        self._register_alias(registry, key)
                self.items_pairs.sort(key=lambda pair: pair[0].lower())
                self._set_status(f"Loaded {len(rows)} items from {p}")
                return
        self._reset_item_indexes()
        self._set_status("No items file found in data/. Autocomplete disabled.")

    def _reset_item_indexes(self) -> None:
        self.items_pairs = []
        self.items_map = {}
        self.item_lookup = {}
        self._item_pairs_set.clear()

    def _ensure_import_template(self) -> None:
        path = DEFAULT_RECIPE_IMPORT_TEMPLATE
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        template_lines = [
            "Input Description,Output Description,Machine,Op Time,EU/t,Base Voltage,Gregtech Machine y/n",
            "# Example: 1x Oilsands Dust,# Example: 1000x Heavy Oil,Example Machine,10,7,ULV,TRUE",
            "",
        ]
        path.write_text("\n".join(template_lines) + "\n", encoding="utf-8")

    def _parse_recipe_csv(self, path: Path) -> Tuple[int, int, List[str]]:
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        imported = 0
        skipped = 0
        errors: List[str] = []
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                raise ValueError("CSV header missing.")
            required = [
                "Input Description",
                "Output Description",
                "Machine",
                "Op Time",
                "EU/t",
                "Base Voltage",
                "Gregtech Machine y/n",
            ]
            missing = [col for col in required if col not in reader.fieldnames]
            if missing:
                raise ValueError(f"Missing required columns: {', '.join(missing)}")
            for idx, row in enumerate(reader, start=2):
                if self._row_is_comment(row):
                    skipped += 1
                    continue
                try:
                    recipe = self._recipe_from_csv_row(row)
                except Exception as exc:
                    errors.append(f"Row {idx}: {exc}")
                    skipped += 1
                    continue
                self.rb.upsert_recipe(recipe, make_active=True)
                imported += 1
        return imported, skipped, errors

    def _row_is_comment(self, row: dict) -> bool:
        values = [str(v or "").strip() for v in row.values()]
        non_empty = [v for v in values if v]
        if not non_empty:
            return True
        first = non_empty[0]
        if first.startswith(COMMENT_PREFIX):
            return True
        if all(v.startswith(COMMENT_PREFIX) for v in non_empty):
            return True
        return False

    def _recipe_from_csv_row(self, row: dict) -> Recipe:
        outputs = self._parse_item_list(row.get("Output Description", ""), "Output Description")
        if not outputs:
            raise ValueError("Output Description is required.")
        inputs = self._parse_item_list(row.get("Input Description", ""), "Input Description")
        machine = (row.get("Machine") or "").strip() or "<unnamed>"
        try:
            time_s = float((row.get("Op Time") or "").strip() or 1.0)
        except Exception as exc:
            raise ValueError(f"Op Time must be a number: {exc}") from exc
        eut_text = (row.get("EU/t") or "").strip()
        base_eut = float(eut_text) if eut_text else None
        base_voltage = self._normalize_voltage(row.get("Base Voltage"))
        gt_recipe = self._parse_bool(row.get("Gregtech Machine y/n"), default=True)

        first_output_id = next(iter(outputs.keys()))
        rid = self.rb.next_recipe_id(first_output_id)

        return Recipe(
            id=rid,
            machine=machine,
            time_s=time_s,
            inputs=inputs,
            outputs=outputs,
            base_eut=base_eut,
            base_voltage=base_voltage,  # type: ignore[arg-type]
            gt_recipe=gt_recipe,
        )

    def _parse_item_list(self, text: str, column: str) -> Dict[str, float]:
        items: Dict[str, float] = {}
        tokens = [part.strip() for part in (text or "").split(",") if part and part.strip()]
        if not tokens:
            return items
        for part in tokens:
            match = ITEM_DESC_RE.match(part)
            if match:
                try:
                    amount = float(match.group("amount"))
                except Exception as exc:
                    raise ValueError(f"{column}: invalid quantity in '{part}'.") from exc
                name = match.group("name").strip()
            else:
                name = part.strip()
                if not name:
                    continue
                amount = 1.0
            item_id, _ = self.resolve_display_to_item(name)
            items[item_id] = items.get(item_id, 0.0) + amount
        if not items:
            raise ValueError(f"{column}: no items parsed from '{text}'.")
        return items

    def _parse_bool(self, value: Optional[str], default: bool = True) -> bool:
        if value is None:
            return default
        s = value.strip().lower()
        if not s:
            return default
        if s in {"true", "t", "yes", "y", "1"}:
            return True
        if s in {"false", "f", "no", "n", "0"}:
            return False
        raise ValueError(f"Invalid boolean value '{value}'")

    def _normalize_voltage(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        s = value.strip()
        if not s:
            return None
        for tier in VOLTAGE_BY_TIER.keys():
            if tier.lower() == s.lower():
                return tier
        raise ValueError(f"Unknown base voltage '{value}'")

    def _register_item(self, item_id: str, display: str, *, sort_now: bool = False) -> None:
        item_id = (item_id or "").strip()
        display = (display or "").strip()
        if not item_id:
            if not display:
                return
            item_id = canonicalise_item_key(display)
        if not display:
            display = item_id
        self.items_map[item_id] = display
        self.item_lookup[item_id] = item_id
        self.item_lookup[item_id.lower()] = item_id
        self.item_lookup[display] = item_id
        self.item_lookup[display.lower()] = item_id
        pair = (display, item_id)
        if pair not in self._item_pairs_set:
            self._item_pairs_set.add(pair)
            self.items_pairs.append(pair)
            if sort_now:
                self.items_pairs.sort(key=lambda item: item[0].lower())

    def _register_alias(self, alias: str, item_id: str) -> None:
        alias = (alias or "").strip()
        if not alias:
            return
        canonical = (item_id or "").strip()
        if not canonical:
            return
        self.item_lookup.setdefault(alias, canonical)
        self.item_lookup.setdefault(alias.lower(), canonical)

    def resolve_display_to_item(self, display_name: str) -> Tuple[str, str]:
        clean = (display_name or "").strip()
        if not clean:
            raise ValueError("Empty display name")
        key = self.item_lookup.get(clean)
        if key is None:
            key = self.item_lookup.get(clean.lower())
        if key:
            display = self.display_for_item(key)
            return key, display
        item_id = canonicalise_item_key(clean)
        display = clean
        self._register_item(item_id, display, sort_now=True)
        return item_id, display

    def display_for_item(self, item_id: str) -> str:
        clean_id = (item_id or "").strip()
        if not clean_id:
            return ""
        display = self.items_map.get(clean_id)
        if display:
            return display
        fallback = clean_id.replace("_", " ").strip().title()
        self._register_item(clean_id, fallback, sort_now=True)
        return fallback

    def _load_recipes(self):
        self.rb = load_recipe_book(DEFAULT_RECIPES_PATH)
        self._set_status(f"Loaded recipes from {DEFAULT_RECIPES_PATH}")

    def _rebuild_items_from_recipes(self):
        rows = build_items_index(self.rb)
        save_items_index(rows, ITEMS_DB_PATH)

    def _save_recipes(self):
        save_recipe_book(self.rb, DEFAULT_RECIPES_PATH)
        self._set_status(f"Recipes saved -> {DEFAULT_RECIPES_PATH}")

    # UI helpers
    def _build_menu(self):
        m = tk.Menu(self)
        self.config(menu=m)
        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label="Save Recipes", command=self._save_recipes)
        fm.add_command(label="Reload Items/Recipes", command=self._reload_all)
        fm.add_command(label="Import Recipes from CSV…", command=self.import_recipes_from_csv)
        fm.add_separator()
        fm.add_command(label="Add Recipe", command=self.add_recipe_dialog)
        fm.add_command(label="Manage Active by Output", command=self.manage_actives_dialog)
        fm.add_separator()
        fm.add_command(label="Exit", command=self.destroy)
        m.add_cascade(label="File", menu=fm)

    def _reload_all(self):
        self._load_recipes()
        self._rebuild_items_from_recipes()
        self._load_items()

    def _set_status(self, s: str):
        self.status.set(s)

    # Actions
    def add_recipe_dialog(self):
        dlg = AddEditRecipeDialog(self, self.rb, self.items_pairs)
        self.wait_window(dlg)
        if dlg.result:
            # Auto-save recipes so they persist immediately
            self._save_recipes()
            self._rebuild_items_from_recipes()
            self._load_items()
            self._set_status(f"Added/Updated recipe {dlg.result.id}. Saved and items refreshed.")

    def manage_actives_dialog(self):
        dlg = ManageActivesDialog(self, self.rb)
        self.wait_window(dlg)

    def import_recipes_from_csv(self):
        initial_dir = DEFAULT_RECIPE_IMPORT_TEMPLATE.parent
        path = filedialog.askopenfilename(
            title="Import Recipes from CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialdir=str(initial_dir),
        )
        if not path:
            return
        try:
            imported, skipped, errors = self._parse_recipe_csv(Path(path))
        except Exception as exc:
            messagebox.showerror("Import Failed", f"Could not import recipes:\n{exc}")
            return

        if imported:
            self._save_recipes()
            self._rebuild_items_from_recipes()
            self._load_items()

        lines = [
            f"Imported {imported} recipe(s).",
            f"Skipped {skipped} row(s).",
        ]
        if errors:
            preview = "\n".join(errors[:5])
            lines.extend(["", "Issues:", preview])
            if len(errors) > 5:
                lines.append(f"… {len(errors) - 5} more issue(s).")
        messagebox.showinfo("Recipe Import", "\n".join(lines))
        self._set_status(f"Recipe import complete: {imported} added, {skipped} skipped.")

    def build_plan(self):
        raw_item = self.e_item.get().strip()
        if not raw_item:
            messagebox.showerror("Error", "Target item required.")
            return
        try:
            item, display_name = self.resolve_display_to_item(raw_item)
        except ValueError:
            messagebox.showerror("Error", "Target item required.")
            return
        self.e_item.delete(0, tk.END)
        self.e_item.insert(0, display_name)
        try:
            rate = float(self.e_rate.get().strip())
        except Exception:
            messagebox.showerror("Error", "Rate must be a number.")
            return
        default_tier = self.c_tier.get() or "LV"
        try:
            planner = Planner(self.rb, self.items_map)
            plan = planner.build_plan(item, rate, default_tier, overrides=self.tier_overrides)
        except Exception as e:
            messagebox.showerror("Build failed", str(e))
            return

        self.plan_tree.fill(plan.nodes)
        self.chain_canvas.draw_plan(plan.nodes)
        # Autosave
        self._autosave_plan(plan)
        self._set_status(f"Plan built. Autosaved -> {DEFAULT_AUTOSAVE_PATH}")
        # Enable overrides after a plan exists
        self.btn_overrides.configure(state="normal")
        # Cache nodes list for dialog
        self._last_nodes_flat = self._flatten_nodes(plan.nodes)
        valid = set(self._last_nodes_flat)
        self.tier_overrides = {k: v for k, v in self.tier_overrides.items() if k in valid}
        total_eut = self._compute_total_eut(plan.nodes)
        self.total_eut_var.set(f"Total EU/t: {total_eut:.2f} => Total RF/t {total_eut*4:.2f}")
        self._record_history(item, rate, default_tier, self.tier_overrides)

    def _flatten_nodes(self, root):
        out = []
        def walk(n):
            out.append(n.item)
            for c in n.children:
                walk(c)
        walk(root)
        # uniq preserve order
        seen=set(); uniq=[]
        for it in out:
            if it in seen:
                continue
            seen.add(it)
            uniq.append(it)
        return uniq

    def open_tier_overrides(self):
        try:
            from .dialogs import TierOverridesDialog
        except Exception as e:
            messagebox.showerror("Error", f"Could not open overrides dialog: {e}")
            return
        items = self._last_nodes_flat
        default_tier = self.c_tier.get() or "LV"
        # Build base_voltage map for items if available
        base_map: dict[str, Optional[str]] = {}
        for out, rid in self.rb.active_by_output.items():
            for r in self.rb.recipes:
                if r.id == rid:
                    base_map[out] = r.base_voltage
        dlg = TierOverridesDialog(self, items, default_tier, self.tier_overrides, base_map)
        self.wait_window(dlg)
        if dlg.result is not None:
            self.tier_overrides = dlg.result
            self._set_status("Tier overrides updated. Rebuild to apply.")

    def _get_recipe(self, recipe_id: str):
        return self.rb.get_recipe(recipe_id)

    def _on_plan_tree_right_click(self, event):
        row = self.plan_tree.tree.identify_row(event.y)
        if not row:
            return
        self.plan_tree.tree.selection_set(row)
        self.plan_tree.tree.focus(row)
        node = self.plan_tree.get_node(row)
        if not node:
            return

        menu = tk.Menu(self, tearoff=0)
        recipes = self.rb.recipes_for_output(node.item)
        has_recipe = node.recipe_id not in {"<raw>", "<none>"}

        if has_recipe:
            menu.add_command(label="Edit Recipe…", command=lambda: self._edit_recipe_node(node))
            menu.add_command(label="Override Voltage…", command=lambda: self._override_item_tier(node))
            menu.add_command(label="Add Recipe…", command=lambda: self._add_recipe_for_item(node.item))
            if len(recipes) > 1:
                menu.add_command(label="Choose Active Recipe…", command=lambda: self._choose_recipe_for_item(node.item))
        else:
            menu.add_command(label="Add Recipe…", command=lambda: self._add_recipe_for_item(node.item))
            if len(recipes) > 0:
                menu.add_command(label="Choose Active Recipe…", command=lambda: self._choose_recipe_for_item(node.item))

        if menu.index("end") is None:
            return

        self._plan_context_menu = menu
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _edit_recipe_node(self, node):
        recipe = self._get_recipe(node.recipe_id)
        if not recipe:
            messagebox.showerror("Error", f"Recipe not found: {node.recipe_id}")
            return
        dlg = AddEditRecipeDialog(self, self.rb, self.items_pairs, edit=recipe)
        self.wait_window(dlg)
        if dlg.result:
            self._save_recipes()
            self._rebuild_items_from_recipes()
            self._load_items()
            self.build_plan()

    def _override_item_tier(self, node):
        recipe = self._get_recipe(node.recipe_id)
        base_v = recipe.base_voltage if recipe else None
        tiers = list(VOLTAGE_BY_TIER.keys())
        if base_v and base_v in VOLTAGE_BY_TIER:
            tiers = [t for t in tiers if VOLTAGE_BY_TIER[t] >= VOLTAGE_BY_TIER[base_v]]
        values = ["(default)"] + tiers

        win = tk.Toplevel(self)
        win.title(f"Override Tier – {node.item}")
        ttk.Label(win, text=f"Item: {node.item}").grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 4))
        ttk.Label(win, text="Tier").grid(row=1, column=0, sticky="w", padx=8)
        combo = ttk.Combobox(win, width=12, state="readonly", values=values)
        current = self.tier_overrides.get(node.item)
        if current and current in tiers:
            combo.set(current)
        else:
            combo.set("(default)")
        combo.grid(row=1, column=1, sticky="w", padx=(0, 8), pady=(0, 8))

        btns = ttk.Frame(win)
        btns.grid(row=2, column=0, columnspan=2, pady=(0, 8))

        def apply_and_close():
            val = combo.get()
            if val == "(default)":
                self.tier_overrides.pop(node.item, None)
            else:
                self.tier_overrides[node.item] = val
            win.destroy()
            self.build_plan()

        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 8))
        ttk.Button(btns, text="Apply", command=apply_and_close).pack(side="right", padx=(0, 0))
        combo.bind("<Return>", lambda e: apply_and_close())
        win.grab_set()
        combo.focus_set()

    def _add_recipe_for_item(self, item_id: str):
        dlg = AddEditRecipeDialog(self, self.rb, self.items_pairs, default_output=item_id)
        self.wait_window(dlg)
        if dlg.result:
            self._save_recipes()
            self._rebuild_items_from_recipes()
            self._load_items()
            self.build_plan()

    def _choose_recipe_for_item(self, item_id: str):
        recipes = self.rb.recipes_for_output(item_id)
        if not recipes:
            messagebox.showinfo("Recipes", "No recipes found for this item.")
            return

        win = tk.Toplevel(self)
        win.title(f"Choose Recipe – {item_id}")
        win.resizable(False, True)

        lb = tk.Listbox(win, width=70, height=8)
        active = self.rb.active_by_output.get(item_id)
        for idx, r in enumerate(recipes):
            label = f"{r.id} — {r.machine} (time {r.time_s}s"
            if r.base_voltage:
                label += f", base {r.base_voltage}"
            if r.base_eut is not None:
                label += f", {r.base_eut} EU/t"
            label += ")"
            lb.insert("end", label)
            if r.id == active:
                lb.selection_set(idx)
        lb.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=8, pady=8)

        def choose():
            sel = lb.curselection()
            if not sel:
                return
            recipe = recipes[sel[0]]
            self.rb.active_by_output[item_id] = recipe.id
            self._save_recipes()
            win.destroy()
            self.build_plan()

        ttk.Button(win, text="Cancel", command=win.destroy).grid(row=1, column=0, sticky="e", padx=(8, 4), pady=(0, 8))
        ttk.Button(win, text="Use Recipe", command=choose).grid(row=1, column=1, sticky="w", padx=(4, 8), pady=(0, 8))
        lb.bind("<Double-Button-1>", lambda e: choose())
        win.grab_set()

    def _autosave_plan(self, plan):
        Path(DEFAULT_AUTOSAVE_PATH).parent.mkdir(parents=True, exist_ok=True)
        try:
            data = _json.dumps(plan.model_dump(), option=getattr(_json, "OPT_INDENT_2", 0))  # type: ignore[attr-defined]
            if isinstance(data, str):
                data = data.encode("utf-8")
            Path(DEFAULT_AUTOSAVE_PATH).write_bytes(data)
        except Exception:
            Path(DEFAULT_AUTOSAVE_PATH).write_text(_json.dumps(plan.model_dump(), indent=2))

    def _compute_total_eut(self, node):
        total = 0.0
        if node.machine != "RAW" and node.machines_needed > 0:
            total += node.machines_needed * node.effective_eut
        for child in node.children:
            total += self._compute_total_eut(child)
        return total

    def _history_key(self, entry: dict) -> Tuple:
        overrides = entry.get("overrides", {}) or {}
        return (
            entry.get("item"),
            entry.get("rate"),
            entry.get("tier"),
            tuple(sorted(overrides.items())),
        )

    def _load_history(self):
        p = Path(DEFAULT_HISTORY_PATH)
        if not p.exists():
            self.history_entries = []
            return
        try:
            data = p.read_bytes()
            obj = _json.loads(data)  # type: ignore[attr-defined]
        except Exception:
            try:
                obj = _json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                obj = []
        if isinstance(obj, list):
            cleaned = []
            for entry in obj:
                if not isinstance(entry, dict):
                    continue
                item = entry.get("item")
                rate = entry.get("rate")
                tier = entry.get("tier")
                overrides = entry.get("overrides", {})
                if not isinstance(item, str) or rate is None or not isinstance(tier, str):
                    continue
                if not isinstance(overrides, dict):
                    overrides = {}
                cleaned.append({
                    "item": item,
                    "rate": rate,
                    "tier": tier,
                    "overrides": {str(k): str(v) for k, v in overrides.items()},
                })
            self.history_entries = cleaned[:20]
        else:
            self.history_entries = []

    def _save_history(self):
        Path(DEFAULT_HISTORY_PATH).parent.mkdir(parents=True, exist_ok=True)
        try:
            data = _json.dumps(self.history_entries, option=getattr(_json, "OPT_INDENT_2", 0))  # type: ignore[attr-defined]
            if isinstance(data, str):
                data = data.encode("utf-8")
            Path(DEFAULT_HISTORY_PATH).write_bytes(data)
        except Exception:
            Path(DEFAULT_HISTORY_PATH).write_text(_json.dumps(self.history_entries, indent=2))

    def _record_history(self, item: str, rate: float, tier: str, overrides: dict):
        entry = {
            "item": item,
            "rate": rate,
            "tier": tier,
            "overrides": {k: v for k, v in sorted(overrides.items())},
        }
        key = self._history_key(entry)
        self.history_entries = [e for e in self.history_entries if self._history_key(e) != key]
        self.history_entries.insert(0, entry)
        if len(self.history_entries) > 20:
            self.history_entries = self.history_entries[:20]
        self._save_history()

    def open_history(self):
        if not self.history_entries:
            messagebox.showinfo("History", "No previous plans recorded yet.")
            return

        win = tk.Toplevel(self)
        win.title("Plan History")
        win.resizable(False, True)
        lb = tk.Listbox(win, width=60, height=12)
        for entry in self.history_entries:
            label = f"{entry['item']} @ {entry['rate']} /s [{entry['tier']}]"
            if entry.get("overrides"):
                label += " (overrides)"
            lb.insert("end", label)
        lb.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=6, pady=6)
        lb.selection_set(0)

        def load_selected():
            sel = lb.curselection()
            if not sel:
                return
            entry = self.history_entries[sel[0]]
            self.e_item.delete(0, tk.END)
            self.e_item.insert(0, entry["item"])
            self.e_rate.delete(0, tk.END)
            self.e_rate.insert(0, str(entry["rate"]))
            if entry["tier"] in VOLTAGE_BY_TIER:
                self.c_tier.set(entry["tier"])
            self.tier_overrides = {k: v for k, v in entry.get("overrides", {}).items()}
            win.destroy()
            self.build_plan()

        ttk.Button(win, text="Load", command=load_selected).grid(row=1, column=0, sticky="e", padx=(6, 3), pady=(0, 6))
        ttk.Button(win, text="Close", command=win.destroy).grid(row=1, column=1, sticky="w", padx=(3, 6), pady=(0, 6))
        lb.bind("<Double-Button-1>", lambda e: load_selected())
        win.grab_set()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
