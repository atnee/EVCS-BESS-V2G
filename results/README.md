# Resultados

> **Comece por `ieee8500/resumo/`**: os 8 gráficos principais, numerados na ordem de leitura, e um `LEIA-ME.md` de uma página com a tabela S0 × S1 e a integração. Ele é atualizado sozinho ao fim do S1 e da integração (ou com `python -m integration.resumo`).

Tudo aqui é **gerado pelos comandos** e não vai para o Git (exceto `demo_reference/` e este arquivo). Em cada estudo, as **figuras e o `summary.json` ficam na pasta**, e as **tabelas CSV ficam na subpasta `dados/`**.

```
results/
├── ieee8500/                  alimentador de todos os estudos (configs/network.yaml)
│   ├── resumo/                ← COMECE AQUI: 8 gráficos numerados + LEIA-ME.md
│   ├── s0/                    caso base: network_topology, voltage_map_peak, voltage_profile_peak/valley,
│   │                          voltage_range_day, demand_substation_power_day, lines_impedance/capacitance
│   ├── evcs_screening/        hosting_capacity_map, stations_<n>_evs (onde ficam as estações de cada frota)
│   ├── s1/                    EVCS: S1 a cada 15 min
│   │   ├── overload_*.png             indicadores da rede × quantidade de carros (S0 = 0 carros)
│   │   ├── sensitivity_*.png          o dia de cada frota × S0
│   │   └── evs_<n>/           uma pasta por frota de <n> carros, um gráfico por arquivo:
│   │       ├── stations_map.png
│   │       ├── demand_*.png           demanda de EV e potência na subestação
│   │       ├── voltage_*.png          tensão ao longo do dia e por distância
│   │       ├── impact_*.png           variação de tensão S1 − S0, queda por passo, taps
│   │       ├── network_*.png          mapas de tensão e carregamento S0 e S1, equipamentos, faixas
│   │       └── sessions_*.png         chegadas, tempo de recarga, SOC, espera
│   └── integration/           integração S0–S4 com EVCS, BESS e V2G (coordenador)
│       ├── demand_substation_power_per_scenario.png
│       ├── S1/ S2/ S3/ S4/    impact_*.png e network_*.png de cada cenário × S0
│       ├── summary.csv, impact.csv    números por cenário
│       └── modules/<módulo>/<cenário>/   contribuição de cada módulo
├── modules/                   execução independente de cada módulo (python -m evcs | bess | v2g)
└── demo_reference/            resultados sintéticos históricos da versão inicial (versionado)
```

Todos os gráficos estão em inglês, um por arquivo. Para refazer só as figuras a partir dos resultados salvos (sem rodar fluxo de potência): `python -m integration.redraw` (~3 min).

| Para gerar | Comando (na raiz, com `PYTHONPATH=src`) | Tempo |
|---|---|---|
| `ieee8500/s0/` | `python -m integration.s0_study` | ~2 min |
| `ieee8500/evcs_screening/` | `python -m integration.evcs_screening` | 1ª vez ~1,5 h; depois segundos |
| `ieee8500/s1/` | `python -m integration.s1_study` | ~12 min por frota |
| `ieee8500/integration/` | `python -m integration.coordinator --output results/ieee8500/integration --frozen-taps` | ~15 min |
| `ieee8500/resumo/` | `python -m integration.resumo` (também roda sozinho ao fim do S1 e da integração) | segundos |
| `modules/` | `python -m evcs`, `python -m bess`, `python -m v2g` | segundos |

A pasta do alimentador segue `feeder` em `configs/network.yaml` (hoje `ieee8500`).
