"""Feeder topology (NetworkX), line parameters and electrical inventory; no power flow.
Functions take the normalized dataset (network.feeder_loader.read_feeder_data); default IEEE123."""
import numpy as np
import pandas as pd
import networkx as nx
from network.feeder_loader import read_feeder_data, unit_km

LOAD_MODELS = {1: "PQ (constant power)", 2: "Z (constant impedance)", 5: "I (constant current)"}


def _matrix_stats(m, per_km=1/.3048):
    m = np.asarray(m,float)*per_km
    mutual = (m.sum()-np.trace(m))/(len(m)*(len(m)-1)) if len(m) > 1 else 0.
    return float(np.diag(m).mean()),float(mutual)


def sequence_parameters(code, per_km=1/.3048):
    """Per-km sequence values exactly as create_pandapower_network builds them.
    One/two-phase codes are uncoupled there, so their mutual terms are dropped.
    per_km: 1 / length unit of the matrices in km (IEEE123: per 1000 ft)."""
    out = {}
    for key,symbol in (("rmatrix","r"),("xmatrix","x"),("cmatrix","c")):
        self_value,mutual = _matrix_stats(code[key],per_km)
        mutual = mutual if len(code[key]) == 3 else 0.
        out[f"{symbol}1"] = self_value-mutual
        out[f"{symbol}0"] = self_value+2*mutual
    return out


def linecode_table(data=None):
    """One row per IEEE linecode: raw self/mutual terms, inductance, capacitance and sequence values."""
    data = read_feeder_data() if data is None else data
    omega = 2*np.pi*data["frequency_hz"]
    per_km = 1/unit_km(data)
    usage = pd.DataFrame(data["lines"]).groupby("linecode").length_km.agg(["count","sum"])
    rows = []
    for name,code in data["linecodes"].items():
        r_self,r_mut = _matrix_stats(code["rmatrix"],per_km)
        x_self,x_mut = _matrix_stats(code["xmatrix"],per_km)
        c_self,c_mut = _matrix_stats(code["cmatrix"],per_km)
        seq = sequence_parameters(code,per_km)
        rows.append(dict(linecode=name,phases=len(code["rmatrix"]),
            lines=int(usage["count"].get(name,0)),total_km=float(usage["sum"].get(name,0.)),
            r_self_ohm_km=r_self,r_mutual_ohm_km=r_mut,x_self_ohm_km=x_self,x_mutual_ohm_km=x_mut,
            l_self_mh_km=x_self/omega*1e3,l_mutual_mh_km=x_mut/omega*1e3,
            c_self_nf_km=c_self,c_mutual_nf_km=c_mut,
            r1_ohm_km=seq["r1"],x1_ohm_km=seq["x1"],r0_ohm_km=seq["r0"],x0_ohm_km=seq["x0"],
            l1_mh_km=seq["x1"]/omega*1e3,l0_mh_km=seq["x0"]/omega*1e3,c1_nf_km=seq["c1"],c0_nf_km=seq["c0"],
            x1_over_r1=seq["x1"]/seq["r1"]))
    return pd.DataFrame(rows)


def line_table(data=None):
    """One row per line: length, linecode and total positive/zero-sequence impedance and inductance."""
    data = read_feeder_data() if data is None else data
    omega = 2*np.pi*data["frequency_hz"]
    per_km = 1/unit_km(data)
    rows = []
    for line in data["lines"]:
        seq = sequence_parameters(data["linecodes"][line["linecode"]],per_km)
        km = line["length_km"]
        rows.append(dict(line=line["id"],from_bus=line["from_bus"],to_bus=line["to_bus"],phases=line["phases"],
            linecode=line["linecode"],length_km=km,r1_ohm_km=seq["r1"],x1_ohm_km=seq["x1"],
            r0_ohm_km=seq["r0"],x0_ohm_km=seq["x0"],c1_nf_km=seq["c1"],
            r1_ohm=seq["r1"]*km,x1_ohm=seq["x1"]*km,z1_ohm=float(np.hypot(seq["r1"],seq["x1"])*km),
            l1_mh=seq["x1"]*km/omega*1e3,c1_nf=seq["c1"]*km))
    return pd.DataFrame(rows)


def feeder_graph(data=None):
    """Undirected NetworkX graph of every bus and two-terminal element, open switches included.
    Edge attribute `kind`: line, switch, regulator or transformer; `closed` marks energized edges."""
    data = read_feeder_data() if data is None else data
    loads = pd.DataFrame(data["loads"]).groupby("bus")[["p_kw","q_kvar"]].sum()
    caps = pd.DataFrame(data["capacitors"]).groupby("bus").q_kvar.sum()
    g = nx.Graph(name=data["name"],source=data["source_bus"],frequency_hz=data["frequency_hz"])
    for b in data["buses"]:
        g.add_node(b["id"],phases=b["phases"],vn_kv=b["vn_kv"],xy=tuple(b["xy"]) if b["xy"] else (np.nan,np.nan),
                   load_kw=float(loads.p_kw.get(b["id"],0.)),load_kvar=float(loads.q_kvar.get(b["id"],0.)),
                   capacitor_kvar=float(caps.get(b["id"],0.)))
    lines = line_table(data).set_index("line")
    for l in data["lines"]:
        g.add_edge(l["from_bus"],l["to_bus"],kind="line",id=l["id"],phases=l["phases"],closed=True,
                   length_km=l["length_km"],linecode=l["linecode"],z1_ohm=lines.at[l["id"],"z1_ohm"])
    for s in data["switches"]:
        g.add_edge(s["from_bus"],s["to_bus"],kind="switch",id=s["id"],phases=s["phases"],closed=s["closed"],length_km=0.)
    for r in data["regulators"]:
        g.add_edge(r["from_bus"],r["to_bus"],kind="regulator",id=r["id"],phases=r["phases"],closed=True,
                   length_km=0.,taps=r["taps"],ratio=[1+.00625*t for t in r["taps"]],lv_bus=r["to_bus"])
    t = data["transformer"]
    g.add_edge(t["from_bus"],t["to_bus"],kind="transformer",id=t["id"],phases="ABC",closed=True,length_km=0.,
               kv=f'{t["vn_hv_kv"]}/{t["vn_lv_kv"]}',sn_kva=t["sn_mva"]*1e3)
    return g


def energized(graph):
    """Subgraph without open switches: the radial feeder actually solved."""
    return graph.edge_subgraph([(u,v) for u,v,d in graph.edges(data=True) if d["closed"]]).copy()


def bus_table(graph):
    """Distance (km along the feeder) and hop count from the source, plus parent bus."""
    radial = energized(graph)
    source = graph.graph["source"]
    km = nx.single_source_dijkstra_path_length(radial,source,weight="length_km")
    hops = nx.single_source_shortest_path_length(radial,source)
    parent = {child: p for p,child in nx.bfs_edges(radial,source)}
    return pd.DataFrame([dict(bus=n,phases=d["phases"],vn_kv=d["vn_kv"],x=d["xy"][0],y=d["xy"][1],
                              distance_km=km[n],hops=hops[n],parent=parent.get(n),degree=radial.degree(n),
                              load_kw=d["load_kw"],load_kvar=d["load_kvar"],capacitor_kvar=d["capacitor_kvar"])
                         for n,d in graph.nodes(data=True)])


def graph_metrics(graph):
    radial = energized(graph)
    source = graph.graph["source"]
    km = nx.single_source_dijkstra_path_length(radial,source,weight="length_km")
    farthest = max(km,key=km.get)
    kinds = pd.Series([d["kind"] for *_,d in graph.edges(data=True)]).value_counts()
    return {"buses": graph.number_of_nodes(), "edges": graph.number_of_edges(),
            **{f"{k}_count": int(v) for k,v in kinds.items()},
            "open_switches": sum(1 for *_,d in graph.edges(data=True) if d["kind"]=="switch" and not d["closed"]),
            "energized_is_tree": nx.is_tree(radial),
            "loops_if_open_switches_closed": len(nx.cycle_basis(graph)),
            "end_buses": sum(1 for n in radial if radial.degree(n)==1 and n != source),
            "max_hops": max(nx.single_source_shortest_path_length(radial,source).values()),
            "total_line_km": float(sum(d["length_km"] for *_,d in graph.edges(data=True) if d["kind"]=="line")),
            "farthest_bus": farthest, "farthest_km": float(km[farthest]),
            "main_path": nx.shortest_path(radial,source,farthest)}


def electrical_inventory(data=None):
    """Tables describing the electrical configuration: sources, regulators, transformer, capacitors, loads."""
    data = read_feeder_data() if data is None else data
    loads = pd.DataFrame(data["loads"])
    loads["model_name"] = loads.model.map(LOAD_MODELS)
    load_summary = loads.groupby(["connection","model_name"]).agg(count=("id","size"),p_kw=("p_kw","sum"),
                                                                  q_kvar=("q_kvar","sum")).reset_index()
    # Wye loads are named by phase; delta letters denote AB/BC/CA branches.
    loads["phase_or_branch"] = np.where(loads.connection=="delta",
        loads.phases.str[:1].map({"A":"AB","B":"BC","C":"CA"}),loads.phases)
    by_phase = loads.groupby(["connection","phase_or_branch"])[["p_kw","q_kvar"]].sum().reset_index()
    regulators = pd.DataFrame([dict(id=r["id"],from_bus=r["from_bus"],to_bus=r["to_bus"],phases=r["phases"],
        taps=r["taps"],ratio_pu=[round(1+.00625*t,5) for t in r["taps"]],
        mean_tap_used=float(np.mean(r["taps"])),sn_kva=r["sn_mva"]*1e3) for r in data["regulators"]])
    t = data["transformer"]
    transformer = pd.DataFrame([dict(id=t["id"],from_bus=t["from_bus"],to_bus=t["to_bus"],
        kv=f'{t["vn_hv_kv"]}/{t["vn_lv_kv"]}',sn_kva=t["sn_mva"]*1e3,connection=t["connection"],
        r_percent=t["vkr_percent"],x_percent=t["x_percent"],model_in_pandapower="YNyn equivalent")])
    return {"loads_by_model": load_summary, "loads_by_phase": by_phase, "regulators": regulators,
            "transformer": transformer, "capacitors": pd.DataFrame(data["capacitors"]),
            "switches": pd.DataFrame(data["switches"]),
            "totals": pd.Series({"frequency_hz": data["frequency_hz"], "source_bus": data["source_bus"],
                                 "nominal_kv": float(pd.Series([b["vn_kv"] for b in data["buses"]]).mode()[0]), "load_kw": loads.p_kw.sum(), "load_kvar": loads.q_kvar.sum(),
                                 "capacitor_kvar": sum(c["q_kvar"] for c in data["capacitors"])})}
