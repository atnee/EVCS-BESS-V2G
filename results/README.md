# Resultados

Tudo aqui é **gerado pelos comandos** e não vai para o Git (exceto `demo_reference/` e este arquivo). Cada estudo grava em `results/<alimentador>/<estudo>/` quando roda sem `--output`.

```
results/
├── ieee8500/                  alimentador de todos os estudos (configs/network.yaml)
│   ├── integration/           integração S0–S4 com EVCS, BESS e V2G (coordenador)
│   │   ├── impact_S1..S4.png, impact.csv   cada cenário × S0 (com --frozen-taps: também com os taps do S0)
│   │   └── modules/<módulo>/<cenário>/   contribuição de cada módulo
│   ├── s0/                    caso base S0: topologia, perfil de tensão, linhas, summary.json
│   ├── evcs_screening/        EVCS: capacidade da rede por barra, alocação por frota
│   └── s1/                    EVCS: S1 a cada 15 min
│       ├── intraday_sensitivity.png   ← todas as frotas × S0 (comece por aqui)
│       ├── impact_sensitivity.png     ← o que as estações fazem com a tensão, por frota
│       ├── s0_intraday.csv
│       └── evs_<n>/           uma pasta por frota (impact_s0_s1.png, curvas, tensão, sessões, summary.json)
├── modules/                   execução independente de cada módulo (python -m evcs | bess | v2g)
├── notebook_demo/             saída do notebook 04 (integração S0–S4)
└── demo_reference/            resultados sintéticos históricos da versão inicial (versionado)
```

| Para gerar | Comando (na raiz, com `PYTHONPATH=src`) |
|---|---|
| `ieee8500/s0/` | `python -m integration.s0_study` |
| `ieee8500/evcs_screening/` | `python -m integration.evcs_screening` |
| `ieee8500/s1/` | `python -m integration.s1_study` |
| `ieee8500/integration/` | `python -m integration.coordinator --output results/ieee8500/integration --frozen-taps` |
| `modules/` | `python -m evcs`, `python -m bess`, `python -m v2g` |

A pasta do alimentador segue `feeder` em `configs/network.yaml` (hoje `ieee8500`).
