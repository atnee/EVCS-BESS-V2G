# Resultados

Tudo aqui é **gerado pelos comandos** e não vai para o Git (exceto `demo_reference/` e este arquivo). Cada estudo grava em `results/<alimentador>/<estudo>/` quando roda sem `--output`.

```
results/
├── ieee8500/                  EVCS no IEEE 8500 (Ayrton)
│   ├── s0/                    caso base S0: topologia, perfil de tensão, linhas, summary.json
│   ├── evcs_screening/        triagem: capacidade da rede por barra, alocação por frota
│   └── s1/                    S1 a cada 15 min
│       ├── intraday_sensitivity.png   ← todas as frotas × S0 (comece por aqui)
│       ├── s0_intraday.csv
│       └── evs_<n>/           uma pasta por frota (curvas, tensão, sessões, summary.json)
├── ieee123/                   integração S0–S4 com BESS e V2G (coordenador)
├── modules/                   execução independente de cada módulo (python -m evcs | bess | v2g)
├── notebook_demo/             saída do notebook 04 (integração)
└── demo_reference/            resultados sintéticos históricos da versão inicial (versionado)
```

| Para gerar | Comando (na raiz, com `PYTHONPATH=src`) |
|---|---|
| `ieee8500/s0/` | `python -m integration.s0_study` |
| `ieee8500/evcs_screening/` | `python -m integration.evcs_screening` |
| `ieee8500/s1/` | `python -m integration.s1_study` |
| `ieee123/` | `python -m integration.coordinator --output results/ieee123` |
| `modules/` | `python -m evcs`, `python -m bess`, `python -m v2g` |

A pasta do EVCS segue `planning.network` em `configs/evcs.yaml` (hoje `ieee8500`).
