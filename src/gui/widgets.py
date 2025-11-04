from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Tuple

try:
    from rapidfuzz import process, fuzz
except Exception:  # pragma: no cover
    process = None  # type: ignore
    fuzz = None  # type: ignore


def _search_items(query: str, items: List[Tuple[str, str]], limit: int = 50) -> List[Tuple[str, str]]:
    """Search items by display first, then registry. Returns (display, registry)."""

    if len(query) < 4:
        return []
    q = query.strip()
    if not q:
        return []

    ql = q.lower()
    scored: List[Tuple[Tuple, Tuple[str, str]]] = []
    fuzzy_pool: List[Tuple[str, str]] = []

    for idx, (disp, reg) in enumerate(items):
        dl = disp.lower()
        rl = reg.lower()
        if ql == dl:
            key = (0, idx)
        elif ql == rl:
            key = (1, idx)
        elif dl.startswith(ql):
            key = (2, len(disp), idx)
        elif rl.startswith(ql):
            key = (3, len(reg), idx)
        elif ql in dl:
            key = (4, dl.index(ql), len(disp), idx)
        elif ql in rl:
            key = (5, rl.index(ql), len(reg), idx)
        else:
            fuzzy_pool.append((disp, reg))
            continue
        scored.append((key, (disp, reg)))

    scored.sort(key=lambda x: x[0])
    seen = set()
    results: List[Tuple[str, str]] = []
    for _, pair in scored:
        if pair[1] in seen:
            continue
        seen.add(pair[1])
        results.append(pair)
        if len(results) >= limit:
            return results

    if process and fuzz and fuzzy_pool and len(results) < limit:
        remaining = limit - len(results)
        display_matches = process.extract(q, [d for d, _ in fuzzy_pool], scorer=fuzz.WRatio, limit=remaining * 2)
        for _name, _score, idx in display_matches:
            disp, reg = fuzzy_pool[idx]
            if reg in seen:
                continue
            seen.add(reg)
            results.append((disp, reg))
            if len(results) >= limit:
                break
        if len(results) < limit:
            registry_matches = process.extract(q, [r for _, r in fuzzy_pool], scorer=fuzz.WRatio, limit=remaining * 2)
            for _name, _score, idx in registry_matches:
                disp, reg = fuzzy_pool[idx]
                if reg in seen:
                    continue
                seen.add(reg)
                results.append((disp, reg))
                if len(results) >= limit:
                    break

    if not process or not fuzz:
        # Fallback simple substring search
        if not scored:
            hits = [pair for pair in items if ql in pair[0].lower() or ql in pair[1].lower()]
            for disp, reg in hits:
                if reg in seen:
                    continue
                seen.add(reg)
                results.append((disp, reg))
                if len(results) >= limit:
                    break

    return results[:limit]


class AutocompleteEntry:
    """Attach a popup autocomplete to a ttk.Entry.

    search_provider: Callable[[str], List[Tuple[str, str]]] -> returns (display, registry)
    """

    def __init__(self, entry: ttk.Entry, search_provider: Callable[[str], List[Tuple[str, str]]]):
        self.entry = entry
        self.search_provider = search_provider
        self.popup: tk.Toplevel | None = None
        self.lb: tk.Listbox | None = None
        self.data: List[Tuple[str, str]] = []

        entry.bind("<KeyRelease>", self._on_key)
        entry.bind("<Down>", self._move_down)
        entry.bind("<Up>", self._move_up)
        entry.bind("<Return>", self._accept)
        entry.bind("<Escape>", self._hide)
        entry.bind("<FocusOut>", self._hide)

    def _on_key(self, e=None):
        if e and e.keysym in {"Down", "Up", "Return", "Escape"}:
            return
        if e and e.keysym == "Tab":
            self._hide()
            return
        text = self.entry.get()
        self.data = self.search_provider(text) or []
        if not self.data:
            self._hide()
            return
        self._show_list([d for (d, _) in self.data])

    def _show_list(self, items: List[str]):
        if self.popup is None:
            self.popup = tk.Toplevel(self.entry)
            self.popup.wm_overrideredirect(True)
            self.lb = tk.Listbox(self.popup, width=80, height=min(12, max(3, len(items))))
            self.lb.pack(fill="both", expand=True)
            self.lb.bind("<Double-Button-1>", self._accept)
            self.lb.bind("<Return>", self._accept)
        else:
            assert self.lb is not None
            self.lb.delete(0, tk.END)
        for s in items:
            assert self.lb is not None
            self.lb.insert(tk.END, s)
        x = self.entry.winfo_rootx()
        y = self.entry.winfo_rooty() + self.entry.winfo_height()
        self.popup.geometry(f"+{x}+{y}")
        self.popup.deiconify()
        self.lb.selection_clear(0, tk.END)
        self.lb.selection_set(0)

    def _hide(self, e=None):
        if self.popup is not None:
            self.popup.withdraw()

    def _move_down(self, e=None):
        if not self.lb:
            return
        i = self.lb.curselection()
        if not i:
            self.lb.selection_set(0)
            return "break"
        i = i[0]
        if i < self.lb.size() - 1:
            self.lb.selection_clear(0, tk.END)
            self.lb.selection_set(i + 1)
        return "break"

    def _move_up(self, e=None):
        if not self.lb:
            return
        i = self.lb.curselection()
        if not i:
            self.lb.selection_set(0)
            return "break"
        i = i[0]
        if i > 0:
            self.lb.selection_clear(0, tk.END)
            self.lb.selection_set(i - 1)
        return "break"

    def _accept(self, e=None):
        if not self.lb or not self.data:
            return
        i = self.lb.curselection()
        idx = i[0] if i else 0
        disp, _ = self.data[idx]
        self.entry.delete(0, tk.END)
        self.entry.insert(0, disp)
        self._hide()
        return "break"


class AutocompleteText:
    """Autocomplete for item ids inside a tk.Text field, before ':' delimiter."""

    def __init__(self, text: tk.Text, search_provider: Callable[[str], List[Tuple[str, str]]]):
        self.text = text
        self.search_provider = search_provider
        self.popup: tk.Toplevel | None = None
        self.lb: tk.Listbox | None = None
        self.data: List[Tuple[str, str]] = []

        text.bind("<KeyRelease>", self._on_key, add=True)
        text.bind("<Escape>", self._hide, add=True)
        text.bind("<FocusOut>", self._hide, add=True)

    def _current_token(self):
        idx = self.text.index("insert")
        line_start = f"{idx.split('.')[0]}.0"
        line = self.text.get(line_start, idx)
        colon_pos = line.find(":")
        if colon_pos != -1 and self.text.count(line_start, idx, "chars")[0] > colon_pos:
            return None, None, None
        i = len(line)
        while i > 0 and not line[i - 1].isspace() and line[i - 1] != ":":
            i -= 1
        token = line[i:].strip()
        start = f"{idx.split('.')[0]}.{i}"
        end = idx
        return token, start, end

    def _on_key(self, e=None):
        token, start, end = self._current_token()
        if not token:
            self._hide()
            return
        self.data = self.search_provider(token) or []
        if not self.data:
            self._hide()
            return
        self._show_list([d for (d, _) in self.data])

    def _show_list(self, items: List[str]):
        bbox = self.text.bbox("insert")
        if bbox is None:
            self._hide()
            return
        x, y, w, h = bbox
        absx = self.text.winfo_rootx() + x
        absy = self.text.winfo_rooty() + y + h
        if self.popup is None:
            self.popup = tk.Toplevel(self.text)
            self.popup.wm_overrideredirect(True)
            self.lb = tk.Listbox(self.popup, width=80, height=min(12, max(3, len(items))))
            self.lb.pack(fill="both", expand=True)
            self.lb.bind("<Return>", self._accept)
            self.lb.bind("<Double-Button-1>", self._accept)
            self.lb.bind("<Escape>", self._hide)
        else:
            assert self.lb is not None
            self.lb.delete(0, tk.END)
        for s in items:
            assert self.lb is not None
            self.lb.insert(tk.END, s)
        self.popup.geometry(f"+{absx}+{absy}")
        self.popup.deiconify()
        self.lb.selection_clear(0, tk.END)
        self.lb.selection_set(0)

    def _hide(self, e=None):
        if self.popup is not None:
            self.popup.withdraw()

    def _accept(self, e=None):
        if not self.popup or not self.data:
            return
        i = self.lb.curselection()
        idx = i[0] if i else 0
        disp, _ = self.data[idx]
        token, start, end = self._current_token()
        if token is not None and start is not None and end is not None:
            self.text.delete(start, end)
            self.text.insert(start, disp)
        self._hide()
        return "break"


def make_search_provider(pairs: List[Tuple[str, str]]):
    def _fn(q: str) -> List[Tuple[str, str]]:
        return _search_items(q, pairs)

    return _fn
