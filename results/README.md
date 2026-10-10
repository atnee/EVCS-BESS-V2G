# Resultados

> **Comece por `ieee8500/resumo/`**: as 4 figuras principais, numeradas na ordem de leitura, e um `LEIA-ME.md` de uma página com a tabela S0 × S1 e a integração. Ele é atualizado sozinho ao fim do S1 e da integração (ou com `python -m integration.resumo`).

Tudo aqui é **gerado pelos comandos** e não vai para o Git (exceto `demo_reference/` e este arquivo). Em cada estudo, as **figuras e o `summary.json` ficam na pasta**, e as **tabelas CSV ficam na subpasta `dados/`**.

```
results/
├── ieee8500/                  alimentador de todos os estudos (configs/network.yaml)
│   ├── resumo/                ← COMECE AQUI: 4 figuras + LEIA-ME.md
│   ├── s0/                    caso base: topologia, perfil de tensão, mapa de tensão
│   ├── evcs_screening/        EVCS: capacidade da rede por barra, alocação por frota (coverage_<n>_evs.png)
│   ├── s1/                    EVCS: S1 a cada 15 min
│   │   ├── intraday_sensitivity.png   o dia de cada frota × S0
│   │   ├── impact_sensitivity.png     o que as estações fazem com a tensão, por frota
│   │   └── evs_<n>/           uma pasta por frota de <n> carros (2000 a 5000)
│   │       ├── network_state.png      a rede no S0 e no S1 lado a lado
│   │       ├── impact_s0_s1.png       variação de tensão, com e sem a ação dos reguladores
│   │       └── (curvas do dia, sessões, alocação, summary.json)
│   └── integration/           integração S0–S4 com EVCS, BESS e V2G (coordenador)
│       ├── network_state_S1..S4.png   a rede no S0 e no cenário, lado a lado
│       ├── impact_S1..S4.png          cada cenário × S0
│       ├── summary.csv, impact.csv    números por cenário
│       └── modules/<módulo>/<cenário>/   contribuição de cada módulo
├── modules/                   execução independente de cada módulo (python -m evcs | bess | v2g)
├── notebook_demo/             saída do notebook 04 (integração S0–S4)
└── demo_reference/            resultados sintéticos históricos da versão inicial (versionado)
```

| Para gerar | Comando (na raiz, com `PYTHONPATH=src`) | Tempo |
|---|---|---|
| `ieee8500/s0/` | `python -m integration.s0_study` | ~2 min |
| `ieee8500/evcs_screening/` | `python -m integration.evcs_screening` | 1ª vez ~1,5 h; depois segundos |
| `ieee8500/s1/` | `python -m integration.s1_study` | ~12 min por frota |
| `ieee8500/integration/` | `python -m integration.coordinator --output results/ieee8500/integration --frozen-taps` | ~15 min |
| `ieee8500/resumo/` | `python -m integration.resumo` (também roda sozinho ao fim do S1 e da integração) | segundos |
| `modules/` | `python -m evcs`, `python -m bess`, `python -m v2g` | segundos |

A pasta do alimentador segue `feeder` em `configs/network.yaml` (hoje `ieee8500`).
