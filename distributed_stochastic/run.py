#!/usr/bin/env python3
"""V2.0 — triagem de localizações BESS/V2G e sondagem AC opcional no IEEE 8500.

Da raiz do NOVO projeto:
    python -m distributed_stochastic.run screen --baseline "<caminho para replay_s0_s4_opendss>"
    python -m distributed_stochastic.run probe --baseline "<mesmo caminho>" --hours 18
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from .candidates import ScreenSettings, write_screen


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="task",required=True)
    screen=sub.add_parser("screen",help="Triagem física em S0/S1 (não executa novo fluxo AC)")
    probe=sub.add_parser("probe",help="Sondagem AC local em barras selecionadas (requer OpenDSS)")
    for subp in (screen,probe):
        subp.add_argument("--repo",type=Path,default=Path.cwd())
        subp.add_argument("--baseline",type=Path,required=True,
                          help="Pasta com S0/ e S1/ do replay OpenDSS do projeto anterior")
        subp.add_argument("--output",type=Path,default=None)
    screen.add_argument("--bess-count",type=int,default=16)
    screen.add_argument("--v2g-count",type=int,default=16)
    screen.add_argument("--spacing-m",type=float,default=300.)
    screen.add_argument("--electrical-spacing-km",type=float,default=.10)
    screen.add_argument("--existing-v2g-only",action="store_true",
                        help="Somente barras de eletropostos existentes (ainda exigem upgrade bidirecional)")
    screen.add_argument("--no-anchors",action="store_true",help="Não reservar barras históricas")
    probe.add_argument("--screen-dir",type=Path,default=Path("results/v20_candidatos"))
    probe.add_argument("--hours",type=int,nargs="+",default=[18])
    probe.add_argument("--delta-kw",type=float,default=200.,
                       help="Compatibilidade V2.0: injeção positiva única se --probe-powers-kw ausente")
    probe.add_argument("--probe-powers-kw",type=float,nargs="+",default=None,
                       help="Perturbações assinadas, ex.: 200 -200 (sem SOC ou decisão de localização)")
    probe.add_argument("--limit-per-type",type=int,default=4)
    probe.add_argument("--save-derivatives",action="store_true")
    args=p.parse_args(argv)
    repo=args.repo.resolve()
    if args.output is None:
        args.output=repo / ("results/v20_candidatos" if args.task=="screen" else "results/v20_sondagem_ac")
    if args.task=="screen":
        cfg=ScreenSettings(bess_count=args.bess_count,v2g_count=args.v2g_count,
                           spacing_m=args.spacing_m,electrical_spacing_km=args.electrical_spacing_km,
                           include_anchor=not args.no_anchors,v2g_only_existing_evcs=args.existing_v2g_only)
        result=write_screen(repo,args.baseline,args.output,cfg)
        print("[V2.0] Triagem concluída. SEM otimização de localização ou validação AC dos novos sites.")
        print("Barras elegíveis:",result["eligible_buses"])
        print("Selecionadas:",result["selected"])
        print("Linhas críticas S1 vs S0:",result["new_line_count"])
    else:
        from .ac_probe import probe as ac_probe
        result=ac_probe(repo,args.baseline,args.screen_dir,args.output,tuple(args.hours),
                        args.delta_kw,args.limit_per_type,args.save_derivatives,args.probe_powers_kw)
        print("[V2.0] Sondagem AC concluída:",result["n_physical_probes"],"testes locais")
    print("Arquivos:",args.output.resolve())


if __name__=="__main__":
    main()
