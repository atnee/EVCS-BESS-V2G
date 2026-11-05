"""Reproduce the selected trial-7 schedule from the normal BESS configuration."""
from argparse import ArgumentParser
from pathlib import Path
from shutil import copy2

import numpy as np
import pandas as pd
import yaml

from bess.model import Battery, simulate
from core.schemas import read_parameters


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "results" / "ieee8500" / "peak_reserve_l3029502_trial7"


def reference_dispatch(reference, scenario):
    table = pd.read_csv(reference / "modules" / "bess" / scenario / "dispatch.csv")
    return table.demand_before_kw.to_numpy(float), table.injection_kw.to_numpy(float)


def experiment_config():
    config = read_parameters(ROOT / "configs" / "bess.yaml")
    battery = config["battery"]
    if (config["strategy"] != "trial7_schedule" or
            config["connection"]["bus"] != "l3029502" or
            battery["capacity_kwh"] != 7200 or battery["power_kw"] != 2200 or
            battery["soc_initial"] != .3 or config["time"]["dt_h"] != 1.0):
        raise ValueError("Unexpected selected trial-7 configuration")
    commands = np.asarray(config["trial7_schedule"]["commands_kw"], float)
    _, prior = reference_dispatch(REFERENCE, "S2")
    if not np.allclose(commands, prior, atol=1e-8):
        raise ValueError("Selected schedule differs from saved trial-7 results")
    result = simulate(Battery(**battery), commands, 1.0, final_soc=battery["soc_initial"])
    if np.max(np.abs(result.rejected_kw)) > 1e-6:
        raise ValueError("Trial-7 schedule exceeds BESS power or SOC limits")
    config["strategy"] = "fixed_peak_reserve_trial"
    config["peak_reserve_trial"] = {
        "reference": str(REFERENCE.relative_to(ROOT)),
        "commands_kw": commands.tolist(),
    }
    return config


def fixed_peak_reserve_trial(battery, demand_kw, config, dt_h):
    if dt_h != 1.0:
        raise ValueError("Trial 7 requires hourly integration")
    reference = ROOT / config["peak_reserve_trial"]["reference"]
    demand = np.asarray(demand_kw, float)
    for scenario in ("S2", "S4"):
        prior, _ = reference_dispatch(reference, scenario)
        if demand.shape == prior.shape and np.allclose(demand, prior, atol=1e-6):
            break
    else:
        raise ValueError("Scenario demand differs from the saved trial-7 scenario")
    return simulate(battery, config["peak_reserve_trial"]["commands_kw"],
                    dt_h, final_soc=battery.soc_initial)


def preview():
    config = experiment_config()
    battery = Battery(**config["battery"])
    reference = ROOT / config["peak_reserve_trial"]["reference"]
    for scenario in ("S2", "S4"):
        demand, _ = reference_dispatch(reference, scenario)
        dispatch = fixed_peak_reserve_trial(battery, demand, config, 1.0)
        print(f"{scenario}: bus={config['connection']['bus']}; "
              f"charge 00-06={dispatch.injection_kw[0]:.1f} kW each; "
              f"discharge 16={dispatch.injection_kw[16]:.1f}, "
              f"18={dispatch.injection_kw[18]:.1f} kW; "
              f"SOC min/max/final={dispatch.soc.min():.4f}/"
              f"{dispatch.soc.max():.4f}/{dispatch.soc[-1]:.4f}")
    print("Network loading and voltage require the full electrical simulation.")


def integrate(output):
    from bess.strategies import STRATEGIES
    from integration.coordinator import export_results

    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Choose a new result directory: {output}")
    config = experiment_config()
    battery = Battery(**config["battery"])
    reference = ROOT / config["peak_reserve_trial"]["reference"]
    for scenario in ("S2", "S4"):
        demand, _ = reference_dispatch(reference, scenario)
        fixed_peak_reserve_trial(battery, demand, config, 1.0)
    config_dir = output / "experiment_configs"
    config_dir.mkdir(parents=True)
    for source in (ROOT / "configs").glob("*.yaml"):
        copy2(source, config_dir / source.name)
    (config_dir / "bess.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    STRATEGIES["fixed_peak_reserve_trial"] = fixed_peak_reserve_trial
    print(export_results(output, config_dir, frozen_taps=True).to_string(index=False))


def main():
    parser = ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("preview")
    run = sub.add_parser("integrate")
    run.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    preview() if args.action == "preview" else integrate(args.output)


if __name__ == "__main__":
    main()
