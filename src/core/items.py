from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

try:
    import orjson as _json
except Exception:  # pragma: no cover - fallback
    import json as _json  # type: ignore

from .models import ItemRow, RecipeBook


ITEMS_DB_PATH = Path("data") / "items.json"


def _detect_csv(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:4096]
    except Exception:
        return False
    return ("," in head and head.count("\"") >= 2) or path.suffix.lower() in {".csv"}


def load_items(path: str) -> Tuple[List[ItemRow], Dict[str, str]]:
    """Load an items index.

    Supports the new JSON structure as well as the previous CSV / text files
    for backwards compatibility. Returns (rows, registry_to_display_map).
    """

    p = Path(path)
    rows: List[ItemRow] = []
    reg2disp: Dict[str, str] = {}
    if not p.exists():
        return rows, reg2disp

    if p.suffix.lower() == ".json":
        try:
            data = p.read_bytes()
            obj = _json.loads(data)  # type: ignore[attr-defined]
        except Exception:
            obj = _json.loads(p.read_text(encoding="utf-8"))
        items = obj.get("items") if isinstance(obj, dict) else obj
        if not isinstance(items, Iterable):
            return rows, reg2disp
        for entry in items:
            if not isinstance(entry, dict):
                continue
            raw_registry = str(entry.get("registry") or "").strip()
            item_id = str(entry.get("id") or entry.get("item_id") or raw_registry).strip()
            if not item_id:
                continue
            display = str(entry.get("display") or item_id).strip()
            aliases = entry.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            cleaned_aliases: List[str] = []
            for a in aliases:
                s = str(a).strip()
                if s and s not in cleaned_aliases and s != item_id:
                    cleaned_aliases.append(s)
            if raw_registry and raw_registry != item_id and raw_registry not in cleaned_aliases:
                cleaned_aliases.append(raw_registry)
            row = ItemRow(registry=item_id, display=display, item_id=item_id, aliases=cleaned_aliases)
            rows.append(row)
            reg2disp[item_id] = display
            for alias in cleaned_aliases:
                reg2disp[alias] = display
        return rows, reg2disp

    if _detect_csv(p):
        with p.open(newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.reader(f)
            first = next(reader, None)
            # Optional header
            if not (
                first
                and len(first) >= 2
                and first[0].upper().startswith("REGISTRY")
            ):
                if first:
                    reg = first[0].strip()
                    disp = first[1].strip() if len(first) > 1 else reg
                    row = ItemRow(registry=reg, display=disp)
                    rows.append(row)
                    reg2disp[reg] = disp
            for row in reader:
                if not row:
                    continue
                reg = row[0].strip()
                disp = row[1].strip() if len(row) > 1 else row[0].strip()
                rows.append(ItemRow(registry=reg, display=disp, item_id=reg))
                reg2disp[reg] = disp
        return rows, reg2disp

    # Plain text fallback: one id per line; display==registry
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        rows.append(ItemRow(registry=s, display=s, item_id=s))
        reg2disp[s] = s

    return rows, reg2disp


def _split_item_token(token: str) -> Tuple[str, str]:
    token = token.strip()
    if token.startswith("[") and "]" in token and token.endswith(")"):
        try:
            close = token.index("]")
            display = token[1:close]
            inner = token[close + 1:].strip()
            if inner.startswith("(") and inner.endswith(")"):
                registry = inner[1:-1]
                return registry.strip(), display.strip()
        except ValueError:
            pass
    return token, token


def _sanitize_id(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "item"


def _format_display(display: str, registry: str) -> str:
    candidate = display.strip() if display else ""
    if not candidate or candidate == registry:
        candidate = registry
    candidate = candidate.strip()
    if ":" in candidate:
        candidate = candidate.split(":", 1)[1]
    candidate = candidate.replace("_mb", " (mB)")
    candidate = candidate.replace("_", " ")
    words = []
    for w in candidate.split():
        if w.lower() == "mb":
            words.append("mB")
        else:
            words.append(w.capitalize())
    formatted = " ".join(words).strip()
    return formatted or registry


def canonicalise_item_key(raw_item: str) -> str:
    registry, disp_hint = _split_item_token(raw_item)
    base_source = registry or raw_item or disp_hint
    if not base_source:
        base_source = _format_display(disp_hint, registry)
    item_id = _sanitize_id(base_source)
    return item_id or "item"


def build_items_index(book: RecipeBook) -> List[ItemRow]:
    items: Dict[str, ItemRow] = {}

    def ensure_item(raw_item: str) -> str:
        registry, disp_hint = _split_item_token(raw_item)
        friendly = _format_display(disp_hint, registry)
        item_id = canonicalise_item_key(raw_item)

        aliases: Set[str] = set()
        for candidate in (raw_item, registry):
            if candidate and candidate != item_id:
                aliases.add(candidate)

        row = items.get(item_id)
        if row:
            if row.display == row.registry and friendly:
                row.display = friendly
            elif not row.display and friendly:
                row.display = friendly
            row.aliases = sorted({*row.aliases, *aliases})
        else:
            items[item_id] = ItemRow(
                registry=item_id,
                display=friendly or item_id,
                item_id=item_id,
                aliases=sorted(aliases),
            )
        return item_id

    for recipe in book.recipes:
        for raw_item in recipe.outputs.keys():
            ensure_item(raw_item)
        for raw_item in recipe.inputs.keys():
            ensure_item(raw_item)

    return sorted(items.values(), key=lambda r: (r.display.lower(), r.registry.lower()))


def save_items_index(rows: List[ItemRow], path: Path | None = None) -> None:
    p = Path(path) if path is not None else ITEMS_DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"items": [row.model_dump() for row in rows]}
    try:
        data = _json.dumps(payload, option=getattr(_json, "OPT_INDENT_2", 0))  # type: ignore[attr-defined]
        if isinstance(data, str):
            data = data.encode("utf-8")
        p.write_bytes(data)
    except Exception:
        p.write_text(_json.dumps(payload, indent=2))
