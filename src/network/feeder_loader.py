"""IEEE test feeders (normalized JSON) and native pandapower sequence approximation; no DSS engine.

Available feeders: FEEDERS keys ('ieee123', 'ieee8500'). Datasets are built by scripts/build_<name>_data.py."""
from pathlib import Path
import json
import numpy as np
from core.schemas import Bus, Line, Network

DATA_DIR = Path(__file__).parent / "data"
COMMON_LIMITATIONS = [
    "Three-phase line matrices replaced by averaged self/mutual sequence parameters.",
    "One/two-phase lines represented by uncoupled three-phase lines; unused phases excluded from reporting.",
    "Regulator banks use fixed mean reference taps, without independent phase controls.",
    "No comparison with published IEEE phase voltages has established numerical equivalence.",
]
FEEDERS = {
    "ieee123": dict(name="IEEE 123", file="ieee123.json",
        fidelity="IEEE123 topology; pandapower sequence approximation; fixed mean regulator taps",
        limitations=COMMON_LIMITATIONS[:3]+[
            "Regulator leakage is regularized to 0.05 percent for numerical conditioning.",
            "Unloaded Dd0 transformer represented by a grounded YNyn equivalent.",
            "Line ampacity is an assumed 400 A, not a validated IEEE thermal rating.",
        ]+COMMON_LIMITATIONS[3:]),
    "ieee8500": dict(name="IEEE 8500", file="ieee8500.json",
        fidelity="IEEE8500 medium-voltage reduction; pandapower sequence approximation; fixed mean regulator taps",
        limitations=COMMON_LIMITATIONS[:3]+[
            "Service transformers, triplex secondaries and 120/240 V loads lumped as PQ loads on the primary bus.",
            "Regulator taps follow an emulated RegControl (vreg, band; ganged banks, no time delays).",
            "Capacitor banks always in service (CapControls not modelled).",
            "Delta-wye substation transformer represented by a grounded YNyn equivalent.",
            "STUDY ADJUSTMENT: line ampacity = typical rating of the phase conductor (not IEEE data).",
            "STUDY ADJUSTMENT: source 1.0 pu and regulator vreg 1.03 pu (reference 1.05 / 1.054 / 1.042 pu).",
        ]+COMMON_LIMITATIONS[3:]),
}


def feeder_info(feeder="ieee123"):
    if feeder not in FEEDERS:
        raise ValueError(f"Unknown feeder {feeder!r}; available: {sorted(FEEDERS)}")
    return FEEDERS[feeder]


def read_feeder_data(feeder="ieee123", path=None):
    info = feeder_info(feeder)
    path = DATA_DIR/info["file"] if path is None else Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("name") != info["name"] or not data.get("linecodes"):
        raise ValueError(f"Expected normalized {info['name']} JSON dataset")
    return data


def _feeder_key(data):
    return next(k for k,v in FEEDERS.items() if v["name"] == data["name"])


def unit_km(data):
    """Length unit of the linecode matrices, in km (IEEE123: per 1000 ft)."""
    return data.get("linecode_unit_km",.3048)


def load_feeder(feeder="ieee123", path: str | Path | None = None) -> Network:
    data = read_feeder_data(feeder,path)
    buses = [Bus(b["id"], b["phases"], b["vn_kv"]*1000/np.sqrt(3),
                 *(b["xy"] or [None,None])) for b in data["buses"]]
    # Scalar lines are topology metadata only. The electrical backend uses
    # source matrices retained in equipment['feeder_data'].
    lines = []
    for line in data["lines"]:
        code = data["linecodes"][line["linecode"]]
        scale = line["length_km"]/unit_km(data)
        lines.append(Line(line["id"],line["from_bus"],line["to_bus"],line["phases"],
                          float(np.diag(code["rmatrix"]).mean()*scale),
                          float(np.diag(code["xmatrix"]).mean()*scale),code.get("ampacity_a",400.)))
    network = Network(f"{data['name'].replace(' ','')} (pandapower approximation)", buses, lines, data["source_bus"],
                      synthetic=False, equipment={"feeder_data":[data]})
    network.validate()
    return network


def create_pandapower_network(network=None):
    """Return an editable pandapowerNet with original phase metadata attached."""
    import pandapower as pp
    network = load_feeder() if network is None else network
    if network.synthetic or "feeder_data" not in network.equipment:
        raise ValueError("Pandapower backend requires load_feeder()")
    data = network.equipment["feeder_data"][0]
    info = FEEDERS[_feeder_key(data)]
    net = pp.create_empty_network(name=network.name, f_hz=data["frequency_hz"], sn_mva=5.)
    # Vectorized creation: element-by-element calls take ~30 s on the IEEE 8500 feeder.
    buses = data["buses"]
    index = pp.create_buses(net,len(buses),vn_kv=[b["vn_kv"] for b in buses],name=[b["id"] for b in buses],
                            phases=[b["phases"] for b in buses])
    lookup = dict(zip((b["id"] for b in buses),(int(i) for i in index)))
    for b,i in zip(buses,index):
        if b["xy"]:
            net.bus.at[i,"geo"] = json.dumps({"type":"Point","coordinates":list(map(float,b["xy"]))})
    pp.create_ext_grid(net,lookup[data["source_bus"]],vm_pu=data.get("source_pu",1.),
                       s_sc_max_mva=data.get("source_sc_mva",173056.),rx_max=0.001,x0x_max=1.,r0x0_max=0.001)
    per_km = 1/unit_km(data)
    rows = []
    for l in data["lines"]:
        code = data["linecodes"][l["linecode"]]
        params = {}
        for key, symbol in (("rmatrix","r"),("xmatrix","x"),("cmatrix","c")):
            m = np.asarray(code[key])*per_km
            self_value = np.diag(m).mean()
            mutual = (m.sum()-np.trace(m))/6 if len(m)==3 else 0.
            if symbol == "c":
                params.update(c_nf_per_km=self_value-mutual,c0_nf_per_km=self_value+2*mutual)
            else:
                params[f"{symbol}_ohm_per_km"] = self_value-mutual
                params[f"{symbol}0_ohm_per_km"] = self_value+2*mutual
        rows.append(dict(from_buses=lookup[l["from_bus"]],to_buses=lookup[l["to_bus"]],length_km=l["length_km"],
                         max_i_ka=code.get("ampacity_a",400.)/1000,name=l["id"],phases=l["phases"],**params))
    if rows:
        pp.create_lines_from_parameters(net,**{k:[r[k] for r in rows] for k in rows[0]})
    if data["switches"]:
        s = data["switches"]
        pp.create_switches(net,[lookup[x["from_bus"]] for x in s],[lookup[x["to_bus"]] for x in s],et="b",
                           closed=[x["closed"] for x in s],type="LS",name=[x["id"] for x in s],phases=[x["phases"] for x in s])
    for r in data["regulators"]:
        kv, vk = r.get("vn_kv",4.16), r.get("vk_percent",.05)
        pp.create_transformer_from_parameters(net,lookup[r["from_bus"]],lookup[r["to_bus"]],
            sn_mva=r["sn_mva"],vn_hv_kv=kv,vn_lv_kv=kv,vkr_percent=.00001,
            vk_percent=vk,pfe_kw=0,i0_percent=0,
            vector_group="YNyn",vk0_percent=vk,vkr0_percent=.00001,
            mag0_percent=100,mag0_rx=0,si0_hv_partial=.5,
            tap_side="lv",tap_neutral=0,tap_min=-16,tap_max=16,tap_step_percent=.625,
            tap_pos=float(np.mean(r["taps"])),tap_changer_type="Ratio",name=r["id"],phases=r["phases"])
    t = data["transformer"]
    vk = float(np.hypot(t["vkr_percent"],t["x_percent"]))
    pp.create_transformer_from_parameters(net,lookup[t["from_bus"]],lookup[t["to_bus"]],
        sn_mva=t["sn_mva"],vn_hv_kv=t["vn_hv_kv"],vn_lv_kv=t["vn_lv_kv"],
        vkr_percent=t["vkr_percent"],vk_percent=vk,
        pfe_kw=0,i0_percent=0,vector_group="YNyn",vk0_percent=t.get("vk0_percent",3.),
        vkr0_percent=t.get("vkr0_percent",1.27),
        mag0_percent=100,mag0_rx=0,si0_hv_partial=.5,name=t["id"],phases="ABC")
    rows = []
    for l in data["loads"]:
        # Delta powers a/b/c denote AB/BC/CA branches.
        phases = l["phases"][:1] if l["connection"]=="delta" else l["phases"]
        values = {f"{pq}_{ph.lower()}_{unit}": l[key]/1000/len(phases)
                  for ph in phases for pq,unit,key in (("p","mw","p_kw"),("q","mvar","q_kvar"))}
        rows.append(dict(bus=lookup[l["bus"]],name=l["id"],type=l["connection"],source_model=l["model"],
                         nominal_kv=l["kv"]/np.sqrt(3) if l["connection"]=="wye" and len(phases)==3 else l["kv"],**values))
    for c in data["capacitors"]:
        values = {f"q_{ph.lower()}_mvar":-c["q_kvar"]/1000/len(c["phases"]) for ph in c["phases"]}
        rows.append(dict(bus=lookup[c["bus"]],name=c["id"],source_model=2,
                         nominal_kv=c["kv"]/np.sqrt(3) if len(c["phases"])==3 else c["kv"],type="wye",**values))
    _append_asymmetric_loads(net,rows)
    # Regulator control (vreg/band) only where the dataset defines it; data order is upstream first.
    trafo = dict(zip(net.trafo.name,net.trafo.index))
    net["regulator_control"] = [dict(name=r["id"],trafo=int(trafo[r["id"]]),lv_bus=lookup[r["to_bus"]],phases=r["phases"],
                                     vreg_pu=r["vreg_pu"],band_pu=r["band_pu"])
                                for r in data["regulators"] if "vreg_pu" in r]
    net["bus_lookup"] = lookup
    net["fidelity"] = info["fidelity"]
    net["limitations"] = info["limitations"].copy()
    return net


def _append_asymmetric_loads(net, rows):
    """Same table as one pp.create_asymmetric_load call per row, built in one step."""
    import pandas as pd
    import pandapower as pp
    if not rows:
        return
    template = pp.create_asymmetric_load(net,rows[0]["bus"],name="template")
    defaults = net.asymmetric_load.loc[template].to_dict()
    net.asymmetric_load.drop(index=template,inplace=True)
    table = pd.DataFrame([{**defaults,**r} for r in rows],index=range(template,template+len(rows)))
    for column in table:
        if column in net.asymmetric_load and len(net.asymmetric_load):
            table[column] = table[column].astype(net.asymmetric_load[column].dtype)
    net.asymmetric_load = pd.concat([net.asymmetric_load,table]) if len(net.asymmetric_load) else table
