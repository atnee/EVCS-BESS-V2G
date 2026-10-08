"""IEEE123 data and native pandapower sequence approximation; no DSS engine."""
from pathlib import Path
import json
import numpy as np
from core.schemas import Bus, Line, Network

DATA_PATH = Path(__file__).parent / "data" / "ieee123.json"
FIDELITY = "IEEE123 topology; pandapower sequence approximation; fixed mean regulator taps"
LIMITATIONS = [
    "Three-phase line matrices replaced by averaged self/mutual sequence parameters.",
    "One/two-phase lines represented by uncoupled three-phase lines; unused phases excluded from reporting.",
    "Regulator banks use fixed mean reference taps, without independent phase controls.",
    "Regulator leakage is regularized to 0.05 percent for numerical conditioning.",
    "Unloaded Dd0 transformer represented by a grounded YNyn equivalent.",
    "Line ampacity is an assumed 400 A, not a validated IEEE thermal rating.",
    "No comparison with published IEEE phase voltages has established numerical equivalence.",
]


def read_ieee123_data(path=None):
    path = DATA_PATH if path is None else Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("name") != "IEEE 123" or not data.get("linecodes"):
        raise ValueError("Expected normalized IEEE123 JSON dataset")
    return data


def load_ieee123(path: str | Path | None = None) -> Network:
    data = read_ieee123_data(path)
    buses = [Bus(b["id"], b["phases"], b["vn_kv"]*1000/np.sqrt(3),
                 *(b["xy"] or [None,None])) for b in data["buses"]]
    # Scalar lines are topology metadata only. The electrical backend uses
    # source matrices retained in equipment['ieee123_data'].
    lines = []
    for line in data["lines"]:
        code = data["linecodes"][line["linecode"]]
        scale = line["length_km"]/.3048
        lines.append(Line(line["id"],line["from_bus"],line["to_bus"],line["phases"],
                          float(np.diag(code["rmatrix"]).mean()*scale),
                          float(np.diag(code["xmatrix"]).mean()*scale),400.))
    network = Network("IEEE123 (pandapower approximation)", buses, lines, data["source_bus"],
                      synthetic=False, equipment={"ieee123_data":[data]})
    network.validate()
    return network


def create_pandapower_network(network=None):
    """Return an editable pandapowerNet with original phase metadata attached."""
    import pandapower as pp
    network = load_ieee123() if network is None else network
    if network.synthetic or "ieee123_data" not in network.equipment:
        raise ValueError("Pandapower IEEE123 backend requires load_ieee123()")
    data = network.equipment["ieee123_data"][0]
    net = pp.create_empty_network(name=network.name, f_hz=60, sn_mva=5.)
    lookup = {}
    for b in data["buses"]:
        lookup[b["id"]] = pp.create_bus(net,vn_kv=b["vn_kv"],name=b["id"],
                                        phases=b["phases"],geodata=tuple(b["xy"]) if b["xy"] else None)
    pp.create_ext_grid(net,lookup[data["source_bus"]],vm_pu=1.,s_sc_max_mva=173056.,
                       rx_max=0.001,x0x_max=1.,r0x0_max=0.001)
    for l in data["lines"]:
        code = data["linecodes"][l["linecode"]]
        params = {}
        for key, symbol in (("rmatrix","r"),("xmatrix","x"),("cmatrix","c")):
            m = np.asarray(code[key])/.3048
            self_value = np.diag(m).mean()
            mutual = (m.sum()-np.trace(m))/6 if len(m)==3 else 0.
            if symbol == "c":
                params.update(c_nf_per_km=self_value-mutual,c0_nf_per_km=self_value+2*mutual)
            else:
                params[f"{symbol}_ohm_per_km"] = self_value-mutual
                params[f"{symbol}0_ohm_per_km"] = self_value+2*mutual
        pp.create_line_from_parameters(net,lookup[l["from_bus"]],lookup[l["to_bus"]],
            length_km=l["length_km"],max_i_ka=.4,name=l["id"],phases=l["phases"],**params)
    for s in data["switches"]:
        pp.create_switch(net,lookup[s["from_bus"]],lookup[s["to_bus"]],et="b",
                         closed=s["closed"],type="LS",name=s["id"],phases=s["phases"])
    for r in data["regulators"]:
        pp.create_transformer_from_parameters(net,lookup[r["from_bus"]],lookup[r["to_bus"]],
            sn_mva=r["sn_mva"],vn_hv_kv=4.16,vn_lv_kv=4.16,vkr_percent=.00001,
            vk_percent=.05,pfe_kw=0,i0_percent=0,
            vector_group="YNyn",vk0_percent=.05,vkr0_percent=.00001,
            mag0_percent=100,mag0_rx=0,si0_hv_partial=.5,
            tap_side="lv",tap_neutral=0,tap_min=-16,tap_max=16,tap_step_percent=.625,
            tap_pos=float(np.mean(r["taps"])),tap_changer_type="Ratio",name=r["id"],phases=r["phases"])
    t = data["transformer"]
    pp.create_transformer_from_parameters(net,lookup[t["from_bus"]],lookup[t["to_bus"]],
        sn_mva=t["sn_mva"],vn_hv_kv=t["vn_hv_kv"],vn_lv_kv=t["vn_lv_kv"],
        vkr_percent=t["vkr_percent"],vk_percent=float(np.hypot(t["vkr_percent"],t["x_percent"])),
        pfe_kw=0,i0_percent=0,vector_group="YNyn",vk0_percent=3.,vkr0_percent=1.27,
        mag0_percent=100,mag0_rx=0,si0_hv_partial=.5,name=t["id"],phases="ABC")
    for l in data["loads"]:
        # Delta powers a/b/c denote AB/BC/CA branches.
        phases = l["phases"][:1] if l["connection"]=="delta" else l["phases"]
        values = {f"{pq}_{ph.lower()}_{unit}": l[key]/1000/len(phases)
                  for ph in phases for pq,unit,key in (("p","mw","p_kw"),("q","mvar","q_kvar"))}
        pp.create_asymmetric_load(net,lookup[l["bus"]],name=l["id"],type=l["connection"],
                                  source_model=l["model"],
                                  nominal_kv=l["kv"]/np.sqrt(3) if l["connection"]=="wye" and len(phases)==3 else l["kv"],**values)
    for c in data["capacitors"]:
        values = {f"q_{ph.lower()}_mvar":-c["q_kvar"]/1000/len(c["phases"]) for ph in c["phases"]}
        pp.create_asymmetric_load(net,lookup[c["bus"]],name=c["id"],source_model=2,
                                  nominal_kv=c["kv"]/np.sqrt(3) if len(c["phases"])==3 else c["kv"],type="wye",**values)
    net["ieee123_bus_lookup"] = lookup
    net["ieee123_fidelity"] = FIDELITY
    net["ieee123_limitations"] = LIMITATIONS.copy()
    return net

