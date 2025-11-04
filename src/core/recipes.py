from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    import orjson as _json
except Exception:  # pragma: no cover - fallback
    import json as _json  # type: ignore

from .models import RecipeBook


DEFAULT_RECIPES_PATH = str(Path("data") / "recipes.json")


def load_recipe_book(path: Optional[str] = None) -> RecipeBook:
    p = Path(path or DEFAULT_RECIPES_PATH)
    if not p.exists():
        return RecipeBook()
    data = p.read_bytes()
    try:
        obj = _json.loads(data)  # type: ignore[attr-defined]
    except Exception:
        obj = _json.loads(data.decode("utf-8", errors="ignore"))
    # Backwards compatibility: allow {} or []
    if not obj:
        return RecipeBook()
    if isinstance(obj, dict):
        return RecipeBook.model_validate(obj)
    # if stored as list of recipes
    return RecipeBook(recipes=obj, active_by_output={})


def save_recipe_book(book: RecipeBook, path: Optional[str] = None) -> None:
    p = Path(path or DEFAULT_RECIPES_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = _json.dumps(book.model_dump(), option=getattr(_json, "OPT_INDENT_2", 0))  # type: ignore[attr-defined]
        if isinstance(data, str):
            data = data.encode("utf-8")
        p.write_bytes(data)
    except Exception:
        p.write_text(_json.dumps(book.model_dump(), indent=2))

