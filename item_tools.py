#!/usr/bin/env python3
"""
Item tools for Nomifactory Production Planner (per-mod counts, drop-by-mod).

Examples:
  python item_tools.py count-mods data/items_cache.csv
  python item_tools.py drop-by-mod data/items_cache.csv --mod gregtech --out data/items_cache_filtered.csv
  python item_tools.py drop-by-mod data/items_cache.csv --mods gregtech,ic2 --in-place
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path
from typing import Iterable, Tuple


def read_items_csv(path: str) -> Iterable[Tuple[str, str]]:
    p = Path(path)
    with p.open(newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        first = next(reader, None)
        if first and len(first) >= 2 and first[0].upper().startswith("REGISTRY"):
            pass
        elif first:
            yield first[0].strip(), (first[1].strip() if len(first) > 1 else first[0].strip())
        for row in reader:
            if not row:
                continue
            reg = row[0].strip()
            disp = row[1].strip() if len(row) > 1 else row[0].strip()
            yield reg, disp


def count_by_mod(path: str) -> Counter:
    ctr: Counter = Counter()
    for reg, _ in read_items_csv(path):
        mod = reg.split(":", 1)[0] if ":" in reg else "<unknown>"
        ctr[mod] += 1
    return ctr


def write_filtered(path_in: str, out_path: str, drop_mods: set[str]) -> int:
    rows = list(read_items_csv(path_in))
    keep = [(r, d) for (r, d) in rows if (r.split(":", 1)[0] if ":" in r else "") not in drop_mods]
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["REGISTRY_NAME", "DISPLAY_NAME"])
        for r, d in keep:
            w.writerow([r, d])
    return len(rows) - len(keep)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Nomifactory item tools")
    sub = ap.add_subparsers(dest="cmd")

    p_count = sub.add_parser("count-mods", help="Count items by mod id in CSV")
    p_count.add_argument("path")

    p_drop = sub.add_parser("drop-by-mod", help="Drop items for specific mods from CSV")
    p_drop.add_argument("path")
    p_drop.add_argument("--mod", help="Single mod id to drop")
    p_drop.add_argument("--mods", help="Comma-separated mod ids to drop")
    p_drop.add_argument("--out", default=None, help="Output CSV path (default: *_filtered.csv)")
    p_drop.add_argument("--in-place", action="store_true", help="Overwrite the input file")

    args = ap.parse_args(argv)

    if args.cmd == "count-mods":
        ctr = count_by_mod(args.path)
        total = sum(ctr.values())
        print(f"Total items: {total}")
        for mod, n in sorted(ctr.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"{mod}: {n}")
        return 0

    if args.cmd == "drop-by-mod":
        mods: set[str] = set()
        if args.mod:
            mods.add(args.mod)
        if args.mods:
            mods.update([m.strip() for m in args.mods.split(",") if m.strip()])
        if not mods:
            print("No mods specified. Use --mod or --mods.")
            return 2
        out_path = args.path if args.in_place else (args.out or str(Path(args.path).with_name(Path(args.path).stem + "_filtered.csv")))
        dropped = write_filtered(args.path, out_path, mods)
        print(f"Dropped {dropped} rows. Wrote -> {out_path}")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
