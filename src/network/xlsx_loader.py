"""Preliminary normalized XLSX ingestion. Unsupported devices are retained."""
from pathlib import Path
import pandas as pd
from core.schemas import Bus, Line, Network, Profile, TimeGrid

SHEETS = {
 "buses": ["id","phases","vn_ln_v","x","y"],
 "lines": ["id","from_bus","to_bus","phases","r_ohm","x_ohm","ampacity_a"],
 "substations": ["id","bus","vn_ln_v"],
 "loads": ["id","bus","phase","p_kw","q_kvar","model"],
 "load_profiles": ["time","bus","phase","p_kw","q_kvar"],
 "transformers": ["id","from_bus","to_bus","phases","sn_kva","vn_hv_kv","vn_lv_kv","vector_group","vk_pct","vkr_pct"],
 "regulators": ["id","from_bus","to_bus","phases","tap_pu","setpoint_pu"],
 "capacitors": ["id","bus","phases","q_kvar","status"],
 "switches": ["id","from_bus","to_bus","phases","closed"],
 "generation": ["id","bus","phase","p_kw","q_kvar"]}

def from_tables(tables: dict[str,pd.DataFrame], name: str = "Honduras") -> Network:
    """Preserve equipment tables without claiming they can already be solved."""
    for sheet, columns in SHEETS.items():
        if sheet not in tables or list(tables[sheet].columns) != columns:
            raise ValueError(f"Missing sheet or incorrect headers: {sheet}; expected {columns}")
        required=[c for c in columns if c not in ("x","y")]
        if tables[sheet][required].isna().any().any():
            raise ValueError(f"Missing required fields: {sheet}")
        if "id" in columns and tables[sheet].id.duplicated().any():
            raise ValueError(f"Duplicate IDs: {sheet}")
    if tables["buses"].empty or len(tables["substations"]) != 1:
        raise ValueError("Buses and exactly one source/substation required by initial importer")
    buses=[Bus(str(r.id),str(r.phases),float(r.vn_ln_v),None if pd.isna(r.x) else float(r.x),None if pd.isna(r.y) else float(r.y)) for r in tables["buses"].itertuples()]
    lines=[Line(str(r.id),str(r.from_bus),str(r.to_bus),str(r.phases),float(r.r_ohm),float(r.x_ohm),float(r.ampacity_a)) for r in tables["lines"].itertuples()]
    ids={b.id:b for b in buses}
    for sheet, table in tables.items():
        for column in ("bus","from_bus","to_bus"):
            if column in table:
                for value in table[column]:
                    if str(value) not in ids:
                        raise ValueError(f"Unknown bus {value} in {sheet}")
        for row in table.to_dict("records"):
            phases=str(row.get("phases",row.get("phase","")))
            for column in ("bus","from_bus","to_bus"):
                if column in row and phases and not set(phases)<=set(ids[str(row[column])].phases):
                    raise ValueError(f"Invalid phase in {sheet}")
    net=Network(name,buses,lines,str(tables["substations"].iloc[0].bus),False,
                {k:v.to_dict("records") for k,v in tables.items() if k not in ("buses","lines")})
    net.validate()
    return net

def load_xlsx(path: str | Path) -> Network:
    tables=pd.read_excel(path,sheet_name=None,engine="openpyxl",dtype={"id":str,"bus":str,"from_bus":str,"to_bus":str})
    tables.pop("instructions",None)
    return from_tables(tables)

def load_profile(network: Network, grid: TimeGrid) -> Profile:
    """XLSX load power uses positive consumption; convert once to injection."""
    df=pd.DataFrame(network.equipment.get("load_profiles",[]))
    if df.empty:
        raise ValueError("No time-dependent load data")
    df["time"]=pd.to_datetime(df.time,utc=True).dt.tz_convert(grid.index.tz)
    df[["p_kw","q_kvar"]]=-df[["p_kw","q_kvar"]].astype(float)
    result=Profile(df,grid,"imported-loads")
    result.validate(network)
    return result
