"""Land-use zoning of the feeder: residential, commercial and industrial customers and regions (maps only).

python -m integration.zoning                  # -> results/<feeder>/zoneamento/ (IEEE 8500)
The IEEE 8500 does not classify its loads: every customer is a single-phase 120/240 V load behind its own
center-tapped service transformer. The classes are a study hypothesis (configs/<feeder>.yaml, `zoning:`):
- customer: service transformer up to residential_max_kva -> residential, larger -> commercial; a bus fed on
  all three phases by transformers of at least industrial_min_kva_per_phase -> industrial;
- region: square cells of cell_m; industrial within industrial_radius_m of an industrial customer, commercial
  where commercial customers hold at least commercial_share of the cell's load, residential otherwise.
No power flow: the load model and the daily curve are unchanged (class load curves and tariffs come later).
"""
from pathlib import Path
import argparse
import re
import numpy as np
import pandas as pd
from core.schemas import read_parameters
from network.feeder_loader import read_feeder_data
from network.topology import feeder_graph, bus_table
from integration.scenarios import study_feeder
from integration.s0_study import _style, _edges, _map_frame, new_axes, save_figures, INK, MUTED, SURFACE

FT_TO_M = .3048  # IEEE 8500 bus coordinates are in feet
CLASSES = ("residential", "commercial", "industrial")
ZONE_COLORS = {"residential": "#f2c94c", "commercial": "#e0565b", "industrial": "#6f6aa8"}
ZONE_PT = {"residential": "residencial", "commercial": "comercial", "industrial": "industrial"}
SOURCE = Path(__file__).resolve().parents[2]/"data"


def service_transformers(feeder):
    """kVA of the service transformer behind each load (load id -> kVA), from the OpenDSS source files.
    Each IEEE 8500 load hangs from secondary bus SX<n>, fed by the transformer whose secondary is X<n>."""
    folder = SOURCE/feeder/"source"
    if not (folder/"LoadXfmrs.dss").exists():
        raise ValueError(f"No service transformer data for {feeder}: zoning needs the IEEE 8500 source files")
    lines = lambda name: [l for l in (folder/name).read_text(encoding="utf-8",errors="replace").splitlines()
                          if l.lower().startswith("new ")]
    secondary = {re.search(r"wdg=2 bus=(\S+?)\.",l,re.I).group(1).upper(): float(re.search(r"kVA=([\d.]+)",l,re.I).group(1))
                 for l in lines("LoadXfmrs.dss")}
    return {re.search(r"New Load\.(\S+)",l,re.I).group(1).lower(): secondary[re.search(r"Bus1=S(\S+?)\.",l,re.I).group(1).upper()]
            for l in lines("Loads.dss")}


def classify_customers(customers, settings):
    """customers: DataFrame with bus, phases, kva, p_kw. Adds `customer_class`."""
    c = customers.copy()
    c["customer_class"] = np.where(c.kva <= settings["residential_max_kva"],"residential","commercial")
    big = c[c.kva >= settings["industrial_min_kva_per_phase"]]
    three_phase = big.groupby("bus").phases.agg(lambda p: set("".join(p)) >= set("ABC"))
    industrial = c.bus.isin(three_phase.index[three_phase]) & (c.kva >= settings["industrial_min_kva_per_phase"])
    c.loc[industrial,"customer_class"] = "industrial"
    return c


def zone_cells(customers, settings, origin):
    """Square cells holding customers, with their load per class and region type.
    customers: x, y (feet), p_kw, customer_class. origin: (x, y) of the grid corner (feet)."""
    size = settings["cell_m"]/FT_TO_M
    c = customers.assign(cx=((customers.x-origin[0])//size).astype(int),cy=((customers.y-origin[1])//size).astype(int))
    kw = c.pivot_table(index=["cx","cy"],columns="customer_class",values="p_kw",aggfunc="sum",fill_value=0.)
    kw = kw.reindex(columns=list(CLASSES),fill_value=0.).add_suffix("_kw")
    cells = kw.join(c.groupby(["cx","cy"]).size().rename("customers")).reset_index()
    cells["x0"], cells["y0"] = origin[0]+cells.cx*size, origin[1]+cells.cy*size
    total = cells[[f"{k}_kw" for k in CLASSES]].sum(axis=1)
    cells["commercial_share"] = (cells.commercial_kw+cells.industrial_kw)/total
    centre = np.c_[cells.x0+size/2,cells.y0+size/2]
    plants = c.loc[c.customer_class=="industrial",["x","y"]].drop_duplicates().to_numpy()
    near = (np.hypot(centre[:,None,0]-plants[None,:,0],centre[:,None,1]-plants[None,:,1])*FT_TO_M
            <= settings["industrial_radius_m"]).any(axis=1) if len(plants) else np.zeros(len(cells),bool)
    cells["zone"] = np.where(near | (cells.industrial_kw > 0),"industrial",
                             np.where(cells.commercial_share >= settings["commercial_share"],"commercial","residential"))
    return cells


def build_zoning(config_dir="configs", feeder=None):
    feeder = feeder or study_feeder(config_dir)
    settings = read_parameters(Path(config_dir)/f"{feeder}.yaml")["zoning"]
    data = read_feeder_data(feeder)
    graph = feeder_graph(data); buses = bus_table(graph)
    loads = pd.DataFrame(data["loads"])
    loads["kva"] = loads.id.str.lower().map(service_transformers(feeder))
    customers = classify_customers(loads.merge(buses[["bus","x","y","distance_km"]],on="bus"),settings)
    cells = zone_cells(customers,settings,(buses.x.min(),buses.y.min()))
    size = settings["cell_m"]/FT_TO_M
    key = lambda t: list(zip(((t.x-buses.x.min())//size).astype(int),((t.y-buses.y.min())//size).astype(int)))
    zone = dict(zip(zip(cells.cx,cells.cy),cells.zone))
    customers["zone"] = [zone[k] for k in key(customers)]
    buses = buses.assign(zone=[zone.get(k) for k in key(buses)])
    substation = buses.loc[buses.distance_km.idxmin()]
    return dict(feeder=feeder,settings=settings,graph=graph,buses=buses,customers=customers,cells=cells,
                substation=substation)


# ------------------------------------------------------------------------------------------- figures
def _zone_patches(ax, cells, size, alpha=.45):
    from matplotlib.patches import Rectangle
    for row in cells.itertuples():
        ax.add_patch(Rectangle((row.x0,row.y0),size,size,facecolor=ZONE_COLORS[row.zone],alpha=alpha,
                               edgecolor=SURFACE,linewidth=.6,zorder=0))


def _feeder(ax, z):
    pos = {n: d["xy"] for n,d in z["graph"].nodes(data=True)}
    _edges(ax,z["graph"],pos,emphasis=False)
    s = z["substation"]
    ax.scatter(s.x,s.y,marker="s",s=140,color=INK,edgecolor=SURFACE,linewidth=1.5,zorder=7)
    ax.annotate("substation",(s.x,s.y),xytext=(8,-14),textcoords="offset points",fontsize=9,color=INK)
    plant = z["customers"][z["customers"].customer_class=="industrial"].drop_duplicates("bus")
    for row in plant.itertuples():
        ax.scatter(row.x,row.y,marker="*",s=320,color=ZONE_COLORS["industrial"],edgecolor=INK,linewidth=.8,zorder=7)
        ax.annotate(f"industrial customer {row.bus}",(row.x,row.y),xytext=(9,8),textcoords="offset points",fontsize=9,
                    color=INK,bbox=dict(boxstyle="round,pad=.2",fc=SURFACE,ec="none",alpha=.85))


def _frame(ax, z):
    b = z["buses"]; pad = .04*max(b.x.max()-b.x.min(),b.y.max()-b.y.min())
    ax.set_xlim(b.x.min()-pad,b.x.max()+pad); ax.set_ylim(b.y.min()-pad,b.y.max()+pad)
    _map_frame(ax)


def _zone_legend(ax, cells, cell_m, extra=()):
    from matplotlib.patches import Patch
    km2 = (cells.groupby("zone").size()*(cell_m/1000)**2).to_dict()
    handles = [Patch(facecolor=ZONE_COLORS[k],alpha=.6,label=f"{k} region ({km2.get(k,0):g} km²)") for k in CLASSES]
    ax.legend(handles=[*handles,*extra],loc="upper right",frameon=False,fontsize=9)


def plot_zones(z, ax=None):
    """Regions (1 km cells) over the feeder."""
    ax = ax or new_axes((11,9.5))
    s = z["settings"]
    _style(ax,f"Land-use regions (study hypothesis): {s['cell_m']:.0f} m cells, commercial if commercial customers "
              f"hold ≥ {s['commercial_share']:.0%} of the load")
    _zone_patches(ax,z["cells"],s["cell_m"]/FT_TO_M)
    _feeder(ax,z); _zone_legend(ax,z["cells"],s["cell_m"]); _frame(ax,z)
    return ax


def plot_customer_classes(z, ax=None):
    """Each customer coloured by class, sized by its load."""
    from matplotlib.lines import Line2D
    ax = ax or new_axes((11,9.5))
    s = z["settings"]
    _style(ax,f"Customers by service transformer: ≤ {s['residential_max_kva']:g} kVA residential, larger commercial, "
              f"three-phase ≥ {s['industrial_min_kva_per_phase']:g} kVA/phase industrial")
    _feeder(ax,z)
    c = z["customers"]
    for k in CLASSES:
        part = c[c.customer_class==k]
        ax.scatter(part.x,part.y,s=4+part.p_kw*2.2,color=ZONE_COLORS[k],edgecolor=INK if k != "residential" else "none",
                   linewidth=.3,alpha=.85,zorder=5 if k != "residential" else 4)
    counts = c.groupby("customer_class").p_kw.agg(["size","sum"])
    ax.legend(handles=[Line2D([],[],marker="o",ls="",color=ZONE_COLORS[k],ms=8,
                              label=f"{k}: {int(counts.loc[k,'size']) if k in counts.index else 0} customers, "
                                    f"{counts.loc[k,'sum'] if k in counts.index else 0:,.0f} kW") for k in CLASSES],
              loc="upper right",frameon=False,fontsize=9)
    _frame(ax,z)
    return ax


def plot_zones_with_stations(z, sites, params, evs, ax=None):
    """Regions with the charging stations of one fleet on top."""
    from matplotlib.lines import Line2D
    from integration.evcs_screening import TYPE_STYLE
    ax = ax or new_axes((11,9.5))
    _style(ax,f"Charging stations for {evs:,} EVs over the land-use regions")
    _zone_patches(ax,z["cells"],z["settings"]["cell_m"]/FT_TO_M,alpha=.35)
    _feeder(ax,z)
    pos = z["buses"].set_index("bus")[["x","y"]]
    for bus,key in sites.items():
        st = TYPE_STYLE[key]
        ax.scatter(*pos.loc[bus],marker=st["marker"],s={"dc":260,"ac":170}.get(key,70),color=st["color"],
                   edgecolor=SURFACE,linewidth=1.2,zorder=8)
    stations = [Line2D([],[],marker=st["marker"],ls="",color=st["color"],ms=10,
                       label=f"{st['label']} ({sum(v==k for v in sites.values())})")
                for k,st in TYPE_STYLE.items() if k in params.types]
    _zone_legend(ax,z["cells"],z["settings"]["cell_m"],stations); _frame(ax,z)
    return ax


def plot_load_by_zone(z, ax=None):
    """Load of each region split by customer class (kW)."""
    ax = ax or new_axes((9,5))
    _style(ax,"Existing load per region, by customer class")
    cells = z["cells"]
    y = np.arange(len(CLASSES)); left = np.zeros(len(CLASSES))
    for k in CLASSES:
        kw = np.array([cells.loc[cells.zone==zone,f"{k}_kw"].sum() for zone in CLASSES])
        ax.barh(y,kw,.6,left=left,color=ZONE_COLORS[k],label=f"{k} customers",edgecolor=SURFACE)
        left += kw
    total = left.sum()
    for i,v in enumerate(left):
        ax.annotate(f"{v:,.0f} kW ({v/total:.0%})",(v,i),xytext=(6,0),textcoords="offset points",va="center",fontsize=9,color=INK)
    ax.set_yticks(y,[f"{k} region" for k in CLASSES]); ax.invert_yaxis()
    ax.set_xlim(0,left.max()*1.3); ax.set_xlabel("nominal load (kW)",color=MUTED); ax.legend(frameon=False,fontsize=9)
    return ax


# ------------------------------------------------------------------------------------------- export
def _main_fleet(config_dir):
    from evcs.planning import from_config, with_fleet
    config = read_parameters(Path(config_dir)/"evcs.yaml")
    block = config.get("integration") or {}
    if not block.get("sites"):
        return None
    return {str(b): k for b,k in block["sites"].items()}, with_fleet(from_config(config),block["evs"]), int(block["evs"])


def _pt(fmt, value):
    """Number in Brazilian format (1.234,5)."""
    return fmt.format(value).replace(",","X").replace(".",",").replace("X",".")


def report(z, stations=None):
    s, cells, c = z["settings"], z["cells"], z["customers"]
    km2 = (s["cell_m"]/1000)**2
    rows = []
    for zone in CLASSES:
        part = c[c.zone==zone]
        by = part.groupby("customer_class").size()
        rows.append(f"| {ZONE_PT[zone].capitalize()} | {_pt('{:g}',(cells.zone==zone).sum()*km2)} km² | {_pt('{:,d}',len(part))} | "
                    f"{_pt('{:,.0f}',part.p_kw.sum())} kW ({part.p_kw.sum()/c.p_kw.sum():.0%}) | "
                    f"{by.get('residential',0)} / {by.get('commercial',0)} / {by.get('industrial',0)} |")
    plant = c[c.customer_class=="industrial"]
    doc = [f"# Zoneamento — {z['feeder'].upper()}", "",
           "Gerado por `python -m integration.zoning`. Mapa de regiões residencial, comercial e industrial, sem fluxo de "
           "potência: o modelo de carga e a curva diária continuam os mesmos. Curvas por classe e tarifas ficam para a "
           "próxima etapa.", "",
           "## Critério (hipótese do estudo, `configs/ieee8500.yaml` → `zoning:`)", "",
           "O IEEE 8500 não classifica as cargas: todas são monofásicas em 120/240 V, cada uma atrás do seu transformador "
           "de serviço. A classe vem do tamanho desse transformador:", "",
           f"- **Cliente residencial:** transformador de até {s['residential_max_kva']:g} kVA.",
           f"- **Cliente comercial:** transformador maior que {s['residential_max_kva']:g} kVA.",
           f"- **Cliente industrial:** barra atendida nas três fases por transformadores de pelo menos "
           f"{s['industrial_min_kva_per_phase']:g} kVA.",
           f"- **Regiões:** células de {s['cell_m']:g} m. Industrial: a até {s['industrial_radius_m']:g} m de um cliente "
           f"industrial. Comercial: clientes comerciais com pelo menos {s['commercial_share']:.0%} da carga da célula. "
           "Residencial: o restante.", "",
           "## Resultado", "",
           "| Região | Área | Consumidores | Carga nominal | Residenciais / comerciais / industriais |",
           "|---|---|---|---|---|", *rows, "",
           f"Clientes industriais: {plant.bus.nunique()} barra(s) ("
           + ", ".join(f"`{b}`: {_pt('{:,.0f}',g.p_kw.sum())} kW em {len(g)} fases, a {_pt('{:.1f}',g.distance_km.iloc[0])} km da subestação"
                       for b,g in plant.groupby("bus")) + ")." if len(plant) else "Nenhum cliente industrial.", ""]
    if stations:
        sites, params, evs = stations
        zone = z["buses"].set_index("bus").zone
        doc += [f"## Estações de recarga por região ({_pt('{:,d}',evs)} veículos)", "",
                "| Região | Hubs DC | Eletropostos trifásicos | Eletropostos monofásicos |", "|---|---|---|---|"]
        for zn in CLASSES:
            n = {k: sum(1 for b,v in sites.items() if v == k and zone.get(b) == zn) for k in ("dc","ac","ac1")}
            doc.append(f"| {ZONE_PT[zn].capitalize()} | {n['dc']} | {n['ac']} | {n['ac1']} |")
        doc.append("")
    doc += ["## Figuras", "",
            "- `zones_map.png` — regiões sobre o alimentador.",
            "- `zones_customer_classes.png` — cada consumidor pela classe, com tamanho proporcional à carga.",
            "- `zones_load_by_region.png` — carga de cada região por classe de consumidor.",
            *(["- `zones_with_stations.png` — estações de recarga sobre as regiões."] if stations else []), "",
            "## Limitações", "",
            "- A classe é inferida do transformador de serviço; o IEEE 8500 não traz uso do solo nem classe de consumidor.",
            "- A rede original não tem indústria: a única carga trifásica grande (3 × 250 kVA) é tratada como industrial.",
            "- Os limites (kVA, tamanho da célula, fração comercial, raio industrial) são hipóteses ajustáveis no YAML.", ""]
    return "\n".join(doc)


def export_zoning(output=None, config_dir="configs"):
    import matplotlib
    matplotlib.use("Agg")
    z = build_zoning(config_dir)
    output = Path(output or Path("results")/z["feeder"]/"zoneamento")
    tables = output/"dados"; tables.mkdir(parents=True,exist_ok=True)
    z["customers"][["id","bus","phases","p_kw","kva","customer_class","zone","x","y","distance_km"]].to_csv(
        tables/"customers.csv",index=False)
    z["cells"].to_csv(tables/"cells.csv",index=False)
    figures = {"zones_map": plot_zones(z).figure, "zones_customer_classes": plot_customer_classes(z).figure,
               "zones_load_by_region": plot_load_by_zone(z).figure}
    stations = _main_fleet(config_dir)
    if stations:
        figures["zones_with_stations"] = plot_zones_with_stations(z,*stations).figure
    save_figures(figures,output,clean=True)
    (output/"LEIA-ME.md").write_text(report(z,stations),encoding="utf-8")
    return z


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--configs",default="configs")
    parser.add_argument("--output",help="default: results/<feeder>/zoneamento")
    args = parser.parse_args()
    z = export_zoning(args.output,args.configs)
    print(z["cells"].zone.value_counts().to_string())


if __name__ == "__main__":
    main()
