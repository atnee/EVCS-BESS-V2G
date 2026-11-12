"""Common style of the paper figures (Elsevier artwork): drawn at the printed size in mm, Arial, lettering of
7-8 pt, thin lines, one colour per scenario and per asset in every figure."""

FONT, LABEL_PT, BODY_PT = "Arial", 8., 7.
INK, MUTED, GRID = "#1a1a1a", "#555555", "#e3e3e3"
SCENARIO_COLORS = {"S0": "#7a7a7a", "S1": "#d94f1f", "S2": "#c2409a", "S3": "#1baf7a", "S4": "#2a78d6"}
STATION_COLORS = {"dc": "#4a3aa7", "ac": "#eb6834", "ac1": "#1baf7a"}  # as the network maps
BESS_COLOR, V2G_COLOR = "#c2409a", "#1baf7a"
DOUBLE_MM, ONE_HALF_MM, SINGLE_MM = 190., 140., 90.  # Elsevier column widths


def rc():
    """matplotlib rc for the paper figures (use with plt.rc_context)."""
    return {"font.family": FONT, "text.color": INK, "pdf.fonttype": 42, "axes.edgecolor": INK,
            "axes.labelcolor": INK, "axes.linewidth": .5, "xtick.major.width": .5, "ytick.major.width": .5,
            "xtick.major.size": 2, "ytick.major.size": 2, "xtick.labelsize": BODY_PT, "ytick.labelsize": BODY_PT,
            "axes.labelsize": BODY_PT, "legend.fontsize": BODY_PT, "lines.linewidth": 1.}


def figure_mm(width, height):
    import matplotlib.pyplot as plt
    return plt.figure(figsize=(width/25.4, height/25.4), facecolor="white")


def panel(ax, title):
    """Panel title on the left, no top/right spines."""
    ax.set_title(title, loc="left", fontsize=LABEL_PT, weight="bold", pad=3)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    return ax


def scale_bar(ax, x_right, y, unit_m, km=2, color=INK):
    """Scale bar of `km` ending at x_right (feeder coordinates in units of unit_m metres)."""
    length = km*1000/unit_m
    ax.plot([x_right-length, x_right], [y, y], color=color, lw=1.2, solid_capstyle="butt")
    ax.annotate(f"{km} km", (x_right-length/2, y), xytext=(0, 2), textcoords="offset points", ha="center",
                va="bottom", fontsize=BODY_PT)
