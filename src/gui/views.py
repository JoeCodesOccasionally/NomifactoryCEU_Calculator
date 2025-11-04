from __future__ import annotations

import math
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

from src.core.models import PlanNode


class PlanTree(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        cols = ("rate", "machine", "tier", "machines", "op_time", "eut", "oc")
        self.tree = ttk.Treeview(self, columns=cols, show="tree headings", height=25)
        self.tree.heading("#0", text="Item")
        self.tree.column("#0", stretch=True, width=260)
        heads = {
            "rate": "Rate (/s)",
            "machine": "Machine",
            "tier": "Tier",
            "machines": "Machines",
            "op_time": "Op time (s)",
            "eut": "EU/t",
            "oc": "OC",
        }
        for k, v in heads.items():
            self.tree.heading(k, text=v)
            self.tree.column(k, stretch=True, width=140)
        ysb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.node_map: Dict[str, PlanNode] = {}

    def fill(self, root: PlanNode) -> None:
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.node_map.clear()

        def fill_node(n: PlanNode, parent=""):
            item_label = n.item_display or n.item
            vals = (
                f"{n.item_rate_per_s:.6g}",
                n.machine,
                n.machine_tier,
                n.machines_needed,
                f"{n.effective_time_s:.6g}",
                f"{n.effective_eut:.6g}",
                n.overclocks,
            )
            iid = self.tree.insert(parent, "end", text=item_label, values=vals)
            self.node_map[iid] = n
            self.tree.item(iid, open=True)
            for c in n.children:
                fill_node(c, iid)

        if root:
            fill_node(root)

    def get_node(self, iid: str) -> PlanNode | None:
        return self.node_map.get(iid)


class ChainLayout:
    def __init__(self, x_spacing=360, y_spacing=140, node_w=240, node_h=84, margin=80):
        self.xs = x_spacing
        self.ys = y_spacing
        self.w = node_w
        self.h = node_h
        self.m = margin
        self.pos = {}

    def _leaves(self, n: PlanNode) -> int:
        return 1 if not n.children else sum(self._leaves(c) for c in n.children)

    def _assign(self, n: PlanNode, left: int, depth: int) -> int:
        if not n.children:
            self.pos[id(n)] = (left, depth)
            return left
        cur = left
        centers = []
        for c in n.children:
            w = self._leaves(c)
            centers.append(self._assign(c, cur, depth + 1))
            cur += w
        center = (centers[0] + centers[-1]) // 2
        self.pos[id(n)] = (center, depth)
        return center

    def layout(self, root: PlanNode):
        self.pos.clear()
        self._assign(root, 0, 0)
        coords = {}
        for k, (sx, sy) in self.pos.items():
            x = self.m + sx * self.xs
            y = self.m + sy * self.ys
            coords[k] = (x, y)
        return coords


class ChainCanvas(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.scale = 1.0
        self.min_scale = 0.4
        self.max_scale = 3.0
        self.node_width = 240
        self.node_height = 84
        self.margin = 80
        self.x_spacing = self.node_width + 20
        self.y_spacing = self.node_height + 40

        self._root_node: Optional[PlanNode] = None
        self._base_coords: Dict[int, Tuple[float, float]] = {}
        self._id2node: Dict[int, PlanNode] = {}
        self._edges: List[Tuple[int, int]] = []
        self._node_labels: Dict[int, str] = {}
        self._base_width = 0.0
        self._base_height = 0.0
        self._current_bbox: Optional[Tuple[int, int, int, int]] = None

        self.zoom_var = tk.StringVar(value="100%")

        toolbar = ttk.Frame(self, padding=(0, 0, 0, 4))
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Button(toolbar, text="Zoom In", command=self.zoom_in).pack(side="left")
        ttk.Button(toolbar, text="Zoom Out", command=self.zoom_out).pack(
            side="left", padx=(4, 0)
        )
        ttk.Button(toolbar, text="Reset View", command=self.reset_view).pack(
            side="left", padx=(4, 0)
        )
        ttk.Label(toolbar, textvariable=self.zoom_var).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Export PNG…", command=self.export_png).pack(
            side="right"
        )

        self.canvas = tk.Canvas(self, background="#ffffff", highlightthickness=0)
        self.hbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(
            xscrollcommand=self.hbar.set, yscrollcommand=self.vbar.set
        )
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.vbar.grid(row=1, column=1, sticky="ns")
        self.hbar.grid(row=2, column=0, sticky="ew")
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_mousewheel)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Shift-MouseWheel>", self._on_shift_mousewheel)
        self.canvas.bind("<Button-4>", self._on_wheel_linux)
        self.canvas.bind("<Button-5>", self._on_wheel_linux)
        self.canvas.bind("<Shift-Button-4>", self._on_wheel_linux_shift)
        self.canvas.bind("<Shift-Button-5>", self._on_wheel_linux_shift)
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan_move)
        self.canvas.bind("<ButtonPress-3>", self._on_pan_start)
        self.canvas.bind("<B3-Motion>", self._on_pan_move)

    def draw_plan(self, root: PlanNode):
        self._root_node = root
        self.canvas.delete("all")
        self._base_coords.clear()
        self._id2node.clear()
        self._edges.clear()
        self._node_labels.clear()
        self._current_bbox = None
        if not root:
            self.canvas.configure(scrollregion=(0, 0, 0, 0))
            self.scale = 1.0
            self._update_zoom_label()
            return

        layout = ChainLayout(
            x_spacing=self.x_spacing,
            y_spacing=self.y_spacing,
            node_w=self.node_width,
            node_h=self.node_height,
            margin=self.margin,
        )
        self._collect_graph(root)
        self._base_coords = layout.layout(root)
        self._compute_base_extents()
        self.scale = 1.0
        self._redraw()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self._update_zoom_label()

    def zoom_in(self):
        self._set_zoom(self.scale * 1.2, focus=("center",))

    def zoom_out(self):
        self._set_zoom(self.scale / 1.2, focus=("center",))

    def reset_view(self):
        if not self._base_coords:
            return
        self.scale = 1.0
        self._redraw()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self._update_zoom_label()

    def export_png(self):
        if not self._base_coords:
            messagebox.showinfo("Export Plan", "There is no plan to export.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG Image", "*.png")],
            title="Export Plan",
        )
        if not path:
            return
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            messagebox.showerror(
                "Export Failed",
                "PNG export requires the Pillow library.\nInstall it with: pip install pillow",
            )
            return

        export_scale = min(max(self.scale, 1.5), 4.0)
        width = max(1, int(math.ceil(self._base_width * export_scale)))
        height = max(1, int(math.ceil(self._base_height * export_scale)))
        img = Image.new("RGB", (width, height), "#ffffff")
        draw = ImageDraw.Draw(img)

        outline_color = "#333333"
        text_color = "#000000"
        node_w = self.node_width * export_scale
        node_h = self.node_height * export_scale
        line_width = max(1, int(round(2 * export_scale)))

        base_font = tkfont.nametofont("TkDefaultFont")
        base_size = abs(int(base_font.actual("size") or 9))
        font_size = max(8, int(round(base_size * export_scale)))

        font: ImageFont.ImageFont
        font_family = base_font.actual("family") or "Arial"
        try:
            font = ImageFont.truetype(font_family, font_size)
        except Exception:
            try:
                font = ImageFont.truetype("arial", font_size)
            except Exception:
                font = ImageFont.load_default()

        for parent_id, child_id in self._edges:
            px, py = self._base_coords[parent_id]
            cx, cy = self._base_coords[child_id]
            sx = (cx + self.node_width / 2) * export_scale
            sy = (cy + self.node_height) * export_scale
            tx = (px + self.node_width / 2) * export_scale
            ty = py * export_scale
            draw.line((sx, sy, tx, ty), fill=outline_color, width=line_width)
            self._draw_arrow_head(draw, (sx, sy), (tx, ty), export_scale, outline_color)

        for nid, (bx, by) in self._base_coords.items():
            sx = bx * export_scale
            sy = by * export_scale
            node = self._id2node[nid]
            fill = self._node_fill(node)
            rect = (sx, sy, sx + node_w, sy + node_h)
            draw.rectangle(rect, fill=fill, outline=outline_color, width=line_width)
            text = self._node_labels[nid]
            spacing = max(2, int(font_size * 0.2))
            try:
                bbox = draw.multiline_textbbox(
                    (0, 0), text, font=font, align="center", spacing=spacing
                )
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
                text_x = sx + node_w / 2 - text_w / 2
                text_y = sy + node_h / 2 - text_h / 2
                draw.multiline_text(
                    (text_x, text_y),
                    text,
                    font=font,
                    fill=text_color,
                    align="center",
                    spacing=spacing,
                )
            except AttributeError:
                lines = text.splitlines()
                line_spacing = spacing
                text_h = len(lines) * font_size + (len(lines) - 1) * line_spacing
                text_y = sy + node_h / 2 - text_h / 2
                for line in lines:
                    text_w = draw.textlength(line, font=font)
                    text_x = sx + node_w / 2 - text_w / 2
                    draw.text((text_x, text_y), line, font=font, fill=text_color)
                    text_y += font_size + line_spacing

        try:
            img.save(path)
        except Exception as exc:
            messagebox.showerror("Export Failed", f"Could not save PNG: {exc}")
            return

        messagebox.showinfo("Export Plan", f"Plan exported to:\n{path}")

    def _collect_graph(self, root: PlanNode):
        def walk(n: PlanNode):
            nid = id(n)
            self._id2node[nid] = n
            self._node_labels[nid] = self._format_node_label(n)
            for c in n.children:
                cid = id(c)
                self._edges.append((nid, cid))
                walk(c)

        walk(root)

    def _compute_base_extents(self):
        if not self._base_coords:
            self._base_width = 0.0
            self._base_height = 0.0
            return
        xs = [x for x, _ in self._base_coords.values()]
        ys = [y for _, y in self._base_coords.values()]
        max_x = max(xs, default=0.0)
        max_y = max(ys, default=0.0)
        self._base_width = max_x + self.node_width + self.margin
        self._base_height = max_y + self.node_height + self.margin

    def _redraw(self):
        self.canvas.delete("all")
        if not self._base_coords:
            self.canvas.configure(scrollregion=(0, 0, 0, 0))
            self._current_bbox = None
            return

        scale = self.scale
        node_w = self.node_width * scale
        node_h = self.node_height * scale
        outline = "#333333"
        line_width = max(1, int(round(2 * scale)))
        text_size = max(6, int(round(9 * scale)))

        scaled_coords: Dict[int, Tuple[float, float]] = {
            nid: (x * scale, y * scale) for nid, (x, y) in self._base_coords.items()
        }

        for parent_id, child_id in self._edges:
            px, py = scaled_coords[parent_id]
            cx, cy = scaled_coords[child_id]
            sx = cx + node_w / 2
            sy = cy + node_h
            tx = px + node_w / 2
            ty = py
            self.canvas.create_line(
                sx,
                sy,
                tx,
                ty,
                arrow="last",
                width=line_width,
                fill=outline,
            )

        for nid, (sx, sy) in scaled_coords.items():
            node = self._id2node[nid]
            fill = self._node_fill(node)
            self.canvas.create_rectangle(
                sx,
                sy,
                sx + node_w,
                sy + node_h,
                outline=outline,
                width=line_width,
                fill=fill,
            )
            self.canvas.create_text(
                sx + node_w / 2,
                sy + node_h / 2,
                text=self._node_labels[nid],
                justify="center",
                font=("TkDefaultFont", text_size),
            )

        bbox = self.canvas.bbox("all")
        if bbox:
            self.canvas.configure(scrollregion=bbox)
            self._current_bbox = bbox
        else:
            self.canvas.configure(scrollregion=(0, 0, 0, 0))
            self._current_bbox = None

    def _set_zoom(
        self, new_scale: float, focus: Optional[Tuple[float, float] | Tuple[str]] = None
    ):
        if not self._base_coords:
            return
        new_scale = max(self.min_scale, min(self.max_scale, new_scale))
        if math.isclose(new_scale, self.scale, rel_tol=1e-3):
            return

        canvas = self.canvas
        if focus and focus[0] == "center":
            fx = canvas.winfo_width() / 2
            fy = canvas.winfo_height() / 2
        elif focus and isinstance(focus, tuple) and len(focus) == 2:
            fx, fy = focus
        else:
            fx = fy = None

        left_base = canvas.canvasx(0) / self.scale
        top_base = canvas.canvasy(0) / self.scale
        if fx is not None and fy is not None:
            focus_base_x = canvas.canvasx(fx) / self.scale
            focus_base_y = canvas.canvasy(fy) / self.scale
        else:
            focus_base_x = focus_base_y = None

        self.scale = new_scale
        self._redraw()
        self._update_zoom_label()

        bbox = self._current_bbox
        if not bbox:
            return
        total_width = bbox[2] - bbox[0]
        total_height = bbox[3] - bbox[1]
        viewport_w = max(canvas.winfo_width(), 1)
        viewport_h = max(canvas.winfo_height(), 1)

        if (
            focus_base_x is not None
            and focus_base_y is not None
            and total_width > 0
            and total_height > 0
        ):
            new_focus_x = focus_base_x * new_scale
            new_focus_y = focus_base_y * new_scale
            target_left = new_focus_x - viewport_w / 2
            target_top = new_focus_y - viewport_h / 2
        else:
            target_left = left_base * new_scale
            target_top = top_base * new_scale

        if total_width <= viewport_w:
            canvas.xview_moveto(0)
        else:
            fraction_x = (target_left - bbox[0]) / (total_width - viewport_w)
            canvas.xview_moveto(min(max(fraction_x, 0.0), 1.0))

        if total_height <= viewport_h:
            canvas.yview_moveto(0)
        else:
            fraction_y = (target_top - bbox[1]) / (total_height - viewport_h)
            canvas.yview_moveto(min(max(fraction_y, 0.0), 1.0))

    def _update_zoom_label(self):
        self.zoom_var.set(f"{int(round(self.scale * 100))}%")

    def _node_fill(self, node: PlanNode) -> str:
        return "#eef7ff" if node.machine == "RAW" else "#f5f5f5"

    def _format_node_label(self, node: PlanNode) -> str:
        label = node.item_display or node.item
        if node.machine == "RAW":
            return f"RAW: {label}\n@ {node.item_rate_per_s:.4g}/s"
        return (
            f"{node.machine} [{node.machine_tier}] x{node.machines_needed}\n"
            f"{label} @ {node.item_rate_per_s:.4g}/s\n"
            f"op={node.effective_time_s:.3g}s  EU/t={node.effective_eut:.4g}"
        )

    def _on_ctrl_mousewheel(self, event: tk.Event):
        delta = event.delta or 0
        factor = 1.1 if delta > 0 else 1 / 1.1
        self._set_zoom(self.scale * factor, focus=(event.x, event.y))
        return "break"

    def _on_mousewheel(self, event: tk.Event):
        if event.delta:
            steps = int(abs(event.delta) / 120) or 1
            direction = -1 if event.delta > 0 else 1
            self.canvas.yview_scroll(direction * steps, "units")
        return "break"

    def _on_shift_mousewheel(self, event: tk.Event):
        if event.delta:
            steps = int(abs(event.delta) / 120) or 1
            direction = -1 if event.delta > 0 else 1
            self.canvas.xview_scroll(direction * steps, "units")
        return "break"

    def _on_wheel_linux(self, event: tk.Event):
        direction = -1 if event.num == 4 else 1
        self.canvas.yview_scroll(direction, "units")
        return "break"

    def _on_wheel_linux_shift(self, event: tk.Event):
        direction = -1 if event.num == 4 else 1
        self.canvas.xview_scroll(direction, "units")
        return "break"

    def _on_pan_start(self, event: tk.Event):
        self.canvas.scan_mark(event.x, event.y)

    def _on_pan_move(self, event: tk.Event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _draw_arrow_head(
        self,
        draw,
        start: Tuple[float, float],
        end: Tuple[float, float],
        scale: float,
        fill: str,
    ):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        if length == 0:
            return
        ux = dx / length
        uy = dy / length
        arrow_len = max(12.0 * scale, 8.0)
        arrow_half = max(6.0 * scale, 4.0)
        tip = end
        left = (
            end[0] - arrow_len * ux + arrow_half * uy,
            end[1] - arrow_len * uy - arrow_half * ux,
        )
        right = (
            end[0] - arrow_len * ux - arrow_half * uy,
            end[1] - arrow_len * uy + arrow_half * ux,
        )
        draw.polygon([tip, left, right], fill=fill)
