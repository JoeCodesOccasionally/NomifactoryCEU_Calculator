#!/usr/bin/env python3
"""
Nomifactory-style production-chain calculator (single-file, no external deps).

Features
--------
- Define recipes (machine name, inputs, outputs, base time, optional base EU/t).
- Item "auto-fill" via fuzzy suggestions from a CraftTweaker-exported item list.
- Compute machines required to reach a target output rate (items per second).
- Includes Nomifactory/GTCE overclocking rules (toggleable per plan).
- Saves/loads recipes and plans as JSON; auto-saves the latest plan to a backup file.
- Simple CLI with subcommands; interactive helpers for adding recipes with suggestions.

Quickstart
----------
1) Export your item IDs (e.g., CraftTweaker dump) to a text file, one id per line.
2) Add a recipe interactively:
   $ python nomi_calc.py items load items.txt
   $ python nomi_calc.py recipe add
3) Plan a chain:
   $ python nomi_calc.py plan build deepmoblearning:polymer_clay --rate 2.0 --tier HV \
       --recipes recipes.json --save-plan my_polymer_chain.json

Notes
-----
- If a recipe omits base_eut, overclocking is disabled for that step and the base time is used.
- Overclocking rules follow https://mrconnerton.github.io/Guides/guides/Overclocking.html
  (Nomifactory v1.2.2): threshold = machine_voltage/4; per overclock duration factor is
  2.0 if base_eut <= 16, else 2.8; EU/t scales by 4^n; min duration is 1 tick.
- Voltages used (EU/t per amp): ULV=8, LV=32, MV=128, HV=512, EV=2048, IV=8192, LuV=32768,
  ZPM=131072, UV=524288, UHV=2097152, UEV=8388608.

"""
from __future__ import annotations
import argparse
import atexit
import difflib
import json
import math
import os
import signal
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

TICK_S = 1.0 / 20.0

# ----------------------------- Voltage tiers -----------------------------
VOLTAGE_BY_TIER: Dict[str, int] = {
    "ULV": 8,
    "LV": 32,
    "MV": 128,
    "HV": 512,
    "EV": 2048,
    "IV": 8192,
    "LuV": 32768,
    "ZPM": 131072,
    "UV": 524288,
    "UHV": 2097152,
    "UEV": 8388608,
}
TIER_ORDER = list(VOLTAGE_BY_TIER.keys())


# ----------------------------- Data models -------------------------------
@dataclass
class Recipe:
    id: str
    machine: str
    time_s: float
    inputs: Dict[str, float]
    outputs: Dict[str, float]
    base_eut: Optional[float] = None  # EU/t at base tier; required for overclocking
    notes: Optional[str] = None

    def to_json(self) -> Dict:
        return {
            "id": self.id,
            "machine": self.machine,
            "time_s": self.time_s,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "base_eut": self.base_eut,
            "notes": self.notes,
        }

    @staticmethod
    def from_json(d: Dict) -> "Recipe":
        return Recipe(
            id=d["id"],
            machine=d["machine"],
            time_s=float(d["time_s"]),
            inputs={k: float(v) for k, v in d.get("inputs", {}).items()},
            outputs={k: float(v) for k, v in d.get("outputs", {}).items()},
            base_eut=(float(d["base_eut"]) if d.get("base_eut") is not None else None),
            notes=d.get("notes"),
        )


class RecipeBook:
    """Stores recipes and an active recipe per output item."""

    def __init__(self) -> None:
        self.recipes: Dict[str, Recipe] = {}  # by recipe id
        self.active_by_output: Dict[str, str] = {}  # output item -> recipe id

    def add(self, r: Recipe, make_active: bool = True) -> None:
        if r.id in self.recipes:
            raise ValueError(f"Recipe id already exists: {r.id}")
        self.recipes[r.id] = r
        # If an output is unique, set it active; otherwise only if requested
        if make_active:
            for out in r.outputs.keys():
                self.active_by_output[out] = r.id

    def set_active(self, output_item: str, recipe_id: str) -> None:
        if recipe_id not in self.recipes:
            raise KeyError(f"Unknown recipe id: {recipe_id}")
        self.active_by_output[output_item] = recipe_id

    def get_active_for(self, item: str) -> Optional[Recipe]:
        rid = self.active_by_output.get(item)
        return self.recipes.get(rid) if rid else None

    def to_json(self) -> Dict:
        return {
            "recipes": [r.to_json() for r in self.recipes.values()],
            "active_by_output": self.active_by_output,
        }

    @staticmethod
    def from_json(d: Dict) -> "RecipeBook":
        rb = RecipeBook()
        for rj in d.get("recipes", []):
            r = Recipe.from_json(rj)
            rb.recipes[r.id] = r
        rb.active_by_output = dict(d.get("active_by_output", {}))
        return rb


# --------------------------- Overclocking rules --------------------------
@dataclass
class OverclockResult:
    ticks: int
    seconds: float
    eut: float
    overclocks: int


class OverclockingRules:
    """Nomifactory/GTCE overclocking rules.

    - A recipe can be overclocked as long as base_eut * 4^n <= machine_voltage/4.
    - Per overclock, duration factor is:
        * 2.0 if base_eut <= 16
        * 2.8 otherwise
    - EU/t scales by 4^n.
    - Duration is tick-rounded (<=16: floor; >16: ceil), min 1 tick.

    If base_eut is None, overclocking is disabled (returns base time/eut as-is).
    """

    def __init__(self, tier: str) -> None:
        if tier not in VOLTAGE_BY_TIER:
            raise ValueError(f"Unknown tier: {tier}")
        self.tier = tier
        self.machine_voltage = VOLTAGE_BY_TIER[tier]

    def apply(self, base_ticks: int, base_eut: Optional[float]) -> OverclockResult:
        if base_eut is None:
            # No overclocking; just return base
            return OverclockResult(
                ticks=max(base_ticks, 1),
                seconds=max(base_ticks, 1) * TICK_S,
                eut=0.0,
                overclocks=0,
            )

        # Basic sanity
        if base_eut > self.machine_voltage:
            raise ValueError(
                f"Recipe base EU/t ({base_eut}) exceeds machine tier voltage ({self.machine_voltage})"
            )

        # Maximum number of overclocks such that base_eut * 4^n <= machine_voltage
        # (matches Nomifactory guide examples: HV can OC <=128 once, <=32 twice, <=8 thrice)
        n = (
            int(math.floor(math.log(self.machine_voltage / base_eut, 4)))
            if base_eut > 0
            else 0
        )
        n = max(0, n)

        # Duration factor
        factor = 2.0 if base_eut <= 16.0 else 2.8
        ticks_f = base_ticks / (factor**n if n > 0 else 1.0)
        # Tick rounding rules
        if base_eut <= 16.0:
            new_ticks = max(1, int(math.floor(ticks_f)))
        else:
            new_ticks = max(1, int(math.ceil(ticks_f)))

        # Ensure we don't overclock past 1 tick
        # If rounding pushed below 1 tick but we still had room to reduce n, step back.
        while new_ticks < 1 and n > 0:
            n -= 1
            ticks_f = base_ticks / (factor**n if n > 0 else 1.0)
            if base_eut <= 16.0:
                new_ticks = max(1, int(math.floor(ticks_f)))
            else:
                new_ticks = max(1, int(math.ceil(ticks_f)))

        eut_eff = base_eut * (4**n)
        return OverclockResult(
            ticks=new_ticks, seconds=new_ticks * TICK_S, eut=eut_eff, overclocks=n
        )


# ----------------------------- Planning core -----------------------------
@dataclass
class PlanNode:
    item: str
    item_rate_per_s: float  # required production rate for this item
    recipe_id: str
    machine: str
    machine_tier: str
    machines_needed: int
    per_machine_ops_per_s: float
    effective_time_s: float
    effective_eut: float  # per machine
    overclocks: int
    inputs: List[Tuple[str, float]] = field(default_factory=list)  # (item, rate/s)
    children: List["PlanNode"] = field(default_factory=list)

    def to_json(self) -> Dict:
        return {
            "item": self.item,
            "item_rate_per_s": self.item_rate_per_s,
            "recipe_id": self.recipe_id,
            "machine": self.machine,
            "machine_tier": self.machine_tier,
            "machines_needed": self.machines_needed,
            "per_machine_ops_per_s": self.per_machine_ops_per_s,
            "effective_time_s": self.effective_time_s,
            "effective_eut": self.effective_eut,
            "overclocks": self.overclocks,
            "inputs": self.inputs,
            "children": [c.to_json() for c in self.children],
        }


@dataclass
class Plan:
    target_item: str
    target_rate_per_s: float
    nodes: PlanNode
    summary: Dict[
        str, Dict[str, float]
    ]  # machine -> {"machines": n, "eu_t": total_eut}
    timestamp: float

    def to_json(self) -> Dict:
        return {
            "target_item": self.target_item,
            "target_rate_per_s": self.target_rate_per_s,
            "nodes": self.nodes.to_json(),
            "summary": self.summary,
            "timestamp": self.timestamp,
        }


class Planner:
    def __init__(self, recipe_book: RecipeBook) -> None:
        self.rb = recipe_book

    def _choose_output_amount(self, r: Recipe, item: str) -> float:
        amt = r.outputs.get(item)
        if not amt:
            raise ValueError(f"Recipe {r.id} does not output {item}")
        return float(amt)

    def _solve_node(
        self,
        item: str,
        rate_per_s: float,
        tier: str,
        visited: set,
    ) -> PlanNode:
        if item in visited:
            raise ValueError(f"Cycle detected while resolving {item}")
        visited.add(item)

        r = self.rb.get_active_for(item)
        if r is None:
            # No recipe; treat as raw input
            return PlanNode(
                item=item,
                item_rate_per_s=rate_per_s,
                recipe_id="<none>",
                machine="<raw input>",
                machine_tier=tier,
                machines_needed=0,
                per_machine_ops_per_s=0.0,
                effective_time_s=0.0,
                effective_eut=0.0,
                overclocks=0,
                inputs=[],
                children=[],
            )

        out_per_op = self._choose_output_amount(r, item)
        base_ticks = max(1, int(round(r.time_s / TICK_S)))

        if r.base_eut is not None:
            oc = OverclockingRules(tier).apply(base_ticks, r.base_eut)
            eff_time_s = oc.seconds
            eff_eut = oc.eut
            overclocks = oc.overclocks
        else:
            eff_time_s = max(base_ticks, 1) * TICK_S
            eff_eut = 0.0
            overclocks = 0

        ops_per_machine_per_s = 1.0 / eff_time_s
        required_ops_per_s = rate_per_s / out_per_op
        machines = int(math.ceil(required_ops_per_s / ops_per_machine_per_s))

        # Compute input rates and recursively build children
        inputs_rates: List[Tuple[str, float]] = []
        for in_item, in_amt in r.inputs.items():
            in_rate = required_ops_per_s * float(in_amt)
            inputs_rates.append((in_item, in_rate))

        children: List[PlanNode] = []
        for in_item, in_rate in inputs_rates:
            child = self._solve_node(in_item, in_rate, tier, visited)
            children.append(child)
        visited.remove(item)

        node = PlanNode(
            item=item,
            item_rate_per_s=rate_per_s,
            recipe_id=r.id,
            machine=r.machine,
            machine_tier=tier,
            machines_needed=machines,
            per_machine_ops_per_s=ops_per_machine_per_s,
            effective_time_s=eff_time_s,
            effective_eut=eff_eut,
            overclocks=overclocks,
            inputs=inputs_rates,
            children=children,
        )
        return node

    def _aggregate_summary(self, node: PlanNode) -> Dict[str, Dict[str, float]]:
        agg: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {"machines": 0.0, "eu_t": 0.0}
        )

        def walk(n: PlanNode):
            if n.machine != "<raw input>" and n.machines_needed > 0:
                key = f"{n.machine} [{n.machine_tier}]"
                agg[key]["machines"] += n.machines_needed
                agg[key]["eu_t"] += n.machines_needed * n.effective_eut
            for c in n.children:
                walk(c)

        walk(node)
        # Convert to floats->rounded for readability
        out = {}
        for k, v in agg.items():
            out[k] = {"machines": int(math.ceil(v["machines"])), "eu_t": v["eu_t"]}
        return out

    def build_plan(self, target_item: str, rate_per_s: float, tier: str) -> Plan:
        root = self._solve_node(target_item, rate_per_s, tier, visited=set())
        summary = self._aggregate_summary(root)
        return Plan(
            target_item=target_item,
            target_rate_per_s=rate_per_s,
            nodes=root,
            summary=summary,
            timestamp=time.time(),
        )


# ----------------------------- Item database -----------------------------
class ItemDB:
    def __init__(self) -> None:
        self.items: List[str] = []

    def load_from_file(self, path: str) -> None:
        with open(path, "r", encoding="utf-8") as f:
            data = [
                line.strip()
                for line in f
                if line.strip() and not line.lstrip().startswith("#")
            ]
        # Normalise to lower-case for matching but keep originals
        self.items = sorted(set(data))

    def suggest(self, query: str, n: int = 10) -> List[str]:
        if not self.items:
            return []
        q = query.strip()
        if not q:
            return self.items[:n]
        # Rank by substring position then difflib ratio
        substr_hits = [it for it in self.items if q in it]
        head = sorted(substr_hits, key=lambda s: s.index(q))[:n]
        if len(head) < n:
            more = [
                m
                for m in difflib.get_close_matches(q, self.items, n=n)
                if m not in head
            ]
            head.extend(more[: max(0, n - len(head))])
        return head[:n]


# --------------------------- Persistence helpers -------------------------
DEFAULT_RECIPES_PATH = "recipes.json"
DEFAULT_AUTOSAVE_PATH = os.path.join("plans", "_autosave_last_plan.json")


def save_recipebook(rb: RecipeBook, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rb.to_json(), f, indent=2)


def load_recipebook(path: str) -> RecipeBook:
    if not os.path.exists(path):
        return RecipeBook()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return RecipeBook.from_json(data)


def save_plan(plan: Plan, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan.to_json(), f, indent=2)


_last_plan_json: Optional[Dict] = None


def autosave_plan_json(plan_json: Dict, path: str = DEFAULT_AUTOSAVE_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan_json, f, indent=2)


# Ensure auto-backup of the latest computed plan on normal exits
@atexit.register
def _autosave_on_exit():
    global _last_plan_json
    if _last_plan_json is not None:
        try:
            autosave_plan_json(_last_plan_json, DEFAULT_AUTOSAVE_PATH)
        except Exception as e:
            sys.stderr.write(f"[autosave warning] {e}\n")


# ------------------------------- CLI layer --------------------------------


def print_plan(node: PlanNode, indent: int = 0) -> None:
    pad = "  " * indent
    if node.machine == "<raw input>":
        print(f"{pad}- RAW INPUT: {node.item}  @ {node.item_rate_per_s:.6g}/s")
        return

    print(
        f"{pad}- {node.machine} [{node.machine_tier}] x{node.machines_needed}  "
        f"-> {node.item} @ {node.item_rate_per_s:.6g}/s  "
        f"(op_time={node.effective_time_s:.4f}s, overclocks={node.overclocks}, per_machine_eut={node.effective_eut:.6g})"
    )
    for in_item, in_rate in node.inputs:
        print(f"{pad}    needs: {in_item}  @ {in_rate:.6g}/s")
    for c in node.children:
        print_plan(c, indent + 1)


def print_summary(summary: Dict[str, Dict[str, float]]) -> None:
    if not summary:
        print("(no machines in summary; target item treated as raw input)")
        return
    print("\n== Machine Summary ==")
    for machine, stats in summary.items():
        print(
            f"- {machine}: {int(stats['machines'])} machines, total ~{stats['eu_t']:.6g} EU/t"
        )


# ----------------------------- Interactive I/O ----------------------------


def prompt(msg: str) -> str:
    try:
        return input(msg)
    except EOFError:
        return ""


def interactive_add_recipe(rb: RecipeBook, items: ItemDB) -> None:
    print("\nAdd a recipe (empty line to cancel at any prompt).\n")
    rid = prompt("Recipe id (unique, e.g. macerate_iron_ore_lv): ").strip()
    if not rid:
        print("Cancelled.")
        return
    machine = prompt("Machine name (e.g. Macerator): ").strip() or "<unnamed>"

    # Output selection with suggestions
    while True:
        q = prompt("Output item id (search/partial ok): ").strip()
        if not q:
            print("Cancelled.")
            return
        sugg = items.suggest(q, n=8)
        if sugg:
            print("  Suggestions:")
            for i, s in enumerate(sugg, 1):
                print(f"   {i}. {s}")
            sel = prompt("Pick # or type full id: ").strip()
            if sel.isdigit() and 1 <= int(sel) <= len(sugg):
                out_item = sugg[int(sel) - 1]
            else:
                out_item = sel
        else:
            out_item = q
        if out_item:
            break

    out_amt = float(prompt("Output amount per op (e.g. 1): ").strip() or "1")

    # Inputs (loop until blank)
    inputs: Dict[str, float] = {}
    print("Enter inputs (blank item to finish).")
    while True:
        q = prompt("  Input item id (search/partial ok): ").strip()
        if not q:
            break
        sugg = items.suggest(q, n=8)
        chosen = q
        if sugg:
            print("    Suggestions:")
            for i, s in enumerate(sugg, 1):
                print(f"     {i}. {s}")
            sel = prompt("    Pick # or type full id: ").strip()
            if sel.isdigit() and 1 <= int(sel) <= len(sugg):
                chosen = sugg[int(sel) - 1]
            else:
                chosen = sel
        amt = float(prompt("  Amount per op: ").strip() or "1")
        inputs[chosen] = amt

    time_s = float(prompt("Base crafting time (seconds): ").strip() or "1.0")
    base_eut_text = prompt(
        "Base EU/t (optional; blank to disable OC for this recipe): "
    ).strip()
    base_eut = float(base_eut_text) if base_eut_text else None

    r = Recipe(
        id=rid,
        machine=machine,
        time_s=time_s,
        inputs=inputs,
        outputs={out_item: out_amt},
        base_eut=base_eut,
    )
    rb.add(r, make_active=True)
    print(f"Added and set active for output {out_item}: {rid}")


# --------------------------------- Main -----------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Nomifactory-style production chain calculator"
    )
    sub = parser.add_subparsers(dest="cmd")

    # items
    pm_items = sub.add_parser("items", help="Manage item list for suggestions")
    sub_items = pm_items.add_subparsers(dest="items_cmd")

    p_items_load = sub_items.add_parser(
        "load", help="Load items from a text file (one id per line)"
    )
    p_items_load.add_argument("path")
    p_items_load.add_argument(
        "--save", default="items_cache.txt", help="Copy to a cache file for later use"
    )

    # recipe
    pm_recipe = sub.add_parser("recipe", help="Manage recipes")
    sub_recipe = pm_recipe.add_subparsers(dest="recipe_cmd")

    p_recipe_add = sub_recipe.add_parser(
        "add", help="Interactively add a recipe with suggestions"
    )
    p_recipe_add.add_argument(
        "--items", default="items_cache.txt", help="Path to item list (for suggestions)"
    )
    p_recipe_add.add_argument(
        "--recipes", default=DEFAULT_RECIPES_PATH, help="Recipes JSON path to save to"
    )

    p_recipe_list = sub_recipe.add_parser(
        "list", help="List active recipes by output item"
    )
    p_recipe_list.add_argument("--recipes", default=DEFAULT_RECIPES_PATH)

    p_recipe_save = sub_recipe.add_parser("save", help="Save recipes to JSON")
    p_recipe_save.add_argument("--recipes", default=DEFAULT_RECIPES_PATH)

    p_recipe_load = sub_recipe.add_parser(
        "load", help="Load recipes from JSON and show a summary"
    )
    p_recipe_load.add_argument("--recipes", default=DEFAULT_RECIPES_PATH)

    # plan
    pm_plan = sub.add_parser("plan", help="Build or inspect plans")
    sub_plan = pm_plan.add_subparsers(dest="plan_cmd")

    p_plan_build = sub_plan.add_parser(
        "build", help="Build a plan for target item and rate"
    )
    p_plan_build.add_argument("item")
    p_plan_build.add_argument(
        "--rate",
        type=float,
        required=True,
        help="Target output rate (items per second)",
    )
    p_plan_build.add_argument(
        "--tier", default="LV", choices=TIER_ORDER, help="Machine tier to run on"
    )
    p_plan_build.add_argument("--recipes", default=DEFAULT_RECIPES_PATH)
    p_plan_build.add_argument(
        "--save-plan", default=None, help="Path to save the computed plan JSON"
    )
    p_plan_build.add_argument(
        "--autosave", default=DEFAULT_AUTOSAVE_PATH, help="Autosave backup path"
    )

    p_plan_show = sub_plan.add_parser("show", help="Pretty-print an existing plan JSON")
    p_plan_show.add_argument("path")

    args = parser.parse_args(argv)

    if args.cmd == "items" and args.items_cmd == "load":
        db = ItemDB()
        db.load_from_file(args.path)
        with open(args.save, "w", encoding="utf-8") as f:
            f.write("\n".join(db.items))
        print(f"Loaded {len(db.items)} items -> cached to {args.save}")
        return 0

    if args.cmd == "recipe" and args.recipe_cmd == "add":
        rb = load_recipebook(args.recipes)
        db = ItemDB()
        if os.path.exists(args.items):
            db.load_from_file(args.items)
        else:
            print(f"[warn] items file not found: {args.items}; suggestions disabled")
        interactive_add_recipe(rb, db)
        save_recipebook(rb, args.recipes)
        print(f"Saved recipes -> {args.recipes}")
        return 0

    if args.cmd == "recipe" and args.recipe_cmd == "list":
        rb = load_recipebook(args.recipes)
        if not rb.active_by_output:
            print("No active recipes set.")
            return 0
        print("Active recipes (by output):")
        for out_item, rid in rb.active_by_output.items():
            r = rb.recipes.get(rid)
            if not r:
                continue
            print(
                f"- {out_item}: {rid}  machine={r.machine}  time={r.time_s}s  base_eut={r.base_eut}"
            )
        return 0

    if args.cmd == "recipe" and args.recipe_cmd == "save":
        rb = load_recipebook(args.recipes)
        save_recipebook(rb, args.recipes)
        print(f"Saved recipes -> {args.recipes}")
        return 0

    if args.cmd == "recipe" and args.recipe_cmd == "load":
        rb = load_recipebook(args.recipes)
        print(
            f"Loaded {len(rb.recipes)} recipes. Active outputs: {len(rb.active_by_output)}"
        )
        for out_item, rid in list(rb.active_by_output.items())[:10]:
            print(f"  - {out_item} -> {rid}")
        if len(rb.active_by_output) > 10:
            print("  ...")
        return 0

    if args.cmd == "plan" and args.plan_cmd == "build":
        rb = load_recipebook(args.recipes)
        if not rb.active_by_output:
            print("No recipes loaded/active; cannot build plan.")
            return 2
        planner = Planner(rb)
        plan = planner.build_plan(args.item, args.rate, args.tier)
        # Pretty print
        print("\n== Plan ==")
        print_plan(plan.nodes)
        print_summary(plan.summary)

        # Persist
        plan_json = plan.to_json()
        global _last_plan_json
        _last_plan_json = plan_json
        # Autosave immediately
        autosave_plan_json(plan_json, args.autosave)
        print(f"\n[autosaved] {args.autosave}")
        if args.save_plan:
            save_plan(plan, args.save_plan)
            print(f"[saved] {args.save_plan}")
        return 0

    if args.cmd == "plan" and args.plan_cmd == "show":
        with open(args.path, "r", encoding="utf-8") as f:
            plan_json = json.load(f)

        # Minimal pretty-printer for stored plans
        def build_node(d: Dict) -> PlanNode:
            return PlanNode(
                item=d["item"],
                item_rate_per_s=d["item_rate_per_s"],
                recipe_id=d["recipe_id"],
                machine=d["machine"],
                machine_tier=d["machine_tier"],
                machines_needed=int(d["machines_needed"]),
                per_machine_ops_per_s=d["per_machine_ops_per_s"],
                effective_time_s=d["effective_time_s"],
                effective_eut=d["effective_eut"],
                overclocks=int(d["overclocks"]),
                inputs=[tuple(x) for x in d.get("inputs", [])],
                children=[build_node(c) for c in d.get("children", [])],
            )

        node = build_node(plan_json["nodes"])
        print("== Plan ==")
        print_plan(node)
        print_summary(plan_json.get("summary", {}))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
