"""
hvac_calculator.py  —  General HVAC Air-Balance & Terminal-Selection Tool
=========================================================================
Works for any HVAC project: cleanrooms, offices, hospitals, factories, labs.
Terminal types, tolerances, and pressure offsets are all configurable.

5-module pipeline:
  A  Input    — Excel file or built-in demo; accepts English or Chinese headers
  B  Calc     — per-room airflow derivation, pressure enforcement, terminal sizing
  C  Zone     — aggregate by AHU / system / zone
  D  Validate — air balance, pressure logic, completeness
  E  Output   — console schedule + Excel (two sheets)

CLI usage:
  python hvac_calculator.py                         # run with demo data
  python hvac_calculator.py input.xlsx              # process your file
  python hvac_calculator.py input.xlsx output.xlsx  # custom output path
  python hvac_calculator.py --template              # generate blank input template
  python hvac_calculator.py --config my_config.json input.xlsx

Config file (JSON) — all fields optional, shown with defaults:
  {
    "pressure_offset":   50,
    "balance_tolerance":  5,
    "terminal_types": [
      {"name": "T1", "max_flow": 200, "capacity": 150},
      {"name": "T2", "max_flow": 500, "capacity": 350},
      {"name": "T3", "max_flow": 1e9, "capacity": 700}
    ]
  }

Input Excel accepted column names (English or Chinese):
  room_number / 房间编号        room_name / 房间名称
  room_function / 功能分类      cleanliness_class / 洁净等级
  pressure_target / 压差要求    zone_id / 所属系统
  supply_air / 送风量           return_air / 回风量
  fresh_air / 新风量            exhaust_air / 排风量
  filter_qty / 高效数量         filter_spec / 高效规格
"""
import sys
import io
import json
import math
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
from datetime import datetime

# ── UTF-8 output on Windows ───────────────────────────────────────────────────
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


# ── Default configuration ─────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    # m³/h surplus enforced for positive-pressure rooms
    "pressure_offset": 50,
    # m³/h tolerance before air balance is flagged as an error
    "balance_tolerance": 5,
    # Terminal type definitions — name, upper airflow bound (m³/h), capacity per unit
    # Override in config JSON to match your project's grille catalogue
    "terminal_types": [
        {"name": "T1", "max_flow": 200,   "capacity": 150},
        {"name": "T2", "max_flow": 500,   "capacity": 350},
        {"name": "T3", "max_flow": 1e9,   "capacity": 700},
    ],
}


def load_config(path: Optional[str]) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if path and Path(path).exists():
        with open(path, encoding="utf-8") as f:
            overrides = json.load(f)
        cfg.update(overrides)
        print(f"[config] Loaded {path}")
    return cfg


def select_terminal(airflow: float, terminal_types: list) -> tuple[str, int]:
    """Return (spec_name, quantity) for a given total airflow."""
    if airflow <= 0:
        return "—", 0
    for t in sorted(terminal_types, key=lambda x: x["max_flow"]):
        if airflow <= t["max_flow"]:
            spec = t["name"]
            qty = math.ceil(airflow / t["capacity"])
            return spec, qty
    # Fallback: largest terminal type
    t = max(terminal_types, key=lambda x: x["max_flow"])
    return t["name"], math.ceil(airflow / t["capacity"])


# ── Column name aliases (English primary, Chinese accepted) ───────────────────
_ALIASES = {
    "房间编号": "room_number",  "room number": "room_number",
    "房间名称": "room_name",    "room name":   "room_name",
    "功能分类": "room_function","function":    "room_function",
    "洁净等级": "cleanliness_class", "cleanliness": "cleanliness_class",
    "压差要求": "pressure_target",   "pressure":    "pressure_target",
    "所属系统": "zone_id",      "zone":        "zone_id",  "system": "zone_id",
    "送风量":   "supply_air",   "supply":      "supply_air",
    "回风量":   "return_air",   "return":      "return_air",
    "新风量":   "fresh_air",    "fresh":       "fresh_air",  "oa": "fresh_air",
    "排风量":   "exhaust_air",  "exhaust":     "exhaust_air",
    "高效数量": "filter_qty",   "filter qty":  "filter_qty",
    "高效规格": "filter_spec",  "filter spec": "filter_spec",
}

def _normalize_col(name: str) -> str:
    return _ALIASES.get(name, _ALIASES.get(name.lower(), name.lower().replace(" ", "_")))


# ── Data models ───────────────────────────────────────────────────────────────
@dataclass
class Room:
    room_number: str
    room_name: str
    room_function: str = "general"
    cleanliness_class: str = "non-clean"
    pressure_target: str = "0"        # "+" positive / "0" neutral / "-" negative
    zone_id: str = "Z1"

    supply_air: float = 0.0           # m³/h — all airflow fields
    return_air: float = 0.0
    fresh_air: float = 0.0
    exhaust_air: float = 0.0

    filter_qty: int = 0
    filter_spec: str = ""

    # Computed
    return_terminal_spec: str = ""
    return_terminal_qty: int = 0
    exhaust_terminal_spec: str = ""
    exhaust_terminal_qty: int = 0
    air_balance_error: float = 0.0
    is_valid: bool = True
    validation_notes: list = field(default_factory=list)


@dataclass
class Zone:
    zone_id: str
    total_supply: float = 0.0
    total_return: float = 0.0
    total_fresh: float = 0.0
    total_exhaust: float = 0.0
    filter_qty: int = 0
    terminals: dict = field(default_factory=dict)   # {"T1": n, "T2": n, ...}
    room_count: int = 0

    def add_terminal(self, spec: str, qty: int):
        if spec and spec != "—":
            self.terminals[spec] = self.terminals.get(spec, 0) + qty


# ── Module A: Input ───────────────────────────────────────────────────────────
_FLOAT_FIELDS = {"supply_air", "return_air", "fresh_air", "exhaust_air"}
_INT_FIELDS   = {"filter_qty", "return_terminal_qty", "exhaust_terminal_qty"}

def load_from_excel(path: str) -> list[Room]:
    try:
        import pandas as pd
    except ImportError:
        print("pandas not installed. Run: pip install pandas openpyxl"); sys.exit(1)

    df = pd.read_excel(path, engine="openpyxl")
    df.columns = [_normalize_col(str(c)) for c in df.columns]

    rooms = []
    for _, row in df.iterrows():
        kwargs = {}
        for col in Room.__dataclass_fields__:
            if col in row.index:
                val = row[col]
                if val is None or (isinstance(val, float) and math.isnan(val)):
                    continue
                if col in _FLOAT_FIELDS:
                    try: val = float(val)
                    except: val = 0.0
                elif col in _INT_FIELDS:
                    try: val = int(val)
                    except: val = 0
                else:
                    val = str(val).strip()
                kwargs[col] = val
        if "room_number" not in kwargs:
            continue   # skip blank rows
        rooms.append(Room(**kwargs))

    print(f"[A] Loaded {len(rooms)} rooms from {path}")
    return rooms


def generate_template(out_path: str = "hvac_input_template.xlsx"):
    """Write a blank Excel template with correct English column headers."""
    try:
        import pandas as pd
    except ImportError:
        print("pandas not installed"); return

    columns = [
        "room_number", "room_name", "room_function", "cleanliness_class",
        "pressure_target", "zone_id",
        "supply_air", "return_air", "fresh_air", "exhaust_air",
        "filter_qty", "filter_spec",
    ]
    example = {
        "room_number": "101", "room_name": "Office A", "room_function": "office",
        "cleanliness_class": "non-clean", "pressure_target": "0", "zone_id": "Z1",
        "supply_air": 500, "return_air": 0, "fresh_air": 100, "exhaust_air": 0,
        "filter_qty": 0, "filter_spec": "",
    }
    pd.DataFrame([example], columns=columns).to_excel(out_path, index=False, engine="openpyxl")
    print(f"[A] Template saved -> {out_path}")
    print("    pressure_target: + (positive)  /  0 (neutral)  /  - (negative)")
    print("    Leave return_air=0 to auto-derive from air balance.")


def demo_data() -> list[Room]:
    """
    Generic mixed-use building demo: office, lab, storage, server room, corridor.
    Replace with load_from_excel() for real projects.
    """
    return [
        Room("101", "Open Office",    "office",   "non-clean", "0",  "AHU-1", supply_air=1200, fresh_air=300, exhaust_air=0),
        Room("102", "Meeting Room",   "office",   "non-clean", "0",  "AHU-1", supply_air=400,  fresh_air=120, exhaust_air=0),
        Room("103", "Reception",      "corridor", "non-clean", "0",  "AHU-1", supply_air=250,  fresh_air=60,  exhaust_air=0),
        Room("104", "Toilet Block",   "toilet",   "non-clean", "-",  "AHU-1", supply_air=180,  fresh_air=180, exhaust_air=220),
        Room("105", "Kitchen",        "kitchen",  "non-clean", "-",  "AHU-1", supply_air=300,  fresh_air=300, exhaust_air=450),
        Room("201", "Clean Lab",      "lab",      "ISO-7",     "+",  "AHU-2", supply_air=2000, fresh_air=400, exhaust_air=0,  filter_qty=4),
        Room("202", "Prep Room",      "lab",      "ISO-7",     "+",  "AHU-2", supply_air=800,  fresh_air=160, exhaust_air=0,  filter_qty=2),
        Room("203", "Gowning",        "changing", "ISO-8",     "+",  "AHU-2", supply_air=350,  fresh_air=70,  exhaust_air=0,  filter_qty=1),
        Room("204", "Transfer Airlock","transfer", "ISO-8",    "+",  "AHU-2", supply_air=200,  fresh_air=40,  exhaust_air=0,  filter_qty=1),
        Room("301", "Server Room",    "IT",       "non-clean", "+",  "AHU-3", supply_air=3500, fresh_air=100, exhaust_air=0),
        Room("302", "Storage",        "storage",  "non-clean", "0",  "AHU-3", supply_air=150,  fresh_air=30,  exhaust_air=0),
        Room("303", "Corridor",       "corridor", "non-clean", "0",  "AHU-3", supply_air=200,  fresh_air=50,  exhaust_air=0),
    ]


# ── Module B: Per-room computation ───────────────────────────────────────────
def compute_room(room: Room, cfg: dict) -> Room:
    """
    1. Derive return_air if not supplied: return = supply - fresh - exhaust
    2. Enforce pressure surplus for positive-pressure rooms
    3. Recalculate air balance error
    4. Select terminal specs and quantities for return and exhaust streams
    """
    terminal_types = cfg["terminal_types"]
    pressure_offset = cfg["pressure_offset"]

    # 1. Derive return air
    if room.return_air == 0 and room.supply_air > 0:
        room.return_air = max(0.0, room.supply_air - room.fresh_air - room.exhaust_air)

    # 2. Pressure enforcement
    if room.pressure_target == "+":
        min_supply = room.return_air + room.fresh_air + room.exhaust_air + pressure_offset
        if room.supply_air < min_supply:
            room.supply_air = min_supply

    # 3. Air balance error
    expected = room.return_air + room.fresh_air + room.exhaust_air
    room.air_balance_error = round(room.supply_air - expected, 2)

    # 4. Terminal selection
    room.return_terminal_spec,  room.return_terminal_qty  = select_terminal(room.return_air,  terminal_types)
    room.exhaust_terminal_spec, room.exhaust_terminal_qty = select_terminal(room.exhaust_air, terminal_types)

    return room


# ── Module C: Zone aggregation ────────────────────────────────────────────────
def aggregate_zones(rooms: list[Room]) -> dict[str, Zone]:
    zones: dict[str, Zone] = {}
    for r in rooms:
        zid = r.zone_id or "Z1"
        if zid not in zones:
            zones[zid] = Zone(zone_id=zid)
        z = zones[zid]
        z.total_supply  += r.supply_air
        z.total_return  += r.return_air
        z.total_fresh   += r.fresh_air
        z.total_exhaust += r.exhaust_air
        z.filter_qty    += r.filter_qty
        z.add_terminal(r.return_terminal_spec,  r.return_terminal_qty)
        z.add_terminal(r.exhaust_terminal_spec, r.exhaust_terminal_qty)
        z.room_count += 1
    return zones


# ── Module D: Validation ──────────────────────────────────────────────────────
def validate(rooms: list[Room], zones: dict[str, Zone], cfg: dict) -> list[str]:
    tol = cfg["balance_tolerance"]
    errors = []

    for r in rooms:
        r.validation_notes = []

        if not r.room_number:
            r.validation_notes.append("missing room_number")
        if r.supply_air <= 0:
            r.validation_notes.append("supply_air is zero or missing")

        for fname in ("supply_air", "return_air", "fresh_air", "exhaust_air"):
            if getattr(r, fname) < 0:
                r.validation_notes.append(f"{fname} is negative ({getattr(r, fname)})")

        if abs(r.air_balance_error) > tol:
            r.validation_notes.append(
                f"air balance off by {r.air_balance_error:+.1f} m3/h "
                f"(supply={r.supply_air:.0f}, return={r.return_air:.0f}, "
                f"fresh={r.fresh_air:.0f}, exhaust={r.exhaust_air:.0f})"
            )

        if r.return_air > 0 and r.return_terminal_qty == 0:
            r.validation_notes.append("return terminal qty=0 but return_air>0")

        net = r.supply_air - r.return_air - r.exhaust_air
        if r.pressure_target == "+" and net < 0:
            r.validation_notes.append(f"pressure conflict: target=+ but net={net:.1f}")
        if r.pressure_target == "-" and net > 0:
            r.validation_notes.append(f"pressure conflict: target=- but net={net:.1f}")

        r.is_valid = len(r.validation_notes) == 0
        for note in r.validation_notes:
            errors.append(f"Room {r.room_number} ({r.room_name}): {note}")

    for z in zones.values():
        z_balance = z.total_supply - z.total_return - z.total_exhaust - z.total_fresh
        if abs(z_balance) > tol * z.room_count:
            errors.append(f"Zone {z.zone_id}: balance off by {z_balance:+.1f} m3/h")

    return errors


# ── Module E: Output ──────────────────────────────────────────────────────────
def print_report(rooms: list[Room], zones: dict[str, Zone], errors: list[str]):
    SEP = "=" * 90
    print(f"\n{SEP}")
    print("HVAC DESIGN SCHEDULE")
    print(f"Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(SEP)
    print(f"{'No':<6} {'Name':<20} {'Func':<10} {'Clean':<10} {'P':>2}  "
          f"{'Supply':>7} {'Return':>7} {'Fresh':>7} {'Exhaust':>7}  "
          f"{'RT':>4} {'RQ':>3}  {'ET':>4} {'EQ':>3}  {'FLT':>4}  {'':>3}")
    print("-" * 90)
    for r in rooms:
        status = "OK" if r.is_valid else "ERR"
        print(f"{r.room_number:<6} {r.room_name:<20} {r.room_function:<10} "
              f"{r.cleanliness_class:<10} {r.pressure_target:>2}  "
              f"{r.supply_air:>7.0f} {r.return_air:>7.0f} {r.fresh_air:>7.0f} {r.exhaust_air:>7.0f}  "
              f"{r.return_terminal_spec:>4} {r.return_terminal_qty:>3}  "
              f"{r.exhaust_terminal_spec:>4} {r.exhaust_terminal_qty:>3}  "
              f"{r.filter_qty:>4}  {status}")

    print(f"\n{SEP}\nZONE SUMMARY\n{SEP}")
    for zid, z in sorted(zones.items()):
        t_str = "  ".join(f"{k}:{v}" for k, v in sorted(z.terminals.items()))
        print(f"\nZone {zid}  ({z.room_count} rooms)")
        print(f"  Supply {z.total_supply:>8.0f}  Return {z.total_return:>8.0f}  "
              f"Fresh {z.total_fresh:>8.0f}  Exhaust {z.total_exhaust:>8.0f}  m3/h")
        print(f"  Filters: {z.filter_qty}   Terminals: {t_str or 'none'}")

    print(f"\n{SEP}")
    if errors:
        print(f"VALIDATION — {len(errors)} ISSUE(S)")
        print(SEP)
        for e in errors:
            print(f"  [!] {e}")
    else:
        print("VALIDATION — ALL CHECKS PASSED")
    print(SEP)


def export_excel(rooms: list[Room], zones: dict[str, Zone], out_path: str):
    try:
        import pandas as pd
    except ImportError:
        print("[E] pandas not installed — skipping Excel export"); return

    room_rows = [{
        "room_number":         r.room_number,
        "room_name":           r.room_name,
        "room_function":       r.room_function,
        "cleanliness_class":   r.cleanliness_class,
        "pressure_target":     r.pressure_target,
        "zone_id":             r.zone_id,
        "supply_air (m3/h)":   r.supply_air,
        "return_air (m3/h)":   r.return_air,
        "fresh_air (m3/h)":    r.fresh_air,
        "exhaust_air (m3/h)":  r.exhaust_air,
        "return_terminal":     r.return_terminal_spec,
        "return_terminal_qty": r.return_terminal_qty,
        "exhaust_terminal":    r.exhaust_terminal_spec,
        "exhaust_terminal_qty":r.exhaust_terminal_qty,
        "filter_qty":          r.filter_qty,
        "filter_spec":         r.filter_spec,
        "balance_error (m3/h)":r.air_balance_error,
        "validation":          "OK" if r.is_valid else "; ".join(r.validation_notes),
    } for r in rooms]

    zone_rows = [{
        "zone_id":             z.zone_id,
        "room_count":          z.room_count,
        "total_supply (m3/h)": z.total_supply,
        "total_return (m3/h)": z.total_return,
        "total_fresh (m3/h)":  z.total_fresh,
        "total_exhaust (m3/h)":z.total_exhaust,
        "filter_qty":          z.filter_qty,
        **{f"terminal_{k}": v for k, v in sorted(z.terminals.items())},
    } for _, z in sorted(zones.items())]

    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        pd.DataFrame(room_rows).to_excel(w, sheet_name="Room Schedule", index=False)
        pd.DataFrame(zone_rows).to_excel(w, sheet_name="Zone Summary",  index=False)

    print(f"[E] Excel saved -> {out_path}")


# ── Public API (importable) ───────────────────────────────────────────────────
def run(input_excel: Optional[str] = None,
        output_excel: Optional[str] = None,
        config_path: Optional[str] = None,
        rooms: Optional[list] = None) -> tuple:
    """
    Main entry point — call from code or CLI.

    Parameters
    ----------
    input_excel  : path to input .xlsx  (None → use demo data or `rooms`)
    output_excel : path to output .xlsx (None → auto-named next to script)
    config_path  : path to JSON config  (None → use defaults)
    rooms        : pre-built list[Room] (overrides input_excel and demo)

    Returns
    -------
    (rooms, zones, errors)
    """
    cfg = load_config(config_path)

    # A: Input
    if rooms is None:
        if input_excel:
            rooms = load_from_excel(input_excel)
        else:
            print("[A] Using built-in demo data")
            rooms = demo_data()

    # B: Compute
    print(f"[B] Computing {len(rooms)} rooms ...")
    rooms = [compute_room(r, cfg) for r in rooms]

    # C: Aggregate
    zones = aggregate_zones(rooms)
    print(f"[C] {len(zones)} zone(s)")

    # D: Validate
    errors = validate(rooms, zones, cfg)
    print(f"[D] {len(errors)} validation issue(s)")

    # E: Output
    print_report(rooms, zones, errors)

    out = output_excel or str(Path(__file__).parent / "hvac_schedule_output.xlsx")
    export_excel(rooms, zones, out)

    return rooms, zones, errors


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]

    if "--template" in args:
        tpl = next((a for a in args if not a.startswith("-")), "hvac_input_template.xlsx")
        generate_template(tpl)
        sys.exit(0)

    config_path = None
    if "--config" in args:
        i = args.index("--config")
        config_path = args[i + 1]
        args = args[:i] + args[i + 2:]

    positional = [a for a in args if not a.startswith("-")]
    inp = positional[0] if len(positional) > 0 else None
    out = positional[1] if len(positional) > 1 else None

    run(inp, out, config_path)
