"""Fig. 1 of the paper: modelling pipeline and its two time resolutions (schematic, no simulation).

python -m integration.framework_figure                 # -> results/ieee8500/paper/fig1_framework.{png,pdf}

Every number on the figure is read from configs/ or from the feeder data (`framework_facts`), so the
schematic follows the configuration the results were produced with.
"""
from pathlib import Path
import argparse
import yaml

def _yaml(config_dir, name):
    return yaml.safe_load((Path(config_dir)/name).read_text(encoding="utf-8"))


def framework_facts(config_dir="configs"):
    """Numbers shown on the figure, from the YAML files and the feeder data."""
    from network.feeder_loader import load_feeder
    from integration.scenarios import study_feeder
    e, b, v = (_yaml(config_dir,f) for f in ("evcs.yaml","bess.yaml","v2g.yaml"))
    sens = Path(config_dir)/"sensitivity.yaml"
    cases = _yaml(config_dir,"sensitivity.yaml")["cases"] if sens.exists() else {}
    feeder = study_feeder(config_dir)
    data = load_feeder(feeder).equipment["feeder_data"][0]
    p, t = e["planning"], e["time"]
    for other in (b["time"],v["time"]):
        if other != t:
            raise ValueError("EVCS, BESS and V2G integration grids differ")
    shares = {k: c["share"] for k,c in p["fleet"]["classes"].items()}
    return dict(
        feeder=data["name"], buses=len(data["buses"]), regulators=len(data["regulators"]), loads=len(data["loads"]),
        plan_min=p["resolution_min"], plan_steps=round(24*60/p["resolution_min"]),
        int_dt_h=t["dt_h"], int_steps=t["steps"], fleet=p["fleet"]["evs"], shares=shares,
        quantile=p["sizing_quantile"], served=p["min_served_share"], sites=len(e["integration"]["sites"]),
        public_evs=e["integration"]["evs"], v2g_cars=v["fleet"]["n_vehicles"], v2g_strategy=v["strategy"],
        window=tuple(v["discharge_window_h"]),
        bess_kw=b["battery"]["power_kw"], bess_kwh=b["battery"]["capacity_kwh"], bess_bus=b["connection"]["bus"],
        bess_strategy=b["strategy"], bess_lines=len(b.get("line_limit",{}).get("lines",{})),
        sens_cases=len(cases), sens_15min=sum(c.get("resolution_min") == 15 for c in cases.values()))


# Elsevier artwork: double-column width 190 mm, Arial, lettering >= 7 pt at the printed size, vector PDF or
# TIFF (500 dpi for line art with colour). Drawn in mm at that size, so font sizes and line widths are final.
WIDTH_MM, HEIGHT_MM = 190., 82.
FONT, TITLE_PT, BODY_PT, SMALL_PT = "Arial", 7.5, 7., 7.
INK, ARROW, FRAME_EDGE, FRAME_FILL = "#1a1a1a", "#3a3a3a", "#c8c8c8", "#f7f7f7"
# Module colours: the same as the network maps (integration.impact.SITE_STYLE)
COLORS = {"network": "#2a78d6", "evcs": "#eb6834", "planning": "#4a3aa7", "v2g": "#1baf7a", "bess": "#c2409a",
          "data": "#7a7a7a"}
LEGEND = (("network","Network model"),("evcs","EV charging"),("planning","Station planning"),
          ("v2g","V2G"),("bess","BESS"),("data","Data / scenarios"))


def _tint(color, amount):
    """Mix `color` with white: amount 0 = white, 1 = the colour."""
    from matplotlib.colors import to_rgb
    return tuple(1-amount*(1-c) for c in to_rgb(color))


def _shade(color, amount=.35):
    from matplotlib.colors import to_rgb
    return tuple(c*(1-amount) for c in to_rgb(color))


def _box(ax, x, y, w, h, title, body, kind, dashed=False):
    """Module box: light tint of the module colour, edge in the colour, bold title and body near the top."""
    from matplotlib.patches import FancyBboxPatch
    c = COLORS[kind]
    ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle="round,pad=0,rounding_size=1.2",fc=_tint(c,.10),ec=c,
                                lw=.8,ls=(0,(3,1.6)) if dashed else "-",zorder=2))
    ax.text(x+w/2,y+h-1.7,title,ha="center",va="top",fontsize=TITLE_PT,weight="bold",color=_shade(c),zorder=3)
    ax.text(x+w/2,y+h-5.3,body,ha="center",va="top",fontsize=BODY_PT,linespacing=1.22,color=INK,zorder=3)


def _path(ax, points, dashed=False, label=None, label_at=None, ha="center", color=ARROW):
    """Orthogonal connector through `points`, arrow head at the last one."""
    from matplotlib.patches import FancyArrowPatch
    ls = (0,(3,1.6)) if dashed else "-"
    for a,b in zip(points[:-2],points[1:-1]):
        ax.plot([a[0],b[0]],[a[1],b[1]],color=color,lw=.7,ls=ls,solid_capstyle="butt",zorder=4)
    ax.add_patch(FancyArrowPatch(points[-2],points[-1],arrowstyle="-|>,head_length=2.2,head_width=1.1",
                                 mutation_scale=1,shrinkA=0,shrinkB=.2,color=color,lw=.7,linestyle=ls,zorder=4))
    if label:
        ax.text(*label_at,label,ha=ha,va="center",fontsize=SMALL_PT,style="italic",color=INK,zorder=5,
                bbox=dict(fc="white",ec="none",pad=.5) if ha == "center" else None)


def _frame(ax, x, y, w, h, label, resolution):
    """Panel of one study: light frame, bold panel label on the left, time resolution on the right."""
    from matplotlib.patches import FancyBboxPatch
    ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle="round,pad=0,rounding_size=1.6",fc=FRAME_FILL,ec=FRAME_EDGE,
                                lw=.6,zorder=0))
    ax.text(x+1.8,y+h-1.5,label,ha="left",va="top",fontsize=TITLE_PT,weight="bold",color=INK,zorder=1)
    ax.text(x+w-1.8,y+h-1.5,resolution,ha="right",va="top",fontsize=TITLE_PT,color=INK,zorder=1)


def framework_figures(facts):
    """{'fig1_framework': Figure}: inputs, (a) the 15-min planning study (S0, S1), (b) the 1-h integration
    (S0-S4), the BESS calibration loop and the sensitivity runs. The title and the explanation belong to
    the caption."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch
    f = facts
    with plt.rc_context({"font.family": FONT,"text.color": INK,"pdf.fonttype": 42}):
        fig = plt.figure(figsize=(WIDTH_MM/25.4,HEIGHT_MM/25.4),facecolor="white")
        ax = fig.add_axes([0,0,1,1]); ax.set_xlim(0,WIDTH_MM); ax.set_ylim(0,HEIGHT_MM); ax.axis("off")

        w, g, x0 = 23.1, 3.0, 33.
        xs = [x0+k*(w+g) for k in range(6)]
        mid = lambda k: xs[k]+w/2

        # Inputs, joined by one line that feeds both studies (parameters and sources: caption and configs/)
        inputs = [(f"{f['feeder']} feeder",f"{f['buses']:,} MV buses\n{f['regulators']} regulator banks","network"),
                  ("Load curve","24 hourly multipliers\nZIP loads","network"),
                  ("EV fleet",f"{f['fleet']:,} EVs\n{100*f['shares'].get('private',0):.0f} % private\n"
                              f"{100*f['shares'].get('taxi',0):.0f} % taxi/ride-hailing","evcs")]
        ih, top, bottom = 17., 76., 17.
        centres = []
        for k,(title,body,kind) in enumerate(inputs):
            y = top-ih-k*(ih+(top-bottom-3*ih)/2)
            _box(ax,2,y,26,ih,title,body,kind)
            centres.append(y+ih/2)
            ax.plot([28,30.5],[y+ih/2]*2,color=ARROW,lw=.7)
        ax.plot([30.5,30.5],[min(centres),max(centres)],color=ARROW,lw=.7)

        # (a) planning and S1 impact study
        _frame(ax,31.5,46.5,156.5,29.5,"(a)  EVCS planning and impact study (S0, S1)",
               f"Δt = {f['plan_min']} min, T = {f['plan_steps']}")
        ya, ha = 52., 15.
        a_boxes = [("Sessions","Monte Carlo,\n1-min resolution","evcs"),
                   ("Sizing",f"{100*f['quantile']:.0f} % quantile\nof concurrency","evcs"),
                   ("Siting","DC: max. coverage\nAC: MILP","planning"),
                   ("Operation",f"FIFO queues,\n≥ {100*f['served']:.0f} % AC served","evcs"),
                   ("Power flow","S0, S1\ntwo regulator views","network"),
                   ("Indicators","voltage, loading,\nlosses, taps","network")]
        for x,(title,body,kind) in zip(xs,a_boxes):
            _box(ax,x,ya,w,ha,title,body,kind)
        ca = ya+ha/2
        for k in range(5):
            _path(ax,[(xs[k]+w,ca),(xs[k+1],ca)])
        _path(ax,[(xs[3]+w*.5,ya),(xs[3]+w*.5,49.2),(xs[2]+w*.7,49.2),(xs[2]+w*.7,ya)],
              label="no: resize",label_at=(xs[3]-g/2,49.2))
        _path(ax,[(30.5,ca),(xs[0],ca)])

        # (b) integrated scenarios
        _frame(ax,31.5,17.,156.5,27.,"(b)  Integrated scenarios S0–S4",
               f"Δt = {f['int_dt_h']:g} h, T = {f['int_steps']}")
        yb, hb = 20., 17.
        b_boxes = [("EVCS demand",f"{f['public_evs']:,} EVs,\n{f['sites']} sites from (a)","evcs"),
                   ("V2G dispatch",f"LP, {f['v2g_cars']} EVs,\nexport {f['window'][0]}–{f['window'][1]} h","v2g"),
                   ("BESS dispatch",f"{f['bess_kw']/1000:g} MW / {f['bess_kwh']/1000:g} MWh,\nline-limit LP","bess"),
                   ("Scenarios","S0 base\nS1–S4: EVCS\n± BESS ± V2G","data"),
                   ("Power flow","S0–S4\ntwo regulator views","network"),
                   ("Criterion","no new overload,\nmin. V ≥ 0.95 pu","network")]
        for x,(title,body,kind) in zip(xs,b_boxes):
            _box(ax,x,yb,w,hb,title,body,kind)
        cb = yb+hb/2
        for k in range(5):
            _path(ax,[(xs[k]+w,cb),(xs[k+1],cb)])
        _path(ax,[(30.5,cb),(xs[0],cb)])

        # BESS calibration loop and sensitivity runs
        cx, cw, ch = xs[2], xs[4]+w-xs[2], 11.
        _box(ax,cx,1.5,cw,ch,"BESS calibration (once per S1 configuration)",
             f"per-line fit a, b, s from a fixed-schedule run ({f['bess_lines']} lines)","bess",dashed=True)
        _path(ax,[(mid(4),yb),(mid(4),1.5+ch)],dashed=True,color=COLORS["bess"])
        _path(ax,[(mid(2),1.5+ch),(mid(2),yb)],dashed=True,color=COLORS["bess"],
              label="a, b, s",label_at=(mid(2)+1.2,(1.5+ch+yb)/2),ha="left")
        _box(ax,2,1.5,26,ch,"Sensitivity",f"(b) rerun for {f['sens_cases']} cases","data",dashed=True)
        _path(ax,[(28,7.),(mid(0),7.),(mid(0),yb)],dashed=True)

        # Colour key, one row above the panels
        lx, ly = 2., HEIGHT_MM-2.6
        for kind,label in LEGEND:
            ax.add_patch(FancyBboxPatch((lx,ly-.9),3.6,1.8,boxstyle="round,pad=0,rounding_size=.4",
                                        fc=_tint(COLORS[kind],.10),ec=COLORS[kind],lw=.8))
            t = ax.text(lx+4.8,ly,label,ha="left",va="center",fontsize=SMALL_PT,color=INK)
            box = t.get_window_extent(fig.canvas.get_renderer()).transformed(ax.transData.inverted())
            lx = box.x1+5.  # measured text width, so the gaps are equal
    return {"fig1_framework": fig}


def save_paper_figure(figures, folder, dpi=500):
    """Vector PDF (preferred by Elsevier), TIFF at `dpi` (colour line art) and PNG for previews."""
    import matplotlib.pyplot as plt
    folder = Path(folder); folder.mkdir(parents=True,exist_ok=True)
    for name,fig in figures.items():
        fig.savefig(folder/f"{name}.pdf",facecolor="white")
        fig.savefig(folder/f"{name}.tif",dpi=dpi,facecolor="white",pil_kwargs={"compression": "tiff_lzw"})
        fig.savefig(folder/f"{name}.png",dpi=dpi,facecolor="white")
        plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--configs",default="configs")
    ap.add_argument("--output",default="results/ieee8500/paper")
    a = ap.parse_args()
    save_paper_figure(framework_figures(framework_facts(a.configs)),a.output)
    print(Path(a.output)/"fig1_framework.png")


if __name__ == "__main__":
    main()
