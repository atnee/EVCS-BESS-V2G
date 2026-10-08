"""Versioned module output contract, independent of EVCS/BESS/V2G implementations."""
from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import pandas as pd
from core.schemas import Profile, TimeGrid


def export_profile_bundle(profile, output, *, module, context, tables=None, input_files=()):
    profile.validate()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    profile.export(output / "profile.csv")
    manifest = dict(schema_version=1, module=module, context=context,
                    asset_id=profile.asset_id, vehicle_ids=list(profile.vehicle_ids),
                    time=asdict(profile.grid), metadata=profile.metadata,
                    sign_convention="positive injection, negative consumption; kW/kvar per phase",
                    profile_sha256=hashlib.sha256((output/"profile.csv").read_bytes()).hexdigest(),
                    inputs_sha256={Path(p).name:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in input_files})
    for name, table in (tables or {}).items():
        table.to_csv(output/f"{name}.csv",index=False)
    (output/"manifest.json").write_text(json.dumps(manifest,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    return output


def read_profile_bundle(path, network=None):
    path = Path(path)
    manifest = json.loads((path/"manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported module output schema")
    if hashlib.sha256((path/"profile.csv").read_bytes()).hexdigest() != manifest["profile_sha256"]:
        raise ValueError("Profile hash mismatch")
    data = pd.read_csv(path/"profile.csv",dtype={"bus":str,"phase":str})
    data["time"] = pd.to_datetime(data.time)
    profile = Profile(data,TimeGrid(**manifest["time"]),manifest["asset_id"],
                      tuple(manifest["vehicle_ids"]),manifest["metadata"])
    profile.validate(network)
    return profile
