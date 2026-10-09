"""Normalize the pinned IEEE 8500-node text dataset (balanced load case) without a DSS engine.

Medium-voltage reduction: each 1-phase center-tapped service transformer, its triplex line and
its 120/240 V load are replaced by a constant-power load on the transformer's primary bus and
phase (7.2 kV line-to-neutral). The secondary network, transformer losses and impedances are
dropped. Regulator taps have no published reference, so they are set by emulating the
RegControl settings (vreg, band) at nominal load with the runtime pandapower model.
Run from any directory after obtaining the source files in data/ieee8500/source.
"""
from pathlib import Path
import hashlib
import json
import re
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/ieee8500/source"
TARGET = ROOT / "src/network/data/ieee8500.json"
COMMIT = "5005c668a72d20775f4c2d060feebb2866ba1d38"
URL = "https://github.com/tshort/OpenDSS/tree/"+COMMIT+"/Distrib/IEEETestCases/8500-Node"
MV_KV, HV_KV = 12.47, 115.
DEFAULT_AMPS = 400.  # OpenDSS default normamps; the matrix linecodes of this feeder define none

# ---- Study adjustments (deviations from the IEEE reference data, recorded in provenance.adjustments) ----
# 1. Ampacity by phase conductor instead of the 400 A default. Typical overhead ACSR ratings
#    (75 C conductor, 25 C ambient, 0.6 m/s wind; catalog values such as Southwire's), applied by size
#    also to WPAL (weatherproof aluminium). Hypothesis, not IEEE data. Cables with normamps keep it.
CONDUCTOR_AMPS = {"397": 587., "4/0": 357., "2/0": 276., "1/0": 242., "2": 184., "4": 140., "6": 105.}
# 2. Voltage level: source 1.0 pu (reference 1.05) and every regulator aiming at 1.03 pu
#    (reference 126.5 V and 125 V on 120 V = 1.054 and 1.042 pu), so the base case S0 stays inside
#    ANSI range A (0.95-1.05 pu) instead of starting above 1.05 pu near the substation.
SOURCE_PU_REFERENCE, SOURCE_PU = 1.05, 1.0
VREG_PU = 1.03


def records(filename):
    result = []
    for raw in (SOURCE / filename).read_text().splitlines():
        line = re.split(r"!|//",raw)[0].strip().lower()
        if line.startswith("new "):
            result.append(line)
        elif line.startswith("~") and result:
            result[-1] += " " + line[1:]
    return result


def properties(line):
    return dict(re.findall(r"([\w%]+)\s*=\s*(\[[^\]]*\]|\([^)]*\)|[^\s]+)", line))


def terminal(value, n=3):
    """'bus.1.3' -> ('bus', 'AC'); node 0 (neutral) is dropped."""
    parts = value.split(".")
    nodes = [int(x) for x in parts[1:] if x != "0"]
    return parts[0], "".join("ABC"[x-1] for x in nodes) if nodes else "ABC"[:n]


def matrix(value):
    rows = [[float(x) for x in r.split()] for r in value.strip("[]()").split("|")]
    return [[rows[max(i,j)][min(i,j)] for j in range(len(rows))] for i in range(len(rows))]


def sequence_matrix(n, s1, s0):
    """Phase matrix from sequence values: self (2 s1 + s0)/3, mutual (s0 - s1)/3."""
    if n == 1:
        return [[s1]]
    return [[(2*s1+s0)/3 if i==j else (s0-s1)/3 for j in range(n)] for i in range(n)]


def linecode(p, n):
    if "rmatrix" in p:
        code = {key: matrix(p[key]) for key in ("rmatrix","xmatrix","cmatrix")}
    else:
        f = lambda k, default=0.: float(p.get(k,default))
        code = dict(rmatrix=sequence_matrix(n,f("r1"),f("r0",f("r1"))),
                    xmatrix=sequence_matrix(n,f("x1"),f("x0",f("x1"))),
                    cmatrix=sequence_matrix(n,f("c1"),f("c0",f("c1"))))
    code["ampacity_a"] = float(p.get("normamps",DEFAULT_AMPS))
    return code


def conductor_ampacity(name):
    """Rating of the phase conductor named first in an IEEE 8500 linecode
    ('3ph_h-397_acsr397_acsr397_acsr2/0_acsr' -> 397 ACSR; 'x' marks an absent phase)."""
    found = re.findall(r"(397|4/0|2/0|1/0|2|4|6)_(?:acsr|wpal|tpx)",name.split("-",1)[-1])
    return CONDUCTOR_AMPS[found[0]] if found else None


def _hops(lines, switches, banks, transformer):
    import networkx as nx
    g = nx.Graph()
    g.add_edges_from((e["from_bus"],e["to_bus"]) for e in [*lines,*banks,transformer,*(s for s in switches if s["closed"])])
    return nx.single_source_shortest_path_length(g,transformer["from_bus"])


def build():
    codes = {}
    for rec in records("LineCodes2.DSS"):
        p = properties(rec)
        codes[rec.split()[1].split(".",1)[1]] = linecode(p,int(p.get("nphases",3)))
    bus_phases = {}
    def register(bus, phases):
        bus_phases.setdefault(bus, set()).update(phases)
    lines, switches = [], []
    for rec in records("Lines.dss"):
        name, p = rec.split()[1].split(".",1)[1], properties(rec)
        n = int(p.get("phases",3))
        b1, ph = terminal(p["bus1"],n)
        b2, ph2 = terminal(p["bus2"],n)
        assert ph == ph2, rec
        register(b1,ph); register(b2,ph)
        if p.get("switch") == "y":
            switches.append(dict(id=name,from_bus=b1,to_bus=b2,phases=ph,closed=p.get("enabled","true")!="false"))
            continue
        assert p.get("units") == "km", rec
        code = p.get("linecode")
        if code is None:  # inline impedance (substation connector)
            code = f"inline_{name}"
            codes[code] = linecode(p,n)
        lines.append(dict(id=name,from_bus=b1,to_bus=b2,phases=ph,length_km=float(p["length"]),linecode=code))
    used = {l["linecode"] for l in lines}
    codes = {k: v for k,v in codes.items() if k in used}
    # Study adjustment 1: conductor ampacity; zero-length inline connectors get the substation rating.
    amps_changed = {}
    for name,code in codes.items():
        if name.startswith("inline_"):
            amps = round(27500/(np.sqrt(3)*MV_KV),1)
        elif code["ampacity_a"] == DEFAULT_AMPS and conductor_ampacity(name):
            amps = conductor_ampacity(name)
        else:
            continue
        amps_changed[name] = dict(reference_a=code["ampacity_a"],adopted_a=amps)
        code["ampacity_a"] = amps

    # Service transformers: secondary bus X... -> primary bus and phase
    primary = {}
    for rec in records("LoadXfmrCodes.dss"):
        if not rec.split()[1].startswith("transformer."):
            continue
        buses = properties(rec)["buses"].strip("[]").split()
        bus, ph = terminal(buses[0],1)
        primary[buses[1].split(".")[0]] = (bus, ph)
    service = {}
    for rec in records("Triplex_Lines.DSS"):
        p = properties(rec)
        service[p["bus2"].split(".")[0]] = primary[p["bus1"].split(".")[0]]
    loads = []
    for rec in records("Loads.dss"):
        p = properties(rec)
        bus, ph = service[p["bus1"].split(".")[0]]
        register(bus,ph)
        kw, pf = float(p["kw"]), float(p["pf"])
        loads.append(dict(id=rec.split()[1].split(".",1)[1],bus=bus,phases=ph,connection="wye",
                          model=int(p["model"]),kv=MV_KV/np.sqrt(3),p_kw=kw,
                          q_kvar=round(kw*np.tan(np.arccos(pf)),6)))

    caps = []
    for rec in records("Capacitors.dss"):
        p = properties(rec)
        n = int(p.get("phases",3))
        bus, ph = terminal(p["bus1"],n)
        register(bus,ph)
        caps.append(dict(id=rec.split()[1].split(".",1)[1],bus=bus,phases=ph,q_kvar=float(p["kvar"]),
                         kv=MV_KV/np.sqrt(3) if n==1 else MV_KV))

    # Regulator banks (one single-phase unit per phase, ganged here) and the substation transformer
    controls = {}
    for filename in ("Transformers.dss","Regulators.dss"):
        for rec in records(filename):
            if rec.split()[1].startswith("regcontrol."):
                p = properties(rec)
                controls[p["transformer"]] = float(p["vreg"])*float(p["ptratio"])/(MV_KV/np.sqrt(3)*1000)
    banks, transformer = {}, None
    for filename in ("Transformers.dss","Regulators.dss"):
        for rec in records(filename):
            name, p = rec.split()[1].split(".",1)[1], properties(rec)
            if not rec.split()[1].startswith("transformer."):
                continue
            buses = p["buses"].strip("()[]").replace(",", " ").split()
            if "bank" in p:
                (b1,ph),(b2,_) = terminal(buses[0],1), terminal(buses[1],1)
                bank = banks.setdefault(p["bank"],dict(id=p["bank"],from_bus=b1,to_bus=b2,phases="",
                                                        sn_mva=float(p["kvas"].strip("()[]").replace(","," ").split()[0])/1000,
                                                        vk_percent=float(p["xhl"]),vn_kv=MV_KV))
                assert (bank["from_bus"],bank["to_bus"]) == (b1,b2)
                bank["phases"] += ph
                bank.setdefault("vreg_pu",[]).append(controls[name])
            else:
                kvas = float(p["kvas"].strip("()[]").replace(","," ").split()[0])
                r = float(re.findall(r"%r=([\d.]+)",rec)[0])
                transformer = dict(id=name,from_bus=terminal(buses[0])[0],to_bus=terminal(buses[1])[0],
                                   sn_mva=kvas/1000,vn_hv_kv=HV_KV,vn_lv_kv=MV_KV,connection="Dyn",
                                   vkr_percent=2*r,x_percent=float(p["xhl"]))
    regulators = []
    hops = _hops(lines,switches,list(banks.values()),transformer)
    for bank in sorted(banks.values(),key=lambda r: hops[r["from_bus"]]):  # upstream first
        bank["phases"] = "".join(sorted(bank["phases"]))
        bank["vreg_reference_pu"] = round(float(np.mean(bank.pop("vreg_pu"))),6)
        bank["vreg_pu"] = VREG_PU  # study adjustment 2
        bank["band_pu"] = round(2/120,6)  # band=2 V on the 120 V base (+/- 1 V)
        bank["taps"] = [0]*len(bank["phases"])
        regulators.append(bank)
        register(bank["from_bus"],bank["phases"]); register(bank["to_bus"],bank["phases"])
    register(transformer["from_bus"],"ABC"); register(transformer["to_bus"],"ABC")

    coords = {}
    for raw in (SOURCE/"Buscoords.dss").read_text().splitlines():
        parts = raw.replace(","," ").split()
        if len(parts) == 3 and not raw.startswith("/"):
            coords[parts[0].lower()] = [float(parts[1]),float(parts[2])]
    missing = sorted(b for b in bus_phases if b not in coords)
    # Switch and regulator terminals without coordinates take those of the nearest bus that has them.
    import networkx as nx
    g = nx.Graph()
    g.add_edges_from((e["from_bus"],e["to_bus"]) for e in [*lines,*switches,*banks.values(),transformer])
    for bus in missing:
        nearest = next(n for n in nx.bfs_tree(g,bus) if n in coords)
        coords[bus] = coords[nearest]
    hv = transformer["from_bus"]
    data = dict(name="IEEE 8500", frequency_hz=60, source_bus=hv, source_pu=SOURCE_PU,
                source_sc_mva=round(HV_KV**2/14.8,1),  # Transformers.dss source reactor, x = 14.8 ohm at 115 kV
                linecode_unit_km=1.0, coordinate_unit_m=.3048, linecodes=codes,
                buses=[dict(id=b,phases="".join(sorted(ph)),vn_kv=HV_KV if b==hv else MV_KV,xy=coords.get(b))
                       for b,ph in bus_phases.items()],
                lines=lines, switches=switches, loads=loads, capacitors=caps, regulators=regulators,
                transformer=transformer,
                provenance=dict(url=URL,commit=COMMIT,retrieved="2026-10-09",case="balanced load (Master.dss)",
                                length_unit="km",coordinate_unit="ft",reduction="MV only: service transformers, "
                                "triplex lines and 120/240 V loads lumped as PQ loads on the primary bus/phase",
                                adjustments=[
                                    dict(item="line ampacity",reference="OpenDSS default 400 A (linecodes define none)",
                                         adopted="typical rating of the phase conductor: "+", ".join(f"{k}: {v:.0f} A" for k,v in CONDUCTOR_AMPS.items())
                                                 +"; inline connectors: substation rated current",
                                         reason="400 A under-rates the 397 ACSR trunk (base case overloaded) and over-rates #4/#2 laterals",
                                         linecodes=amps_changed),
                                    dict(item="voltage level",reference=f"source {SOURCE_PU_REFERENCE} pu; regulators vreg 1.054 (feeder) / 1.042 pu (VREG2-4)",
                                         adopted=f"source {SOURCE_PU} pu; all regulators vreg {VREG_PU} pu",
                                         reason="reference case starts above 1.05 pu near the substation; base case S0 must respect ANSI range A")],
                                regulator_taps="nominal-load taps of the emulated RegControl (no published reference); the solver moves them per snapshot",
                                coordinates_copied_from_nearest_bus=missing,
                                sha256={p.name:hashlib.sha256(p.read_bytes().replace(b"\r\n",b"\n")).hexdigest() for p in sorted(SOURCE.iterdir())}))
    TARGET.parent.mkdir(parents=True,exist_ok=True)
    TARGET.write_text(json.dumps(data,indent=1)+"\n",encoding="utf-8")
    data["regulators"] = regulate(TARGET)
    TARGET.write_text(json.dumps(data,indent=1)+"\n",encoding="utf-8")
    (TARGET.parent/"IEEE8500-LICENSE.txt").write_bytes((ROOT/"data/ieee123/source/License.txt").read_bytes())
    print(f"{len(data['buses'])} buses, {len(lines)} lines, {len(switches)} switches, {len(loads)} loads, "
          f"{len(missing)} buses with coordinates copied from the nearest bus")
    print(f"Nominal demand: {sum(l['p_kw'] for l in loads):.0f} kW, {sum(l['q_kvar'] for l in loads):.0f} kvar")
    print("Regulator taps:",{r["id"]:r["taps"] for r in data["regulators"]})


def regulate(path):
    """Taps at nominal load from the runtime RegControl emulation (network.pandapower_solver)."""
    sys.path.insert(0,str(ROOT/"src"))
    from network.feeder_loader import load_feeder, create_pandapower_network
    from network.pandapower_solver import solve_snapshot, POWER_COLUMNS
    network = load_feeder("ieee8500",path)
    regulators = network.equipment["feeder_data"][0]["regulators"]
    net = create_pandapower_network(network)
    solve_snapshot(net,net.asymmetric_load[POWER_COLUMNS].copy())
    trafo = dict(zip(net.trafo.name,net.trafo.tap_pos))
    for r in regulators:
        r["taps"] = [int(trafo[r["id"]])]*len(r["phases"])
    return regulators


if __name__ == "__main__":
    build()
