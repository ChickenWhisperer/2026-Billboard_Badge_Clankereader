#!/usr/bin/env python3
"""Render PNG previews of the back cover (outside, inside, and fit cross-sections)."""
import pathlib
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import billboard_back_cover as bc  # noqa: E402
from build123d import Plane, Pos  # noqa: E402

OUT = bc.OUT


def mesh(shape, tol=0.15):
    v, f = shape.tessellate(tol, 0.5)
    v = np.array([[p.X, p.Y, p.Z] for p in v])
    return v[np.array(f)]


def shade(tris, light=(0.3, -0.4, -1.0), base=(0.85, 0.35, 0.2)):
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    L = np.array(light) / np.linalg.norm(light)
    k = 0.35 + 0.65 * np.abs(n @ L)
    return np.clip(np.outer(k, base), 0, 1)


def view(tris, elev, azim, path, title, base, light):
    fig = plt.figure(figsize=(11, 9.5), dpi=110)
    ax = fig.add_subplot(111, projection="3d")
    pc = Poly3DCollection(tris, facecolors=shade(tris, light, base), edgecolors="none")
    ax.add_collection3d(pc)
    ax.set_xlim(-118, 118); ax.set_ylim(-118 + 11, 118 + 11); ax.set_zlim(-60, 60)
    ax.set_box_aspect((1, 1, 0.5))
    ax.view_init(elev=elev, azim=azim)
    ax.set_axis_off()
    ax.set_title(title, fontsize=13)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def section(cover, ref, plane_axis, value, path, title, xlim, ylim):
    """2-D cross-section: cover in orange, badge parts in grey/blue."""
    from build123d import Box, Align
    fig, ax = plt.subplots(figsize=(12, 5.5), dpi=110)
    thin = 0.02

    def cut(shape):
        if plane_axis == "x":
            slab = bc.box(value - thin, value + thin, -300, 300, -300, 300)
        else:
            slab = bc.box(-300, 300, value - thin, value + thin, -300, 300)
        try:
            return shape & slab
        except Exception:
            return None

    def draw(shape, color, alpha=1.0, label=None):
        s = cut(shape)
        if s is None or s.volume < 1e-6:
            return
        for face in s.faces():
            n = face.normal_at()
            if abs(n.X if plane_axis == "x" else n.Y) < 0.99:
                continue
            pts = [v for v in face.outer_wire().vertices()]
            edges = face.outer_wire().edges()
            poly = []
            for e in edges:
                for t in np.linspace(0, 1, 8):
                    p = e.position_at(t)
                    poly.append(((p.Y if plane_axis == "x" else p.X), p.Z))
            ax.fill(*zip(*poly), color=color, alpha=alpha, lw=0, label=label)
            label = None

    colors = {"bezel": "#555", "display": "#9ab", "pcb_assembly": "#2a6"}
    for name, part in ref.items():
        c = colors.get(name, "#36c" if "plug" not in name else "#c9c")
        draw(part, c, 0.9, name if name in colors or name.startswith("SW") or name.startswith("usb") else None)
    draw(cover, "#e8743b", 0.95, "back cover")
    ax.set_aspect("equal"); ax.set_xlim(*xlim); ax.set_ylim(*ylim)
    ax.set_xlabel("Y (mm)" if plane_axis == "x" else "X (mm)"); ax.set_ylabel("Z (mm)  front is up")
    ax.grid(alpha=0.3); ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.25), fontsize=8, ncol=6)
    ax.set_title(title)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


if __name__ == "__main__":
    cover = bc.build()
    tris = mesh(cover)
    view(tris, -90, 90, OUT / "preview_back.png", "Back cover - outside (as seen from behind the badge)",
         (0.85, 0.35, 0.2), (0.2, 0.3, -1))
    view(tris, 55, -100, OUT / "preview_inside.png", "Back cover - inside (badge side), as printed",
         (0.85, 0.35, 0.2), (0.3, -0.5, 1))
    ref = bc.badge_reference(bc.pathlib.Path(__file__).resolve().parents[2])
    section(cover, ref, "x", -106.8, OUT / "section_x-106.8_buttons.png",
            "Section at X = -106.8 (MENU- and A buttons, left lanyard hole)", (-90, 112), (-12, 8))
    section(cover, ref, "y", 70.0, OUT / "section_y70.png",
            "Section at Y = 70 (A/B buttons, power switch, through the PCB/display overlap)", (-118, 118), (-12, 8))
    print("previews written to", OUT)
