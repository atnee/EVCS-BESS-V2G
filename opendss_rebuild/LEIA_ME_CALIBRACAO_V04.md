# V0.4 – Primeira calibração térmica BESS no OpenDSS

Esta atualização **não sobrescreve** `run.py`, `engine.py`, `configs/` ou dados existentes.
Adiciona `calibrar_bess_opendss.py`, que usa as saídas S0 e S1 já calculadas.

## Uso a partir da raiz `EVCS-BESS-V2G` (PowerShell)

```powershell
python .\opendss_rebuild\calibrar_bess_opendss.py --results .\results\s0_s1_opendss_24h --hours 18 --delta-kw 200 --output .\results\calibracao_bess_odss_h18
```

Se o teste das 18h funcionar, ampliar para 17–19h em uma **nova** pasta:

```powershell
python .\opendss_rebuild\calibrar_bess_opendss.py --results .\results\s0_s1_opendss_24h --hours 17 18 19 --delta-kw 200 --output .\results\calibracao_bess_odss_17_19
```

## Critério para interpretar a saída

- `baseline_replay_check.csv`: a reprodução **cronológica** do S1 original precisa coincidir com os CSVs salvos. Se divergir, o programa para antes da calibração.
- `resumo_sensibilidades.csv`: potência da fonte, tensões e quantidade de sobrecargas antes/depois de +200 kW de descarga em `l3029502`.
- `corredor_41_linhas.csv`: derivadas dos carregamentos por **linha e fase** das 41 linhas distintamente novas do S1.
- `sensibilidade_linhas_por_fase.csv`, `sensibilidade_tensoes_por_fase.csv`: detalhes por linha, fase e barra.
- `manifest.json`: parâmetros, hashes e advertência de que os resultados são **diagnósticos**, não solução otimizada.

Para `benefit_pp_per_kw`, valor positivo significa que **+1 kW de descarga** do BESS *reduziu* o carregamento da linha naquele ponto de operação. Valores negativos indicam aumento do carregamento. Essa é uma derivada local, e o mesmo recurso pode afetar fases distintas de modo diferente.

## Importante para o artigo

1. A calibração se refere a um **BESS trifásico injetando potência ativa equilibrada na média tensão**, sem transformador dedicado modelado.
2. O estado de taps/capacitores é determinado pelo S1 com controle automático, depois **congelado** durante a pequena perturbação; isso isola a sensibilidade local.
3. Essa perturbação não satisfaz por si mesma a dinâmica de SOC da bateria, não incorpora restrições de tensão ao LP e **não recalibra ainda o algoritmo `line_limit` da Kenia**.
4. As derivadas do carregamento `loading_pct` não bastam para inferir a viabilidade de S4: depois haverá de testar o despacho completo, SOC e operação dos reguladores.
5. Os valores não devem ser apresentados como desempenho de BESS ou V2G, nem publicados sem verificação.
