from __future__ import annotations

from typing import Dict, Optional, List, Literal, Tuple
from pydantic import BaseModel, Field


# Voltage tiers used by Nomifactory/GTCE
Tier = Literal[
    "ULV",
    "LV",
    "MV",
    "HV",
    "EV",
    "IV",
    "LuV",
    "ZPM",
    "UV",
    "UHV",
    "UEV",
]


class ItemRow(BaseModel):
    registry: str  # canonical item id (snake_case)
    display: str
    item_id: str | None = None
    aliases: List[str] = Field(default_factory=list)


class Recipe(BaseModel):
    id: str
    machine: str
    time_s: float
    inputs: Dict[str, float] = Field(default_factory=dict)
    outputs: Dict[str, float] = Field(default_factory=dict)
    base_eut: Optional[float] = None
    base_voltage: Optional[Tier] = None
    gt_recipe: bool = True  # if False, do not apply GT overclocking
    operating_voltage: Tier = "LV"  # deprecated; kept for compatibility, not used
    notes: Optional[str] = None


class RecipeBook(BaseModel):
    recipes: List[Recipe] = Field(default_factory=list)
    active_by_output: Dict[str, str] = Field(default_factory=dict)

    def get_active_recipe(self, output_item: str) -> Optional[Recipe]:
        rid = self.active_by_output.get(output_item)
        if not rid:
            return None
        for r in self.recipes:
            if r.id == rid:
                return r
        return None

    def upsert_recipe(self, recipe: Recipe, make_active: bool = True) -> None:
        for i, r in enumerate(self.recipes):
            if r.id == recipe.id:
                # Remove old output mappings that no longer exist
                for out in r.outputs.keys():
                    if self.active_by_output.get(out) == r.id and out not in recipe.outputs:
                        self.active_by_output.pop(out, None)
                self.recipes[i] = recipe
                break
        else:
            self.recipes.append(recipe)
        if make_active:
            for out in recipe.outputs.keys():
                self.active_by_output[out] = recipe.id

    def next_recipe_id(self, output_item: str) -> str:
        base = output_item.strip()
        base = base or "recipe"
        existing = {r.id for r in self.recipes}
        if base not in existing:
            return base
        idx = 2
        while True:
            candidate = f"{base}_{idx}"
            if candidate not in existing:
                return candidate
            idx += 1

    def get_recipe(self, recipe_id: str) -> Optional[Recipe]:
        for r in self.recipes:
            if r.id == recipe_id:
                return r
        return None

    def recipes_for_output(self, output_item: str) -> List[Recipe]:
        return [r for r in self.recipes if output_item in r.outputs]


class PlanNode(BaseModel):
    item: str
    item_display: Optional[str] = None
    item_rate_per_s: float
    recipe_id: str
    machine: str
    machine_tier: Tier
    machines_needed: int
    per_machine_ops_per_s: float
    effective_time_s: float
    effective_eut: float
    overclocks: int
    inputs: List[Tuple[str, float]] = Field(default_factory=list)
    children: List["PlanNode"] = Field(default_factory=list)


class Plan(BaseModel):
    target_item: str
    target_item_display: Optional[str] = None
    target_rate_per_s: float
    nodes: PlanNode
    summary: Dict[str, Dict[str, float]]
    timestamp: float
