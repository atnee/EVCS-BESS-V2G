"""Normalize the pinned IEEE123 text dataset without importing any DSS engine.

This is a dataset-specific extractor, not a general DSS converter. Runtime only
reads the resulting JSON. Run from any directory after obtaining source files.
"""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/ieee123/source"
COMMIT = "5005c668a72d20775f4c2d060feebb2866ba1d38"


def records(filename):
    result = []
    for raw in (SOURCE / filename).read_text().splitlines():
        line = raw.split("!")[0].strip().lower()
        if line.startswith("new "):
            result.append(line)
        elif line.startswith("~"):
            result[-1] += " " + line[1:]
    return result


def properties(line):
    return dict(re.findall(r"([\w%]+)\s*=\s*(\[[^\]]*\]|\([^)]*\)|[^\s]+)", line))


def terminal(value, n=3):
    parts = value.split(".")
    return parts[0], "".join("ABC"[int(x)-1] for x in parts[1:]) if len(parts)>1 else "ABC"[:n]


def matrix(value):
    rows = [[float(x) for x in r.split()] for r in value[1:-1].split("|")]
    return [[rows[max(i,j)][min(i,j)] for j in range(len(rows))] for i in range(len(rows))]


def build():
    codes = {}
    for rec in records("IEEELineCodes.DSS"):
        name = rec.split()[1].split(".")[1]
        if name not in {str(i) for i in range(1,13)}:
            continue
        p = properties(rec)
        codes[name] = {key: matrix(p[key]) for key in ("rmatrix", "xmatrix", "cmatrix")}
    lines, switches, loads, caps = [], [], [], []
    bus_phases = {}
    def register(bus, phases):
        bus_phases.setdefault(bus, set()).update(phases)
    for rec in records("IEEE123Master.dss"):
        name = rec.split()[1]
        p = properties(rec)
        if name.startswith("line."):
            b1, ph = terminal(p["bus1"], int(p.get("phases",3)))
            b2, ph2 = terminal(p["bus2"], int(p.get("phases",3)))
            assert ph == ph2
            if "linecode" in p:
                register(b1,ph); register(b2,ph)
                lines.append(dict(id=name[5:], from_bus=b1, to_bus=b2, phases=ph,
                                  length_km=float(p["length"])*0.3048, linecode=p["linecode"]))
            else:
                # The two open-ended stubs denote ties to real buses 300 and 94.
                closed = not b2.endswith("_open")
                b2 = b2.removesuffix("_open")
                register(b1,ph); register(b2,ph)
                switches.append(dict(id=name[5:], from_bus=b1, to_bus=b2, phases=ph, closed=closed))
        elif name.startswith("capacitor."):
            b, ph = terminal(p["bus1"], int(p["phases"]))
            register(b,ph)
            caps.append(dict(id=name[10:], bus=b, phases=ph, q_kvar=float(p["kvar"]), kv=float(p["kv"])))
    for rec in records("IEEE123Loads.DSS"):
        p = properties(rec)
        b, ph = terminal(p["bus1"], int(p["phases"]))
        register(b,ph)
        loads.append(dict(id=rec.split()[1][5:], bus=b, phases=ph, connection=p["conn"],
                          model=int(p["model"]), kv=float(p["kv"]), p_kw=float(p["kw"]), q_kvar=float(p["kvar"])))
    regulators = [dict(id="reg1",from_bus="150",to_bus="150r",phases="ABC",taps=[7,7,7],sn_mva=5.),
                  dict(id="reg2",from_bus="9",to_bus="9r",phases="A",taps=[-1],sn_mva=2.),
                  dict(id="reg3",from_bus="25",to_bus="25r",phases="AC",taps=[0,-1],sn_mva=4.),
                  dict(id="reg4",from_bus="160",to_bus="160r",phases="ABC",taps=[8,1,5],sn_mva=6.)]
    for r in regulators:
        register(r["from_bus"], r["phases"]); register(r["to_bus"],r["phases"])
    register("610","ABC")
    coords = {r[0].lower():[float(r[1]),float(r[2])] for line in (SOURCE/"BusCoords.dat").read_text().splitlines() if len(r:=line.split())==3}
    data = dict(name="IEEE 123", frequency_hz=60, source_bus="150", linecodes=codes,
                buses=[dict(id=b, phases="".join(sorted(ph)), vn_kv=.48 if b=="610" else 4.16,
                            xy=coords.get(b)) for b,ph in bus_phases.items()],
                lines=lines, switches=switches, loads=loads, capacitors=caps, regulators=regulators,
                transformer=dict(id="xfm1",from_bus="61s",to_bus="610",sn_mva=.15,vn_hv_kv=4.16,
                                 vn_lv_kv=.48,connection="Dd0",vkr_percent=1.27,x_percent=2.72),
                provenance=dict(url="https://github.com/tshort/OpenDSS/tree/"+COMMIT+"/Distrib/IEEETestCases/123Bus",
                                commit=COMMIT, retrieved="2026-10-08", length_unit="1000 ft = 0.3048 km",
                                sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(SOURCE.iterdir())}))
    target=ROOT/"src/network/data/ieee123.json"
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(data,indent=2)+"\n", encoding="utf-8")
    (target.parent/"IEEE123-LICENSE.txt").write_bytes((SOURCE/"License.txt").read_bytes())
    print(f"{len(data['buses'])} buses (including equipment terminals), {len(lines)} lines, {len(loads)} loads")
    print(f"Nominal demand: {sum(l['p_kw'] for l in loads)} kW, {sum(l['q_kvar'] for l in loads)} kvar")


if __name__ == "__main__":
    build()
