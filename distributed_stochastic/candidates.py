"""V2.0 — triagem topológica/física de barras candidatas BESS e V2G.

Este módulo NÃO realiza localização ótima, dimensionamento ou aprovação de conexão.
Ele usa SOMENTE rede física normalizada, resultados AC S0/S1 e localização das EVCS.
Distâncias usam coordenadas sintéticas do IEEE 8500 (pés), não distância viária.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import yaml

FT_TO_M = 0.3048
REQUIRED_VOLT = {"hour", "bus", "phase", "v_pu"}
REQUIRED_BRANCH = {"hour", "line", "phase", "loading_pct"}


@dataclass(frozen=True)
class ScreenSettings:
    bess_count: int = 16
    v2g_count: int = 16
    spacing_m: float = 300.0
    electrical_spacing_km: float = 0.10
    proximity_scale_km: float = 0.70
    min_local_three_phase_lines: int = 1
    include_anchor: bool = True
    v2g_only_existing_evcs: bool = False

    def __post_init__(self):
        if not 1 <= self.bess_count <= 200 or not 1 <= self.v2g_count <= 200:
            raise ValueError("Número de candidatos deve ser 1..200 por tipo")
        if min(self.spacing_m, self.electrical_spacing_km, self.proximity_scale_km) < 0:
            raise ValueError("Distâncias negativas não são válidas")
        if self.proximity_scale_km <= 0:
            raise ValueError("proximity_scale_km deve ser positiva")


def _hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _validate_table(df: pd.DataFrame, required: set[str], kind: str):
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{kind} precisa das colunas {sorted(missing)}")
    key = ["hour", "bus", "phase"] if "bus" in required else ["hour", "line", "phase"]
    if df.duplicated(key).any():
        raise ValueError(f"{kind}: registros duplicados {key}")
    if set(pd.to_numeric(df.hour, errors="raise").astype(int)) != set(range(24)):
        raise ValueError(f"{kind}: faltam horários de 0 a 23")
    var = "v_pu" if kind == "voltages" else "loading_pct"
    if not np.isfinite(pd.to_numeric(df[var], errors="raise")).all():
        raise ValueError(f"{kind}: contém NaN/inf")


def _load_results(baseline: Path) -> dict:
    out = {}
    for label in ("S0", "S1"):
        out[label] = {}
        for kind in ("voltages", "branches"):
            file = baseline / label / f"{kind}.csv"
            if not file.is_file():
                raise FileNotFoundError(f"Arquivo de referência ausente: {file}")
            data = pd.read_csv(file)
            _validate_table(data, REQUIRED_VOLT if kind == "voltages" else REQUIRED_BRANCH, kind)
            data["phase"] = data.phase.astype(str).str.upper()
            data["bus" if kind == "voltages" else "line"] = data["bus" if kind == "voltages" else "line"].astype(str).str.lower()
            out[label][kind] = data
    return out


def _energized_graph(data: dict) -> nx.Graph:
    g = nx.Graph()
    for b in data["buses"]:
        g.add_node(str(b["id"]).lower())
    def edge(u, v, length):
        u, v = str(u).lower(), str(v).lower()
        if u in g and v in g and u != v:
            g.add_edge(u, v, weight=max(float(length), 0.0))
    for item in data["lines"]:
        edge(item["from_bus"], item["to_bus"], item.get("length_km", 0))
    for item in data.get("switches", []):
        if item.get("closed", True):
            edge(item["from_bus"], item["to_bus"], 0.0)
    for item in data.get("regulators", []):
        edge(item["from_bus"], item["to_bus"], 0.0)
    t = data.get("transformer")
    if t:
        edge(t["from_bus"], t["to_bus"], 0.0)
    return g


def _modal_three_phase_kv(data: dict) -> float:
    kv = [round(float(b["vn_kv"]), 4) for b in data["buses"]
          if set(str(b["phases"]).upper()) == set("ABC") and float(b["vn_kv"]) > 0]
    if not kv:
        raise ValueError("Nenhuma barra trifásica com tensão nominal válida")
    return float(pd.Series(kv).mode().iat[0])


def _local_line_peaks(data: dict, branches: pd.DataFrame) -> tuple[dict, dict]:
    peaks = branches.groupby("line", observed=True).loading_pct.max().to_dict()
    local = defaultdict(float)
    three_ph = defaultdict(int)
    for l in data["lines"]:
        ident = str(l["id"]).lower()
        for bus in (str(l["from_bus"]).lower(), str(l["to_bus"]).lower()):
            local[bus] = max(local[bus], float(peaks.get(ident, 0.0)))
            if set(str(l["phases"]).upper()) == set("ABC"):
                three_ph[bus] += 1
    return local, three_ph


def _bus_voltages(results: dict) -> pd.DataFrame:
    s0, s1 = results["S0"]["voltages"], results["S1"]["voltages"]
    def agg(frame, prefix):
        return frame.groupby("bus", observed=True).agg(**{
            f"vmin_{prefix}":("v_pu", "min"),
            f"vmax_{prefix}":("v_pu", "max"),
        })
    return agg(s0, "s0").join(agg(s1, "s1"), how="outer")


def _nearest_critical_distances(g: nx.Graph, data: dict, new_lines: set[str]) -> dict:
    sources = set()
    for l in data["lines"]:
        if str(l["id"]).lower() in new_lines:
            sources.update([str(l["from_bus"]).lower(), str(l["to_bus"]).lower()])
    sources &= set(g)
    if not sources:
        raise ValueError("Nenhuma das novas linhas críticas está conectada à topologia")
    return nx.multi_source_dijkstra_path_length(g, sources, weight="weight")


def _sites_config(repo: Path) -> tuple[dict, str, str]:
    evcs_file = repo / "configs/evcs.yaml"
    bess_file = repo / "configs/bess.yaml"
    if not evcs_file.is_file() or not bess_file.is_file():
        raise FileNotFoundError("configs/evcs.yaml e configs/bess.yaml são obrigatórios")
    evcs = yaml.safe_load(evcs_file.read_text(encoding="utf-8"))
    bess = yaml.safe_load(bess_file.read_text(encoding="utf-8"))
    sites = {str(bus).lower(): str(kind) for bus, kind in evcs["integration"]["sites"].items()}
    v2g_host = str(evcs["station"]["bus"]).lower()
    bess_host = str(bess["connection"]["bus"]).lower()
    sites.setdefault(v2g_host, "current_v2g_host")
    return sites, bess_host, v2g_host


def build_inventory(repo: Path, baseline: Path, settings: ScreenSettings) -> tuple[pd.DataFrame, dict]:
    """Retorna inventário auditável (inclui barras rejeitadas) e metadados."""
    repo, baseline = Path(repo).resolve(), Path(baseline).resolve()
    jf = repo / "src/network/data/ieee8500.json"
    if not jf.is_file():
        raise FileNotFoundError(f"Rede física ausente: {jf}")
    data = json.loads(jf.read_text(encoding="utf-8"))
    if data.get("name") != "IEEE 8500":
        raise ValueError("Rede inesperada; esperado JSON IEEE 8500 do projeto original")
    results = _load_results(baseline)
    g = _energized_graph(data)
    source = str(data["source_bus"]).lower()
    if source not in g:
        raise ValueError("Barra da fonte não consta da topologia")
    reachable = nx.node_connected_component(g, source)
    d_source = nx.single_source_dijkstra_path_length(g, source, weight="weight")
    lines0 = set(results["S0"]["branches"].loc[lambda x: x.loading_pct > 100., "line"])
    lines1 = set(results["S1"]["branches"].loc[lambda x: x.loading_pct > 100., "line"])
    new_lines = lines1 - lines0
    if not new_lines:
        raise ValueError("S1 não possui linhas novas sobrecarregadas; verifique o replay de referência")
    distance_critical = _nearest_critical_distances(g, data, new_lines)
    peaks, n_threelines = _local_line_peaks(data, results["S1"]["branches"])
    voltages = _bus_voltages(results)
    sites, bess_anchor, v2g_anchor = _sites_config(repo)
    critical_nominal_kv = _modal_three_phase_kv(data)
    protected = {source}
    if data.get("transformer"):
        protected.update((str(data["transformer"]["from_bus"]).lower(), str(data["transformer"]["to_bus"]).lower()))
    for reg in data.get("regulators", []):
        protected.update((str(reg["from_bus"]).lower(), str(reg["to_bus"]).lower()))
    # Não excluir automaticamente barras próximas de capacitores: a proximidade é um alerta,
    # mas não prova inviabilidade física.
    cap_nodes = {str(x["bus"]).lower() for x in data.get("capacitors", [])}
    load_kw = defaultdict(float)
    for ld in data.get("loads", []):
        load_kw[str(ld["bus"]).lower()] += float(ld["p_kw"])
    rows = []
    for b in data["buses"]:
        bus = str(b["id"]).lower()
        ph = "".join(sorted(set(str(b["phases"]).upper())))
        kv = float(b["vn_kv"])
        xy = b.get("xy")
        valid_xy = (isinstance(xy, (list, tuple)) and len(xy) == 2 and
                    all(v is not None and math.isfinite(float(v)) for v in xy))
        v = voltages.loc[bus] if bus in voltages.index else None
        vmin = float(v.vmin_s1) if v is not None and pd.notna(v.vmin_s1) else math.nan
        vmax = float(v.vmax_s1) if v is not None and pd.notna(v.vmax_s1) else math.nan
        reasons = []
        if set(ph) != set("ABC"):
            reasons.append("not_three_phase")
        if abs(kv - critical_nominal_kv) > 0.01 * critical_nominal_kv:
            reasons.append("different_nominal_voltage")
        if bus not in reachable:
            reasons.append("not_energized_from_source")
        if bus in protected:
            reasons.append("source_transformer_or_regulator_terminal")
        if n_threelines[bus] < settings.min_local_three_phase_lines:
            reasons.append("no_adjacent_three_phase_line")
        if not valid_xy:
            reasons.append("coordinates_missing")
        if not np.isfinite(vmin) or not np.isfinite(vmax):
            reasons.append("missing_s1_ac_voltage")
        evcs_kind = sites.get(bus, "none")
        existing_evcs = evcs_kind != "none"
        near_critical = float(distance_critical.get(bus, math.inf))
        low_score = float(np.clip((0.97-vmin)/0.07, 0., 1.)) if np.isfinite(vmin) else 0.
        over_risk = float(np.clip((vmax-1.05)/0.03, 0., 1.)) if np.isfinite(vmax) else 0.
        local_loading = float(peaks.get(bus, 0.))
        local_score = float(np.clip((local_loading-90.)/50., 0., 1.))
        dist_score = math.exp(-near_critical/settings.proximity_scale_km)
        rows.append(dict(bus=bus, phases=ph, vn_kv=kv,
            x_ft=float(xy[0]) if valid_xy else math.nan,
            y_ft=float(xy[1]) if valid_xy else math.nan,
            source_distance_km=float(d_source.get(bus, math.inf)),
            critical_distance_km=near_critical, adjacent_three_phase_lines=n_threelines[bus],
            local_peak_loading_pct=local_loading, local_load_kw=float(load_kw[bus]),
            vmin_s0_pu=float(v.vmin_s0) if v is not None and pd.notna(v.vmin_s0) else math.nan,
            vmax_s0_pu=float(v.vmax_s0) if v is not None and pd.notna(v.vmax_s0) else math.nan,
            vmin_s1_pu=vmin, vmax_s1_pu=vmax,
            existing_evcs=bool(existing_evcs), evcs_kind=evcs_kind,
            on_capacitor_bus=bool(bus in cap_nodes),
            is_original_bess_bus=(bus == bess_anchor),
            is_original_v2g_bus=(bus == v2g_anchor),
            eligible=len(reasons) == 0,
            exclusion_reasons=";".join(reasons),
            _low=low_score, _over=over_risk, _local=local_score, _near=dist_score))
    inv = pd.DataFrame(rows)
    max_log = math.log1p(float(inv.local_load_kw.clip(lower=0.).max()))
    inv["demand_score"] = np.log1p(inv.local_load_kw.clip(lower=0.)) / max(max_log, 1.)
    inv["score_bess"] = (0.40*inv._near + 0.25*inv._local + 0.30*inv._low +
                          0.05*inv.demand_score - 0.15*inv._over)
    inv["score_v2g"] = (0.27*inv._near + 0.17*inv._local + 0.23*inv._low +
                         0.13*inv.demand_score + 0.20*inv.existing_evcs.astype(float) -
                         0.15*inv._over)
    inv.drop(columns=["_low", "_over", "_local", "_near"], inplace=True)
    inv.sort_values("bus", inplace=True)
    inv.reset_index(drop=True, inplace=True)
    meta = {
        "status": "TOPOLOGY_AND_BASELINE_SCREEN_ONLY_NOT_OPTIMAL_NOT_GRID_APPROVED",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source_json_sha256": _hash(jf),
        "s0_branches_sha256": _hash(baseline/"S0/branches.csv"),
        "s1_branches_sha256": _hash(baseline/"S1/branches.csv"),
        "evcs_config_sha256": _hash(repo/"configs/evcs.yaml"),
        "source_bus": source,
        "nominal_three_phase_kv": critical_nominal_kv,
        "new_line_ids_s1_vs_s0": sorted(new_lines),
        "new_line_count": len(new_lines),
        "bess_anchor": bess_anchor, "v2g_anchor": v2g_anchor,
        "total_buses": len(inv), "eligible_buses": int(inv.eligible.sum()),
        "scoring_method": {
            "bess": f"0.40*exp(-d_crit/{settings.proximity_scale_km})+0.25*local_stress+0.30*low_voltage+0.05*demand-0.15*overvoltage",
            "v2g": f"0.27*exp(-d_crit/{settings.proximity_scale_km})+0.17*local_stress+0.23*low_voltage+0.13*demand+0.20*existing_evcs-0.15*overvoltage",
            "warning": "Pesos heurísticos de triagem; não são coeficientes físicos de sensibilidade e não provam localização ótima."},
        "assumptions": [
            "Coordenadas (xy) IEEE em pés; espaçamento euclidiano não comprova disponibilidade de terreno nem mobilidade.",
            "EVCS existentes não implicam carregadores bidirecionais; exigir upgrade e CAPEX.",
            "OpenDSS com fonte 1.05 pu e ampacidades de estudo; verificar sensibilidade antes do MILP.",
            "S1 contém 2850 EVs convencionais e 150 EVs V2G já contabilizados; não duplicar perfis.",
            "Uma barra trifásica elegível ainda depende de estudos de conexão, proteção, obras e capacidade local.",
        ]
    }
    return inv, meta


def _allowed_gap(candidate: pd.Series, chosen: list[pd.Series], graph: nx.Graph,
                 spacing_m: float, electrical_km: float,
                 short_dist: dict[str, dict[str, float]] | None = None) -> bool:
    for other in chosen:
        euclidean = math.hypot(float(candidate.x_ft-other.x_ft),float(candidate.y_ft-other.y_ft))*FT_TO_M
        if euclidean < spacing_m - 1e-9:
            return False
        if electrical_km > 0:
            if short_dist is not None:
                km = short_dist[str(other.bus)].get(str(candidate.bus), math.inf)
            else:
                try:
                    km = nx.shortest_path_length(graph,str(candidate.bus),str(other.bus),weight="weight")
                except nx.NetworkXNoPath:
                    km = math.inf
            if km < electrical_km - 1e-9:
                return False
    return True


def select_candidates(inv: pd.DataFrame, network: dict, settings: ScreenSettings,
                      resource: str) -> pd.DataFrame:
    """Seleção determinística por escore + separação espacial, mantendo a barra histórica."""
    if resource not in ("bess", "v2g"):
        raise ValueError("resource deve ser bess ou v2g")
    g = _energized_graph(network)
    eligible = inv.loc[inv.eligible].copy()
    if resource == "v2g" and settings.v2g_only_existing_evcs:
        eligible = eligible.loc[eligible.existing_evcs]
    score = "score_"+resource
    eligible.sort_values([score,"bus"],ascending=[False,True],inplace=True)
    count = settings.bess_count if resource=="bess" else settings.v2g_count
    chosen: list[pd.Series] = []
    short_dist: dict[str, dict[str,float]] = {}
    def remember(row):
        chosen.append(row)
        if settings.electrical_spacing_km > 0:
            short_dist[str(row.bus)] = nx.single_source_dijkstra_path_length(
                g,str(row.bus),cutoff=settings.electrical_spacing_km,weight="weight")
    anchor_col = "is_original_bess_bus" if resource == "bess" else "is_original_v2g_bus"
    if settings.include_anchor:
        anchors = eligible.loc[eligible[anchor_col]]
        if anchors.empty:
            raise ValueError(f"Barra original {resource} foi rejeitada; revise critérios antes de continuar")
        remember(anchors.iloc[0])
    for _, row in eligible.iterrows():
        if len(chosen) >= count:
            break
        if str(row.bus) in {str(c.bus) for c in chosen}:
            continue
        if _allowed_gap(row,chosen,g,settings.spacing_m,settings.electrical_spacing_km,short_dist):
            remember(row)
    frame = pd.DataFrame(chosen).copy()
    frame.insert(0, "candidate_rank", np.arange(1,len(frame)+1))
    frame.insert(1, "resource", resource.upper())
    frame["infrastructure_status"] = np.select(
        [(resource=="bess") & frame.is_original_bess_bus,
         (resource=="v2g") & frame.is_original_v2g_bus,
         (resource=="v2g") & frame.existing_evcs],
        ["existing_bess_host", "existing_v2g_host", "evcs_upgrade_required"],
        default="new_connection_and_site_required")
    frame["candidate_status"] = "SCREENED_ONLY_REQUIRE_AC_AND_TECHNICAL_FEASIBILITY"
    return frame


def write_screen(repo: Path, baseline: Path, output: Path, settings: ScreenSettings) -> dict:
    output.mkdir(parents=True,exist_ok=True)
    inv, meta = build_inventory(repo,baseline,settings)
    net=json.loads((repo/"src/network/data/ieee8500.json").read_text(encoding="utf-8"))
    bess=select_candidates(inv,net,settings,"bess")
    v2g=select_candidates(inv,net,settings,"v2g")
    inv.to_csv(output/"inventario_todas_barras.csv",index=False,float_format="%.9f")
    bess.to_csv(output/"candidatos_bess.csv",index=False,float_format="%.9f")
    v2g.to_csv(output/"candidatos_v2g.csv",index=False,float_format="%.9f")
    both=pd.concat([bess,v2g],ignore_index=True)
    both.to_csv(output/"candidatos_unificados.csv",index=False,float_format="%.9f")
    meta["screen_settings"] = {k:getattr(settings,k) for k in settings.__dataclass_fields__}
    meta["selected"] = {"bess":len(bess),"v2g":len(v2g),
                         "union_unique_buses":int(both.bus.nunique()),
                         "existing_evcs_selected_v2g":int(v2g.existing_evcs.sum()),
                         "v2g_new_site_requirements":int((~v2g.existing_evcs).sum())}
    (output/"manifest_v20.json").write_text(json.dumps(meta,indent=2,ensure_ascii=False),encoding="utf-8")
    return meta
