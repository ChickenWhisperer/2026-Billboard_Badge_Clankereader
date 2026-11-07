#!/usr/bin/env python3
"""
Parametric back cover for the Hackaday Supercon 2026 Billboard Badge.

It snaps over the printed front bezel (hardware/billboard_mockup-Frame.step) and
encloses the back of the e-paper panel and the PCB. Every opening lines up with
the parts on the back of the PCB (hardware/billboard_pcb/billboard_pcb.kicad_pcb).

    python3 billboard_back_cover.py            -> writes STL, 3MF and STEP next to this script
    python3 billboard_back_cover.py --check    -> also runs the fit/interference check

Requires: pip install build123d   (OpenCascade CAD kernel, pure pip, no FreeCAD needed)

Coordinate system (same as the FreeCAD mockup in the Hackaday repo), in mm:
    X  left/right, viewed from the FRONT of the badge; 0 = bezel centre
    Y  up/down, +Y toward the lanyard holes; 0 = bezel centre
    Z  0 = back face of the bezel = front face of the PCB. +Z toward the viewer.
       The bezel occupies Z 0..5, the PCB Z -1.6..0, the parts on the back of the PCB go to about Z -6.6.

Print it back-face-down (as modelled), no supports. Footprint ~232 x 194 mm.
"""

from __future__ import annotations

import argparse
import pathlib

from build123d import (
    Align, Axis, Box, Cylinder, Face, FontStyle, Location, Plane, Pos,
    RectangleRounded, Sketch, Text, export_step, export_stl, extrude, loft,
    Mesher, Part,
)

# ----------------------------------------------------------------------------
# Tunable parameters
# ----------------------------------------------------------------------------
WALL = 2.0            # side wall thickness
FLOOR = 2.0           # back plate thickness
CAVITY = 8.5          # depth from bezel back (Z=0) to inside of back plate. 8.5 fits a LiPo up to ~7 mm.
CLEAR_BEZEL = 0.25    # gap between bezel edge and wall (per side). Increase if your bezel prints fat.
CLEAR_PCB = 0.40      # gap between PCB edge and wall (per side)
LIP = 1.0             # how far the snap lips reach over the front face of the bezel
LIP_GAP = 0.15        # vertical play between bezel front face and lip underside (0 = tight clamp)
BUTTON_GAP = 0.30     # air gap between button nub and the tact-switch plunger
LABELS = True         # engrave labels on the back
LABEL_DEPTH = 0.5

# ----------------------------------------------------------------------------
# Measured geometry (from the repo's STEP / FreeCAD / KiCad files)
# ----------------------------------------------------------------------------
BEZEL_X = 113.8            # half width   (227.6 mm)
BEZEL_Y = 83.56            # half height  (167.12 mm)
BEZEL_R = 3.0              # corner radius
BEZEL_T = 5.0              # thickness (Z 0..5)
POCKET = (-111.4, 105.6, -75.76, 81.94)   # display pocket on back of bezel (x0,x1,y0,y1), 3 mm deep
WINDOW = (-100.8, 100.8, -70.56, 70.56)   # viewing window (through)

PCB_X = 112.8              # half width (225.6 mm)
PCB_Y0, PCB_Y1 = 55.74, 105.74
PCB_R = 6.0
PCB_T = 1.6
PCB_BACK = -PCB_T          # Z of PCB back face
LANYARD = [(-106.8, 99.74), (106.8, 99.74)]   # 8 mm holes in the PCB

# Parts on the back of the PCB (centre x, y)
TACT = {  # 6x6 tact switches, 5.0 mm tall, plunger faces backwards
    # name: (x, y, label, direction from hinge to tip)
    "SW5": (-106.8, 87.0, "MENU-", (-1, 0)),
    "SW4": (-106.8, 70.0, "A", (0, 1)),
    "SW6": (106.8, 87.0, "MENU+", (1, 0)),
    "SW7": (106.8, 70.0, "B", (0, 1)),
}
TACT_H = 5.0
BOOT = (-39.25, 84.0)       # SW2 -> ESP32 IO0
RESET = (-13.0, 96.0)       # SW1 -> ESP32 EN
SMALL_SW_H = 1.95
SLIDE = (-94.0, -86.3, 67.4, 76.57)      # SW3 power slide switch, knob on top (moves along Y)
USB = (-8.92, 0.92, 55.0, 62.93)          # USB-C, opens downward (-Y) at the PCB bottom edge
BATT = (-78.9, -70.6, 58.25, 67.45)       # J1 PH2.0 vertical battery connector
LED_CHG = (-71.2, 69.29)                  # D4 (next to charger)
LED_STAT = (-38.8, 96.6)                  # D6 status LED (GPIO1)

OUT = pathlib.Path(__file__).resolve().parent   # outputs live next to this script

Z_IN = -CAVITY                 # inside face of back plate
Z_OUT = -CAVITY - FLOOR        # outside face of back plate (print bed)


# ----------------------------------------------------------------------------
def rrect(x0, x1, y0, y1, r) -> Sketch:
    r = max(min(r, (x1 - x0) / 2 - 0.01, (y1 - y0) / 2 - 0.01), 0.2)
    return Pos((x0 + x1) / 2, (y0 + y1) / 2) * RectangleRounded(x1 - x0, y1 - y0, r)


def bezel_outline(off: float) -> Sketch:
    return rrect(-BEZEL_X - off, BEZEL_X + off, -BEZEL_Y - off, BEZEL_Y + off, BEZEL_R + off)


def pcb_outline(off: float) -> Sketch:
    return rrect(-PCB_X - off, PCB_X + off, PCB_Y0 - off, PCB_Y1 + off, PCB_R + off)


def slab(sk: Sketch, z0: float, z1: float) -> Part:
    return Pos(0, 0, z0) * extrude(sk, z1 - z0)


def box(x0, x1, y0, y1, z0, z1) -> Part:
    return Pos(x0, y0, z0) * Box(x1 - x0, y1 - y0, z1 - z0, align=(Align.MIN, Align.MIN, Align.MIN))


def cyl(x, y, r, z0, z1) -> Part:
    return Pos(x, y, z0) * Cylinder(r, z1 - z0, align=(Align.CENTER, Align.CENTER, Align.MIN))


def build() -> Part:
    inner = bezel_outline(CLEAR_BEZEL) + pcb_outline(CLEAR_PCB)
    outer = bezel_outline(CLEAR_BEZEL + WALL) + pcb_outline(CLEAR_PCB + WALL)

    # Tray: back plate + walls up to Z=0
    cover = slab(outer, Z_OUT, 0) - slab(inner, Z_IN, 0.01)

    # Bezel wrap: walls on left, right and bottom from Z=0 to just above the bezel front
    wall_top = BEZEL_T + LIP_GAP
    ring = bezel_outline(CLEAR_BEZEL + WALL) - bezel_outline(CLEAR_BEZEL)
    clip = box(-200, 200, -200, BEZEL_Y + CLEAR_BEZEL, -1, 50)  # not across the top edge (the PCB continues there)
    cover += slab(ring, 0, wall_top) & clip

    # Snap lips: short segments so the long walls can flex. Flat underside, 45-degree lead-in on top.
    lip_h = 1.4
    lip_block = slab(bezel_outline(CLEAR_BEZEL + WALL), wall_top, wall_top + lip_h)
    flat = slab(bezel_outline(CLEAR_BEZEL - LIP), wall_top - 0.01, wall_top + 0.4)
    ramp = loft([
        Pos(0, 0, wall_top + 0.4) * bezel_outline(CLEAR_BEZEL - LIP),
        Pos(0, 0, wall_top + lip_h + 0.01) * bezel_outline(CLEAR_BEZEL - LIP + lip_h + 0.2),
    ])
    lip = lip_block - flat - ramp
    seg = (
        box(-95, -45, -200, -60, -1, 50) + box(-25, 25, -200, -60, -1, 50) + box(45, 95, -200, -60, -1, 50)
        + box(-200, -100, -65, -20, -1, 50) + box(-200, -100, 15, 55, -1, 50)
        + box(100, 200, -65, -20, -1, 50) + box(100, 200, 15, 55, -1, 50)
    )
    cover += lip & seg

    # Seat ribs: push the bezel's back border against the lips (outside the display pocket, below the PCB)
    rib_top = 0.0
    cover += box(-112.0, 112.0, -81.0, -79.0, Z_IN - 0.01, rib_top)            # bottom border (7.8 mm wide)
    cover += box(108.7, 110.7, -79.0, 54.5, Z_IN - 0.01, rib_top)              # right border (8.2 mm wide)
    cover += box(-BEZEL_X - CLEAR_BEZEL - 0.01, -112.0, -81.0, 54.5, Z_IN - 0.01, rib_top)  # left border (2.4 mm wide)

    # Lanyard bosses: support the PCB, locate it through its 8 mm holes, keep the lanyard holes open
    for x, y in LANYARD:
        cover += cyl(x, y, 5.5, Z_IN - 0.01, PCB_BACK)
        cover += cyl(x, y, 3.75, PCB_BACK - 0.01, 0)
        cover -= cyl(x, y, 2.4, Z_OUT - 1, 1)

    # PCB support posts (clear of every part on the back of the PCB, see POSTS below)
    for x, y in POSTS:
        cover += cyl(x, y, 2.0, Z_IN - 0.01, PCB_BACK)

    # Cantilever buttons for the four tact switches. Each tongue is a 10 x 16 mm flap cut free on
    # three sides, hinged at its root, thinned to 1.2 mm, with a nub that presses the plunger.
    for name, (sx, sy, label, (dx, dy)) in TACT.items():
        L_tip, L_root, w, gap, hinge = 3.0, 13.0, 10.0, 0.8, 2.0
        tip_x, tip_y = sx + dx * L_tip, sy + dy * L_tip
        root_x, root_y = sx - dx * L_root, sy - dy * L_root
        if dx:   # tongue runs along X
            x0, x1 = sorted((tip_x, root_x)); y0, y1 = sy - w / 2, sy + w / 2
            sx0, sx1 = (x0 - gap, x1) if dx < 0 else (x0, x1 + gap)
            sy0, sy1 = y0 - gap, y1 + gap
            tx0, tx1 = (x0, x1 - hinge) if dx < 0 else (x0 + hinge, x1)
            ty0, ty1 = y0, y1
        else:    # tongue runs along Y
            y0, y1 = sorted((tip_y, root_y)); x0, x1 = sx - w / 2, sx + w / 2
            sy0, sy1 = (y0 - gap, y1) if dy < 0 else (y0, y1 + gap)
            sx0, sx1 = x0 - gap, x1 + gap
            ty0, ty1 = (y0, y1 - hinge) if dy < 0 else (y0 + hinge, y1)
            tx0, tx1 = x0, x1
        tongue = box(x0, x1, y0, y1, Z_OUT - 1, Z_IN + 1)
        cover -= box(sx0, sx1, sy0, sy1, Z_OUT - 1, Z_IN + 0.01) - tongue
        cover -= box(tx0, tx1, ty0, ty1, Z_IN - 0.8, Z_IN + 0.01)        # thin the flap from the inside
        plunger_z = PCB_BACK - TACT_H
        cover += cyl(sx, sy, 2.0, Z_IN - 0.81, plunger_z - BUTTON_GAP)  # nub
        cover -= cyl(sx, sy, 3.5, Z_OUT - 1, Z_OUT + 0.3)                # shallow finger dimple outside

    # BOOT / RESET: paper-clip holes with guide tubes
    for x, y in (BOOT, RESET):
        cover += cyl(x, y, 2.6, Z_IN - 0.01, PCB_BACK - SMALL_SW_H - 0.6)
        cover -= cyl(x, y, 1.6, Z_OUT - 1, 1)

    # Openings
    cover -= slab(rrect(-13.0, 5.0, 12.0, 57.0, 3.0), Z_OUT - 1, Z_IN + 0.5)          # USB-C plug/cable
    x0, x1, y0, y1 = SLIDE
    cover -= slab(rrect(x0 - 0.6, x1 + 0.6, y0 - 1.0, y1 + 1.0, 1.5), Z_OUT - 1, Z_IN + 0.5)  # power slide switch
    x0, x1, y0, y1 = BATT
    cover -= slab(rrect(x0 - 0.7, x1 + 0.7, y0 - 0.7, y1 + 0.7, 1.5), Z_OUT - 1, Z_IN + 0.5)  # battery plug
    for x, y in (LED_CHG, LED_STAT):                                                       # light holes
        cover -= cyl(x, y, 1.2, Z_OUT - 1, Z_IN + 0.5)

    if LABELS:
        cover -= labels()
    return cover


# Posts: chosen by find_posts() below; hard-coded so the build is deterministic.
POSTS = [
    (-100.0, 78.0), (-62.0, 84.0), (-25.0, 75.0), (-8.0, 80.0), (20.0, 80.0), (65.0, 80.0), (95.0, 78.0),
    (-92.0, 100.0), (-55.0, 100.0), (15.0, 100.0), (45.0, 100.0), (90.0, 100.0),
]


def labels() -> Part:
    """Text engraved into the outside of the back plate, readable when looking at the back."""
    # Looking at the back, +X of the model points to the viewer's LEFT, so mirror the text plane.
    items = [
        (-84.5, 87.0, "MENU-"), (-97.5, 62.0, "A"), (84.5, 87.0, "MENU+"), (97.5, 62.0, "B"),
        (BOOT[0], BOOT[1] - 5.0, "BOOT"), (RESET[0], RESET[1] + 4.8, "RST"),
        (-4.0, 7.5, "USB-C"), (-90.15, 63.0, "PWR"), (-74.75, 54.0, "BATT"),
        (LED_CHG[0] + 5.5, LED_CHG[1] - 3.0, "CHG"), (LED_STAT[0] + 0.0, LED_STAT[1] + 3.6, "LED"),
    ]
    out = None
    for x, y, txt in items:
        pl = Plane(origin=(x, y, Z_OUT), x_dir=(-1, 0, 0), z_dir=(0, 0, -1))
        t = Text(txt, font_size=3.2, font_style=FontStyle.BOLD, align=(Align.CENTER, Align.CENTER))
        solid = extrude(pl * t, amount=-LABEL_DEPTH)   # into the part
        out = solid if out is None else out + solid
    return out


# ----------------------------------------------------------------------------
# Reference model of the badge (for the fit check and previews)
# ----------------------------------------------------------------------------
def badge_reference(repo: pathlib.Path):
    """Return dict of name -> Part for the assembled badge, in cover coordinates."""
    from build123d import import_step
    parts = {}
    parts["bezel"] = import_step(repo / "hardware/billboard_mockup-Frame.step")
    pcb = import_step(repo / "hardware/billboard_pcb/billboard_pcb.step")
    parts["pcb_assembly"] = Pos(0, 0, -1.51) * pcb          # board model is 1.51 thick, front at +Z
    parts["display"] = box(-110.2, 104.4, -74.56, 80.74, 0, 3.0)
    # Parts that have no 3D model in the PCB STEP (heights from their vendor models)
    for name, (sx, sy, *_) in TACT.items():
        parts[name] = box(sx - 3.3, sx + 3.3, sy - 3.0, sy + 3.0, PCB_BACK - TACT_H, PCB_BACK)
    parts["SW3_slide"] = box(SLIDE[0], SLIDE[1], SLIDE[2], SLIDE[3], PCB_BACK - 4.6, PCB_BACK)
    parts["USB1"] = box(USB[0], USB[1], USB[2], USB[3], PCB_BACK - 3.23, PCB_BACK + 0.93)
    parts["J1_batt"] = box(BATT[0], BATT[1], BATT[2], BATT[3], PCB_BACK - 5.6, PCB_BACK)
    parts["SW1_reset"] = box(-14.54, -11.46, 93.2, 98.8, PCB_BACK - SMALL_SW_H, PCB_BACK)
    parts["SW2_boot"] = box(-40.79, -37.71, 81.2, 86.8, PCB_BACK - SMALL_SW_H, PCB_BACK)
    for x0, x1 in ((-57.8, -43.7), (42.95, 57.05)):   # display flex cables + FH34 connectors, loops to Z -6.05
        parts[f"fpc_{x0:+.0f}"] = box(x0, x1, 73.94, 85.39, -6.05, 0)
    for x in (-74.54, 0.46, 75.46):                     # SAO header solder tails
        parts[f"sao_tails_{x:+.0f}"] = box(x - 3.4, x + 3.4, 95.14, 99.4, -3.2, -1.6)
    parts["usb_plug"] = box(-4.0 - 6.2, -4.0 + 6.2, 30.0, 55.0, -6.0, 0.0)   # typical USB-C overmold
    return parts


def fit_check(cover: Part, repo: pathlib.Path) -> bool:
    ok = True
    ref = badge_reference(repo)
    print("\nFit check (overlap volume between cover and badge parts, mm^3):")
    for name, p in ref.items():
        try:
            v = (cover & p).volume
        except Exception:
            v = 0.0
        flag = "OK " if v < 0.5 else "HIT"
        ok &= v < 0.5
        print(f"  {flag} {name:18s} {v:9.3f}")
    # lips must overhang the bezel front face
    bez = ref["bezel"]
    probe = (cover & box(-300, 300, -300, 300, BEZEL_T, BEZEL_T + 3)).volume
    print(f"  lip volume above bezel front: {probe:.0f} mm^3")
    return ok


def find_posts():
    """Helper used once to choose POSTS: candidate grid minus keepouts (needs keepouts.json)."""
    import json
    keep = json.load(open(pathlib.Path(__file__).with_name("keepouts.json")))
    keep = [k for k in keep if k[0] != "U1"] + [["U1", -35.81, -17.8, 79.5, 105.0]]
    keep += [["FPC", -58.8, -42.7, 72.9, 86.4], ["FPC", 41.95, 58.05, 72.9, 86.4]]
    for k, (sx, sy, _, (dx, dy)) in TACT.items():
        xs = sorted((sx + dx * 3.8, sx - dx * 13)) if dx else (sx - 5.8, sx + 5.8)
        ys = sorted((sy + dy * 3.8, sy - dy * 13)) if dy else (sy - 5.8, sy + 5.8)
        keep.append([k + "_btn", xs[0], xs[1], ys[0], ys[1]])
    for x, y in LANYARD:
        keep.append(["lan", x - 6, x + 6, y - 6, y + 6])
    r = 2.0 + 0.8

    def free(x, y):
        if abs(x) > PCB_X - 3 or y < PCB_Y0 + 3 or y > PCB_Y1 - 3:
            return False
        if y < 71 and abs(x) < 101:   # PCB there is backed only by display glass: don't press
            return False
        return all(not (k[1] - r < x < k[2] + r and k[3] - r < y < k[4] + r) for k in keep)
    return free


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="run interference check against the repo's badge models")
    ap.add_argument("--repo", type=pathlib.Path,
                    default=pathlib.Path(__file__).resolve().parents[2],
                    help="repo root (default: two levels up, since this lives in hardware/back_cover/)")
    args = ap.parse_args()

    free = None
    try:
        free = find_posts()
    except FileNotFoundError:
        pass
    if free:
        bad = [p for p in POSTS if not free(*p)]
        if bad:
            raise SystemExit(f"posts collide with PCB parts: {bad}")

    cover = build()
    OUT.mkdir(exist_ok=True)
    export_step(cover, OUT / "billboard_back_cover.step")
    export_stl(cover, OUT / "billboard_back_cover.stl", tolerance=0.02, angular_tolerance=0.2)
    m = Mesher()
    m.add_shape(cover, linear_deflection=0.02, angular_deflection=0.2)
    m.write(str(OUT / "billboard_back_cover.3mf"))
    bb = cover.bounding_box()
    print(f"Cover: {bb.size.X:.1f} x {bb.size.Y:.1f} x {bb.size.Z:.1f} mm, "
          f"volume {cover.volume/1000:.1f} cm^3 (~{cover.volume/1000*1.24:.0f} g PLA solid), "
          f"valid={cover.is_valid}, solids={len(cover.solids())}")
    if args.check:
        ok = fit_check(cover, args.repo)
        print("FIT OK" if ok else "FIT PROBLEMS")
    return cover


if __name__ == "__main__":
    main()
