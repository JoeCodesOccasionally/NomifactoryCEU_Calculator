from __future__ import annotations

import math
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from .models import Plan, PlanNode, Recipe, RecipeBook, Tier
from .oc import compute_overclock, VOLTAGE_BY_TIER


class Planner:
    def __init__(self, book: RecipeBook, items_map: Dict[str, str]):
        self.book = book
        self.items_map = items_map

    def _primary_output_amount(self, r: Recipe, item: str) -> float:
        amt = r.outputs.get(item)
        if amt is None:
            # fallback to first output if present
            if r.outputs:
                return float(next(iter(r.outputs.values())))
            raise ValueError(f"Recipe {r.id} has no outputs")
        return float(amt)

    def _tier_for_node(self, r: Recipe, default_tier: Tier, overrides: Dict[str, Tier] | None, item: str) -> Tier:
        # Plan precedence: per-item override -> default tier
        tier: Tier = (overrides or {}).get(item, default_tier)  # type: ignore[assignment]
        # Ensure at least the recipe's base voltage if provided
        base_v = r.base_voltage
        if base_v is not None:
            rv = VOLTAGE_BY_TIER.get(base_v)
            tv = VOLTAGE_BY_TIER.get(tier)
            if rv is not None and (tv is None or tv < rv):
                tier = base_v  # type: ignore[assignment]
        return tier

    def _solve(self, item: str, rate_per_s: float, default_tier: Tier, overrides: Dict[str, Tier] | None, path: List[str]) -> PlanNode:
        if item in path:
            raise ValueError(f"Cycle detected: {' -> '.join(path + [item])}")
        r = self.book.get_active_recipe(item)
        if r is None:
            # RAW input
            return PlanNode(
                item=item,
                item_display=self.items_map.get(item),
                item_rate_per_s=rate_per_s,
                recipe_id="<raw>",
                machine="RAW",
                machine_tier=default_tier,  # raw nodes show default tier
                machines_needed=0,
                per_machine_ops_per_s=0.0,
                effective_time_s=0.0,
                effective_eut=0.0,
                overclocks=0,
                inputs=[],
                children=[],
            )

        tier = self._tier_for_node(r, default_tier, overrides, item)

        out_amt = self._primary_output_amount(r, item)
        # overclocking
        if r.base_eut is not None and r.gt_recipe:
            oc = compute_overclock(r.time_s, r.base_eut, tier)
            eff_time_s = oc.seconds
            eff_eut = oc.eut
            overclocks = oc.overclocks
        else:
            # No OC when base_eut missing
            eff_time_s = r.time_s
            eff_eut = 0.0
            overclocks = 0

        ops_per_machine = 1.0 / max(eff_time_s, 1e-12)
        required_ops = rate_per_s / out_amt
        machines = int(math.ceil(required_ops / ops_per_machine))

        # Build children
        inputs_rates: List[Tuple[str, float]] = []
        for in_item, in_amt in r.inputs.items():
            in_rate = required_ops * float(in_amt)
            inputs_rates.append((in_item, in_rate))

        new_path = path + [item]
        children: List[PlanNode] = []
        for in_item, in_rate in inputs_rates:
            child = self._solve(in_item, in_rate, default_tier, overrides, new_path)
            children.append(child)

        node = PlanNode(
            item=item,
            item_display=self.items_map.get(item),
            item_rate_per_s=rate_per_s,
            recipe_id=r.id,
            machine=r.machine,
            machine_tier=tier,
            machines_needed=machines,
            per_machine_ops_per_s=ops_per_machine,
            effective_time_s=eff_time_s,
            effective_eut=eff_eut,
            overclocks=overclocks,
            inputs=inputs_rates,
            children=children,
        )
        return node

    def _summary(self, root: PlanNode) -> Dict[str, Dict[str, float]]:
        agg: Dict[str, Dict[str, float]] = defaultdict(lambda: {"machines": 0.0, "eu_t": 0.0})

        def walk(n: PlanNode) -> None:
            if n.machine != "RAW" and n.machines_needed > 0:
                key = f"{n.machine} [{n.machine_tier}]"
                agg[key]["machines"] += n.machines_needed
                agg[key]["eu_t"] += n.machines_needed * n.effective_eut
            for c in n.children:
                walk(c)

        walk(root)
        # Round machines to int for readability
        return {k: {"machines": int(math.ceil(v["machines"])), "eu_t": v["eu_t"]} for k, v in agg.items()}

    def build_plan(self, target_item: str, target_rate_per_s: float, default_tier: Tier, overrides: Dict[str, Tier] | None = None) -> Plan:
        root = self._solve(target_item, target_rate_per_s, default_tier, overrides, path=[])
        plan = Plan(
            target_item=target_item,
            target_item_display=self.items_map.get(target_item),
            target_rate_per_s=target_rate_per_s,
            nodes=root,
            summary=self._summary(root),
            timestamp=time.time(),
        )
        return plan
