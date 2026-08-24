#!/usr/bin/env python3
"""Node-RED canvas geometry + layout linting.

Detects overlapping groups, group boxes that don't cover their own nodes,
nodes sitting inside a group they don't belong to, and node-on-node collisions.

Geometry is ported from the Node-RED editor itself
(`@node-red/editor-client/public/red/red.js`), so group maths is exact and
node maths is exact apart from label text measurement (see TEXT WIDTH below):

    x, y                 node CENTRE (not top-left)
    hideLabel            'l' in node ? not node['l'] : type in (link in, link out)
    w  hideLabel         30
       else              max(100, 20*ceil((textw + 50 + (inputs>0 ? 7 : 0)) / 20))
    h  hideLabel         max(30, outputs*15)
       else              max(6 + 24*label_lines, outputs*15, 30)
    junction             no width or height at all

    group box from members (editor `addToGroup`, 25px padding):
       x  = min(n.x - n.w/2 - 25 - (button and align != right ? 20 : 0))
       y  = min(n.y - n.h/2 - 25)
       x2 = max(n.x + n.w/2 + 25 + (button and align == right ? 20 : 0))
       y2 = max(n.y + n.h/2 + 25)

TEXT WIDTH is measured by the browser at 14px in the editor's system font
stack. Here it is approximated from Helvetica AFM metrics scaled to 14px. The
formula quantises to 20px steps, which absorbs most of the error, but node-level
findings are still heuristic -- they are reported as warnings, never errors.
Group-level findings use stored x/y/w/h and are exact.

CLI
    python3 -m nodered_mcp.layout                      # lint the live flows.json
    python3 -m nodered_mcp.layout --tab Windows        # one tab only
    python3 -m nodered_mcp.layout --check group-overlap,group-escape
    python3 -m nodered_mcp.layout --json               # machine-readable findings
    python3 -m nodered_mcp.layout --quiet              # errors only
    python3 -m nodered_mcp.layout --calibrate          # validate the width model
    python3 -m nodered_mcp.layout --fix boxes          # dry-run: refit group boxes
    python3 -m nodered_mcp.layout --fix boxes,move --apply    # commit the repair
    python3 -m nodered_mcp.layout --path other.json

Exit code is 1 if any ERROR-severity finding is present, else 0.

Python
    from layout_util import lint, node_box, group_box_for
    findings = lint(nodes)                      # nodes = the flows.json list
"""

import argparse
import json
import math
import os
import sys
from collections import defaultdict

# No hardcoded host path: the flows.json location is configuration.
# Precedence is caller-supplied path > NODERED_FLOWS_PATH > error.
FLOWS_PATH = os.environ.get("NODERED_FLOWS_PATH") or None

NODE_MIN_WIDTH = 100
NODE_MIN_HEIGHT = 30
GROUP_PADDING = 25
BUTTON_PADDING = 20

# Node types whose label the editor hides, so they render as a 30x30 icon.
HIDDEN_LABEL_TYPES = {"link in", "link out"}

# Node types with no input port. Only affects a +7px term inside a 20px
# quantisation, so an omission here is nearly always invisible.
NO_INPUT_TYPES = {
    "inject",
    "mqtt in",
    "http in",
    "websocket in",
    "tcp in",
    "udp in",
    "serial in",
    "watch",
    "status",
    "catch",
    "link in",
    "cronplus",
    "server-state-changed",
    "server-events",
    "ha-time",
    "ha-tag",
    "ha-webhook",
    "ha-device",
    "poll-state",
    "trigger-state",
    "ha-sentence",
    "server-device",
    "big-timer",
    "time-range-switch",
    "sun-position",
}

# Nodes drawn with a button. Inject's is on the left, debug's on the right;
# each widens the group box by 20px on that side.
BUTTON_LEFT_TYPES = {"inject"}
BUTTON_RIGHT_TYPES = {"debug"}

# Never occupy canvas space.
ZERO_SIZE_TYPES = {"junction"}

# Not canvas nodes at all.
NON_CANVAS_TYPES = {"tab", "subflow", "group", "comment"}

# Helvetica AFM advance widths, units per 1000em.
# fmt: off
# A 100-entry glyph-width table. One entry per line would be 100 lines of
# noise for data that is only ever read as a lookup, so the formatter is
# switched off across it.
_AFM = {
    " ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667,
    "'": 191, "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333,
    ".": 278, "/": 278, ":": 278, ";": 278, "<": 584, "=": 584, ">": 584,
    "?": 556, "@": 1015, "[": 278, "\\": 278, "]": 278, "^": 469, "_": 556,
    "`": 333, "{": 334, "|": 260, "}": 334, "~": 584,
    "A": 667, "B": 667, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778,
    "H": 722, "I": 278, "J": 500, "K": 667, "L": 556, "M": 833, "N": 722,
    "O": 778, "P": 667, "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722,
    "V": 667, "W": 944, "X": 667, "Y": 667, "Z": 611,
    "a": 556, "b": 556, "c": 500, "d": 556, "e": 556, "f": 278, "g": 556,
    "h": 556, "i": 222, "j": 222, "k": 500, "l": 222, "m": 833, "n": 556,
    "o": 556, "p": 556, "q": 556, "r": 333, "s": 500, "t": 278, "u": 556,
    "v": 500, "w": 722, "x": 500, "y": 500, "z": 500,
}
# fmt: on
for _d in "0123456789":
    _AFM[_d] = 556

FONT_SIZE = 14
# Scale from Helvetica metrics to the editor's actual rendered font. 1.0 is
# plain Helvetica; see --calibrate for how this was checked against the 110
# real groups in flows.json.
CHAR_SCALE = 1.0
_FALLBACK_WIDTH = 556  # unknown latin-ish glyph
_WIDE_WIDTH = 1000  # emoji / CJK


def text_width(s):
    """Approximate the rendered pixel width of a node label at 14px."""
    total = 0
    for ch in s:
        w = _AFM.get(ch)
        if w is None:
            w = _WIDE_WIDTH if ord(ch) > 0x2FFF else _FALLBACK_WIDTH
        total += w
    return total / 1000.0 * FONT_SIZE * CHAR_SCALE


def subflow_names(nodes):
    """Map subflow id -> its name, for labelling subflow instance nodes."""
    return {n["id"]: n.get("name", "") for n in nodes if isinstance(n, dict) and n.get("type") == "subflow"}


def node_label(node, subflows=None):
    """Best-effort reproduction of RED.utils.getNodeLabel().

    The editor falls back to each node type's own label function, which this
    cannot execute. Unnamed nodes therefore fall back to the type name (or the
    subflow's name for a subflow instance) and may be measured narrower than
    they render.
    """
    name = node.get("name")
    if name:
        return name
    t = node.get("type", "")
    if t.startswith("subflow:") and subflows:
        return subflows.get(t.split(":", 1)[1], t)
    return t


def is_canvas_node(node):
    """True if the node occupies space on a tab's canvas.

    Structural, not a type allowlist: config nodes are exactly the ones with
    no coordinates, whatever palette they came from.
    """
    if not isinstance(node, dict):
        return False
    if node.get("type", "") in NON_CANVAS_TYPES:
        return False
    return "x" in node and "y" in node


def node_size(node, subflows=None):
    """Return (w, h) in canvas pixels, per the editor's own sizing rules."""
    t = node.get("type", "")
    if t in ZERO_SIZE_TYPES:
        return 0, 0

    outputs = node.get("outputs")
    if outputs is None:
        wires = node.get("wires")
        outputs = len(wires) if isinstance(wires, list) else 0

    if "l" in node:
        hide_label = not node["l"]
    else:
        hide_label = t in HIDDEN_LABEL_TYPES

    if hide_label:
        return NODE_MIN_HEIGHT, max(NODE_MIN_HEIGHT, outputs * 15)

    label = node_label(node, subflows)
    lines = label.split("\n")
    tw = max((text_width(line) for line in lines), default=0)
    has_input = 0 if t in NO_INPUT_TYPES else 1
    w = max(NODE_MIN_WIDTH, 20 * math.ceil((tw + 50 + (7 if has_input else 0)) / 20))
    h = max(6 + 24 * len(lines), outputs * 15, NODE_MIN_HEIGHT)
    return w, h


def node_box(node, subflows=None):
    """Return (x1, y1, x2, y2) for a node, or None if it takes no space."""
    w, h = node_size(node, subflows)
    if w == 0 and h == 0:
        return None
    x, y = node["x"], node["y"]
    return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)


def group_rect(group):
    """Return (x1, y1, x2, y2) from a group's stored geometry."""
    return (group["x"], group["y"], group["x"] + group.get("w", 0), group["y"] + group.get("h", 0))


def group_box_for(members, subflows=None):
    """Compute the group box the editor would draw around `members`.

    Mirrors addToGroup(): 25px padding, +20 on the button side for
    inject/debug. Returns None when no member occupies space.
    """
    x1 = y1 = float("inf")
    x2 = y2 = float("-inf")
    seen = False
    for n in members:
        if n.get("type") == "group":
            gx1, gy1, gx2, gy2 = group_rect(n)
            lx, rx = gx1 - GROUP_PADDING, gx2 + GROUP_PADDING
            ty, by = gy1 - GROUP_PADDING, gy2 + GROUP_PADDING
        else:
            box = node_box(n, subflows)
            if box is None:
                continue
            bx1, by1, bx2, by2 = box
            t = n.get("type", "")
            lpad = GROUP_PADDING + (BUTTON_PADDING if t in BUTTON_LEFT_TYPES else 0)
            rpad = GROUP_PADDING + (BUTTON_PADDING if t in BUTTON_RIGHT_TYPES else 0)
            lx, rx = bx1 - lpad, bx2 + rpad
            ty, by = by1 - GROUP_PADDING, by2 + GROUP_PADDING
        seen = True
        x1, y1 = min(x1, lx), min(y1, ty)
        x2, y2 = max(x2, rx), max(y2, by)
    return (x1, y1, x2, y2) if seen else None


def intersect(a, b):
    """Overlap of two boxes as (dx, dy), or None if they don't intersect."""
    dx = min(a[2], b[2]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[1], b[1])
    return (dx, dy) if dx > 0 and dy > 0 else None


def contains(outer, inner, tol=0.5):
    """True if `inner` sits entirely within `outer`."""
    return (
        inner[0] >= outer[0] - tol
        and inner[1] >= outer[1] - tol
        and inner[2] <= outer[2] + tol
        and inner[3] <= outer[3] + tol
    )


class Finding:
    """One layout problem, keyed so two scans can be diffed."""

    def __init__(self, check, severity, tab, message, ids, detail=None):
        self.check = check
        self.severity = severity
        self.tab = tab
        self.message = message
        self.ids = sorted(ids)
        self.detail = detail or {}

    @property
    def key(self):
        return (self.check, tuple(self.ids))

    def as_dict(self):
        return {
            "check": self.check,
            "severity": self.severity,
            "tab": self.tab,
            "message": self.message,
            "ids": self.ids,
            "detail": self.detail,
        }

    def __repr__(self):
        return f"<{self.severity} {self.check} {self.message}>"


CHECKS = ("group-overlap", "group-escape", "stray-in-group", "node-overlap")
SEVERITY = {
    "group-overlap": "ERROR",
    "group-escape": "ERROR",
    "stray-in-group": "WARN",
    "node-overlap": "WARN",
}


def _ancestors(group, groups_by_id):
    seen = []
    cur = group.get("g")
    while cur and cur in groups_by_id and cur not in seen:
        seen.append(cur)
        cur = groups_by_id[cur].get("g")
    return seen


def lint(nodes, checks=CHECKS, tab_filter=None):
    """Lint a flows.json node list. Returns a list of Finding."""
    tabs = {n["id"]: n.get("label", n["id"]) for n in nodes if isinstance(n, dict) and n.get("type") == "tab"}
    groups = [n for n in nodes if isinstance(n, dict) and n.get("type") == "group"]
    groups_by_id = {g["id"]: g for g in groups}
    by_id = {n["id"]: n for n in nodes if isinstance(n, dict) and "id" in n}
    subflows = subflow_names(nodes)

    def keep(z):
        if z not in tabs:
            return False
        return tab_filter is None or tabs[z] == tab_filter

    groups_by_tab = defaultdict(list)
    for g in groups:
        if keep(g.get("z")):
            groups_by_tab[g["z"]].append(g)

    nodes_by_tab = defaultdict(list)
    for n in nodes:
        if is_canvas_node(n) and keep(n.get("z")):
            nodes_by_tab[n["z"]].append(n)

    out = []

    if "group-overlap" in checks:
        for z, gs in groups_by_tab.items():
            for i in range(len(gs)):
                for j in range(i + 1, len(gs)):
                    a, b = gs[i], gs[j]
                    if b["id"] in _ancestors(a, groups_by_id):
                        continue
                    if a["id"] in _ancestors(b, groups_by_id):
                        continue
                    ov = intersect(group_rect(a), group_rect(b))
                    if ov:
                        out.append(
                            Finding(
                                "group-overlap",
                                SEVERITY["group-overlap"],
                                tabs[z],
                                f"'{a.get('name') or a['id']}' overlaps "
                                f"'{b.get('name') or b['id']}' by {ov[0]:g}x{ov[1]:g}px",
                                [a["id"], b["id"]],
                                {
                                    "overlap_w": ov[0],
                                    "overlap_h": ov[1],
                                    "a": a.get("name"),
                                    "b": b.get("name"),
                                },
                            )
                        )

    if "group-escape" in checks:
        for z, gs in groups_by_tab.items():
            for g in gs:
                stored = group_rect(g)
                members = [by_id[nid] for nid in g.get("nodes", []) if nid in by_id]
                for m in members:
                    if m.get("type") == "group":
                        box = group_rect(m)
                    else:
                        box = node_box(m, subflows)
                    if box is None:
                        continue
                    if not contains(stored, box):
                        out.append(
                            Finding(
                                "group-escape",
                                SEVERITY["group-escape"],
                                tabs[z],
                                f"'{m.get('name') or m.get('type')}' at "
                                f"({m['x']:g},{m['y']:g}) sticks out of group "
                                f"'{g.get('name') or g['id']}'",
                                [g["id"], m["id"]],
                                {
                                    "group": g.get("name"),
                                    "node": m.get("name"),
                                    "node_box": [round(v) for v in box],
                                    "group_box": [round(v) for v in stored],
                                },
                            )
                        )

    if "stray-in-group" in checks:
        for z, ns in nodes_by_tab.items():
            for n in ns:
                box = node_box(n, subflows)
                if box is None:
                    continue
                own = n.get("g")
                own_chain = set()
                if own and own in groups_by_id:
                    own_chain = {own} | set(_ancestors(groups_by_id[own], groups_by_id))
                for g in groups_by_tab[z]:
                    if g["id"] in own_chain:
                        continue
                    if intersect(group_rect(g), box):
                        out.append(
                            Finding(
                                "stray-in-group",
                                SEVERITY["stray-in-group"],
                                tabs[z],
                                f"'{n.get('name') or n.get('type')}' at "
                                f"({n['x']:g},{n['y']:g}) sits inside group "
                                f"'{g.get('name') or g['id']}' but is not a member"
                                + (
                                    f" (member of '{groups_by_id[own].get('name')}')"
                                    if own in groups_by_id
                                    else " (ungrouped)"
                                ),
                                [g["id"], n["id"]],
                                {"group": g.get("name"), "node": n.get("name"), "node_group": own},
                            )
                        )

    if "node-overlap" in checks:
        for z, ns in nodes_by_tab.items():
            boxed = [(n, node_box(n, subflows)) for n in ns]
            boxed = [(n, b) for n, b in boxed if b is not None]
            boxed.sort(key=lambda p: p[1][0])
            for i in range(len(boxed)):
                a, ab = boxed[i]
                for j in range(i + 1, len(boxed)):
                    b, bb = boxed[j]
                    if bb[0] >= ab[2]:
                        break
                    ov = intersect(ab, bb)
                    if ov:
                        out.append(
                            Finding(
                                "node-overlap",
                                SEVERITY["node-overlap"],
                                tabs[z],
                                f"'{a.get('name') or a.get('type')}' ({a['x']:g},{a['y']:g}) "
                                f"overlaps '{b.get('name') or b.get('type')}' "
                                f"({b['x']:g},{b['y']:g}) by {ov[0]:g}x{ov[1]:g}px",
                                [a["id"], b["id"]],
                                {"overlap_w": ov[0], "overlap_h": ov[1]},
                            )
                        )

    out.sort(key=lambda f: (f.severity != "ERROR", f.tab, f.check, f.message))
    return out


def new_findings(before, after):
    """Findings present in `after` but not in `before`, keyed by check+ids."""
    old = {f.key for f in before}
    return [f for f in after if f.key not in old]


def load(path=None):
    target = path or FLOWS_PATH
    if not target:
        raise ValueError("No flows.json path given. Pass path=..., or set NODERED_FLOWS_PATH.")
    with open(target) as fh:
        return json.load(fh)


def calibrate(nodes):
    """Sanity-check the width model against every real group in flows.json.

    The editor grows a group box to fit its members, so a correct model gives
    computed <= stored for groups the editor last touched. Systematic
    computed > stored means the model is measuring nodes too wide.
    """
    by_id = {n["id"]: n for n in nodes if isinstance(n, dict) and "id" in n}
    groups = [n for n in nodes if isinstance(n, dict) and n.get("type") == "group"]
    subflows = subflow_names(nodes)
    rows = []
    for g in groups:
        members = [by_id[nid] for nid in g.get("nodes", []) if nid in by_id]
        computed = group_box_for(members, subflows)
        if computed is None:
            continue
        stored = group_rect(g)
        rows.append(
            (
                g.get("name") or g["id"],
                stored[0] - computed[0],  # +ve = stored box starts left of needed
                stored[1] - computed[1],
                computed[2] - stored[2],  # +ve = content overflows right edge
                computed[3] - stored[3],
            )
        )
    if not rows:
        print("no groups with members")
        return
    over_r = [r for r in rows if r[3] > 0.5]
    over_b = [r for r in rows if r[4] > 0.5]
    print(f"groups checked: {len(rows)}   CHAR_SCALE={CHAR_SCALE}")
    print(f"  content overflows RIGHT edge: {len(over_r)}")
    print(f"  content overflows BOTTOM edge: {len(over_b)}")
    if over_r:
        worst = sorted(over_r, key=lambda r: -r[3])[:10]
        print("  worst right overflows (px):")
        for name, _, _, dr, _ in worst:
            print(f"    {dr:8.1f}  {name}")
    slack_r = sorted(-r[3] for r in rows)
    print(
        f"  right-edge slack: min={slack_r[0]:.0f} "
        f"median={slack_r[len(slack_r) // 2]:.0f} max={slack_r[-1]:.0f}"
    )


def fix(path=None, ops=("boxes", "move"), apply=False, tab_filter=None):
    """Repair layout errors. Dry-run unless apply=True.

    boxes  Refit every group box to exactly enclose its members. Repairs
           group-escape errors (a group whose box no longer covers its own
           nodes) without moving a single node.
    move   Relocate groups that sit on top of another group. The group with
           fewer members moves; its nodes travel with it.

    Returns (findings_before, findings_after).
    """
    from .flows import Flows  # deferred: flows.py imports this module

    f = Flows(path)
    before = lint(f.nodes, tab_filter=tab_filter)
    tabs = {
        n["id"]: n.get("label", n["id"]) for n in f.nodes if isinstance(n, dict) and n.get("type") == "tab"
    }
    changes = []

    if "boxes" in ops:
        for g in [n for n in f.nodes if isinstance(n, dict) and n.get("type") == "group"]:
            if tab_filter and tabs.get(g.get("z")) != tab_filter:
                continue
            old = (g["x"], g["y"], g.get("w"), g.get("h"))
            if f.refit_group(g["id"]):
                new = (g["x"], g["y"], g["w"], g["h"])
                changes.append(f"refit  [{tabs.get(g.get('z'), '?')}] '{g.get('name')}' {old} -> {new}")

    if "move" in ops:
        for _ in range(20):
            overlaps = [x for x in lint(f.nodes, checks=("group-overlap",), tab_filter=tab_filter)]
            if not overlaps:
                break
            finding = overlaps[0]
            a, b = (f.by_id[i] for i in finding.ids)
            mover = a if len(a.get("nodes", [])) <= len(b.get("nodes", [])) else b
            w, h = mover.get("w", 0), mover.get("h", 0)
            nx, ny = f.free_region(mover["z"], w, h)
            dx, dy = nx - mover["x"], ny - mover["y"]
            f.shift_group(mover["id"], dx, dy)
            changes.append(
                f"move   [{tabs.get(mover.get('z'), '?')}] '{mover.get('name')}' "
                f"by ({dx:+g},{dy:+g}) -> ({nx},{ny})"
            )

    after = lint(f.nodes, tab_filter=tab_filter)

    print(f"{'APPLYING' if apply else 'DRY RUN'} -- {len(changes)} change(s)")
    for c in changes:
        print(f"  {c}")

    def tally(fs):
        c = defaultdict(int)
        for x in fs:
            c[x.check] += 1
        return c

    tb, ta = tally(before), tally(after)
    print("\nfindings before -> after:")
    for check in CHECKS:
        if tb[check] or ta[check]:
            arrow = "" if tb[check] == ta[check] else ("  WORSE" if ta[check] > tb[check] else "  better")
            print(f"  {SEVERITY[check]:5} {check:15} {tb[check]:3} -> {ta[check]:3}{arrow}")
    print(f"  {'total':5} {'':15} {len(before):3} -> {len(after):3}")

    if apply:
        if not changes:
            print("nothing to write")
        else:
            f.save()
    else:
        print("\nnothing written -- re-run with --apply to commit")
    return before, after


def report(findings, quiet=False):
    if not findings:
        print("layout clean: no findings")
        return
    counts = defaultdict(int)
    for f in findings:
        counts[f.check] += 1
    by_tab = defaultdict(list)
    for f in findings:
        if quiet and f.severity != "ERROR":
            continue
        by_tab[f.tab].append(f)
    for tab in sorted(by_tab):
        print(f"\n=== {tab} ===")
        for f in by_tab[tab]:
            print(f"  [{f.severity:5}] {f.check:15} {f.message}")
    print("\nsummary:")
    for check in CHECKS:
        if counts[check]:
            print(f"  {SEVERITY[check]:5} {check:15} {counts[check]}")
    errs = sum(1 for f in findings if f.severity == "ERROR")
    print(f"  total {len(findings)} ({errs} errors)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lint Node-RED canvas layout.")
    ap.add_argument("--path", default=FLOWS_PATH, help="flows.json to lint (default: $NODERED_FLOWS_PATH)")
    ap.add_argument("--tab", help="limit to one tab by label")
    ap.add_argument("--check", help=f"comma-separated subset of {','.join(CHECKS)}")
    ap.add_argument("--json", action="store_true", help="emit findings as JSON")
    ap.add_argument("--quiet", action="store_true", help="print errors only")
    ap.add_argument(
        "--calibrate", action="store_true", help="validate the node width model against real groups"
    )
    ap.add_argument("--fix", metavar="OPS", help="repair layout: 'boxes', 'move', or 'boxes,move'")
    ap.add_argument(
        "--apply", action="store_true", help="with --fix, actually write flows.json (default: dry run)"
    )
    args = ap.parse_args(argv)

    if not args.path:
        ap.error("no flows.json path: pass --path or set NODERED_FLOWS_PATH")

    nodes = load(args.path)

    if args.calibrate:
        calibrate(nodes)
        return 0

    if args.fix:
        ops = tuple(o.strip() for o in args.fix.split(","))
        bad_ops = [o for o in ops if o not in ("boxes", "move")]
        if bad_ops:
            ap.error(f"unknown fix op(s): {','.join(bad_ops)}")
        fix(args.path, ops=ops, apply=args.apply, tab_filter=args.tab)
        return 0

    checks = tuple(c.strip() for c in args.check.split(",")) if args.check else CHECKS
    bad = [c for c in checks if c not in CHECKS]
    if bad:
        ap.error(f"unknown check(s): {','.join(bad)}")

    findings = lint(nodes, checks=checks, tab_filter=args.tab)
    if args.json:
        print(json.dumps([f.as_dict() for f in findings], indent=2))
    else:
        report(findings, quiet=args.quiet)
    return 1 if any(f.severity == "ERROR" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
