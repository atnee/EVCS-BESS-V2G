"""Trial 9: relocate trial-7 BESS to the downstream south-trunk bus."""
from argparse import ArgumentParser
from pathlib import Path
from shutil import copy2

import numpy as np
import yaml

from bess.model import Battery
from bess.peak_reserve_trial import fixed_peak_reserve_trial, reference_dispatch
from core.schemas import read_parameters


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "results" / "ieee8500" / "peak_reserve_l3029502_trial7"
CANDIDATE_BUS = "m1069504"


def experiment_config():
    config = read_parameters(REFERENCE / "experiment_configs" / "bess.yaml")
    battery = config["battery"]
    if (config["connection"]["bus"] != "l3029502" or
            config["connection"]["phases"] != "ABC" or
            battery["capacity_kwh"] != 7200 or battery["power_kw"] != 2200 or
            battery["soc_initial"] != .3 or config["time"]["dt_h"] != 1.0):
        raise ValueError("Unexpected trial-7 reference configuration")
    _, prior_commands = reference_dispatch(REFERENCE, "S2")
    if not np.allclose(config["peak_reserve_trial"]["commands_kw"], prior_commands, atol=1e-8):
        raise ValueError("Trial-7 reference dispatch differs from its saved configuration")

    config["connection"]["bus"] = CANDIDATE_BUS
    config["peak_reserve_trial"]["reference"] = str(REFERENCE.relative_to(ROOT))
    config["south_bus_trial9"] = {
        "reference": str(REFERENCE.relative_to(ROOT)),
        "candidate_bus": CANDIDATE_BUS,
        "hypothesis": "Downstream placement may relieve the south trunk directly",
    }
    return config


def preview():
    config = experiment_config()
    battery = Battery(**config["battery"])
    for scenario in ("S2", "S4"):
        demand, prior_commands = reference_dispatch(REFERENCE, scenario)
        result = fixed_peak_reserve_trial(battery, demand, config, 1.0)
        if not np.allclose(result.injection_kw, prior_commands, atol=1e-8):
            raise ValueError("Trial-9 dispatch is not identical to trial 7")
        if np.max(np.abs(result.rejected_kw)) > 1e-6:
            raise ValueError("Trial-9 dispatch exceeds battery limits")
        print(f"{scenario}: bus={CANDIDATE_BUS}; same 24-hour dispatch as trial 7; "
              f"SOC min/max/final={result.soc.min():.4f}/"
              f"{result.soc.max():.4f}/{result.soc[-1]:.4f}")
    print("Line loading and voltage require the full network simulation.")


def integrate(output):
    from bess.strategies import STRATEGIES
    from integration.coordinator import export_results

    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Choose a new result directory: {output}")
    config = experiment_config()
    battery = Battery(**config["battery"])
    for scenario in ("S2", "S4"):
        demand, prior_commands = reference_dispatch(REFERENCE, scenario)
        result = fixed_peak_reserve_trial(battery, demand, config, 1.0)
        if not np.allclose(result.injection_kw, prior_commands, atol=1e-8):
            raise ValueError("Trial-9 dispatch is not identical to trial 7")
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
