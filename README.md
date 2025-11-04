# Nomifactory CEU Calculator

Nomifactory Production Planner is a Python toolkit for building and inspecting GregTechCEu/Nomifactory production chains. It combines a lightweight command-line interface with a Tk/ttk GUI that visualises entire crafting trees and calculates machine counts, EU/t draw, and overclocking tiers.

Currently tailored for NomifactoryCEU only (overclocking logic etc.) but I plan to expand this to other GregTech packs, and generalise to automation packs in general.

<img width="2255" height="1437" alt="image" src="https://github.com/user-attachments/assets/1934c665-ab71-4eb0-b1d8-43823e5abe4e" />

## Features
- Manually define, or bulk import recipes, then select a target product, the desired rate and machine voltage, and a production chain will be calculated for you.
- Calculate tier-correct machine counts, EU/t draw, and overclocking for any target item.
- Maintain an item-aware recipe library with fuzzy autocomplete and optional CSV import helpers.
- Interactive GUI with split view: hierarchical plan tree + zoomable canvas (exportable to PNG with Pillow).
- Command-line utilities for loading items, adding recipes, building plans, and inspecting saved plans.
- Autosave and history tracking so you can jump between recently generated plans.

## Getting Started
1. Clone the repository:
   ```bash
   git clone https://github.com/<your-user>/NomifactoryCEU_Calculator.git
   cd NomifactoryCEU_Calculator
   ```
2. (Recommended) Create a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install pydantic orjson ttkbootstrap pillow
   ```
   Skip `orjson`, `ttkbootstrap`, or `pillow` if you do not need the related features.

Sample data lives under `data/` (example `items.json`, cached CSV, and `recipes.json`) and ready-made plans are stored in `plans/`. This is currently my personal recipe repository! I plan to include demo datafiles only in this repo for new users, but I don't yet know how to keep that separate from the actual repo (advice welcome).

## GUI Usage
1. Launch the GUI:
   ```bash
   python nomi_calc_gui.py
   ```
2. Select `File -> Add a Recipe` to define your first recipe. (Or go `File -> Import Recipes from CSV` and fill out the template file!)
3. Once one or more recipes are defined, type in your target item in the field at the top. Autocomplete draws from `data/items.json'.
4. Set the desired output rate and default machine tier, then click **Build Plan**.
5. Inspect the generated tree and canvas:
   - Right-click recipes in the plan to add/edit recipes, or override the voltage just for that recipe.
   - Zoom, pan, and export the canvas (`Export PNG…` requires Pillow).
   - Adjust tier overrides per item (`Tier Overrides…`) and rebuild to see the impact.
   - Use **History…** to revisit the last 20 plan requests.

Autosaves land in `plans/_autosave_last_plan.json`, and a compact request history is kept in `plans/_history.json`.

## Project Layout
- `nomi_calc.py` – standalone CLI planner, no third-party GUI dependencies.
- `nomi_calc_gui.py` – thin launcher for the Tk GUI defined in `src/gui/app.py`.
- `src/core/` – shared domain logic (recipes, planning, overclocking, data models).
- `src/gui/` – widgets, dialogs, and canvas layout for the desktop app.
- `data/` – example recipes/items plus an import template.
- `plans/` – autosaved and sample plan outputs.

## Contributing
- This is just a fun (mostly vibe-coded) project to go along with my NomifactoryCEU playthrough. I suspect this will come in handy for many automation-based modpacks and games, so contributions are welcome. I plan to generalise it further, and provide preset settings for different gregtech packs, but I'm in no rush.
- File issues or pull requests with observed bugs, feature ideas, or fixes.
