# Módulo V2G — frota *vehicle-to-grid* (S3)

Responsável: **Cesar** · branch `feature/v2g-cesar` · configuração `configs/v2g.yaml` · notebook `notebooks/03_v2g_cesar.ipynb`

O **S3** é o S1 com V2G: uma frota de carros conectados à estação anfitriã devolve energia na ponta do alimentador **IEEE 8500** e recarrega fora dela, sem comprometer a próxima viagem de nenhum carro. A **V2G-B** é uma **variante de comparação aprovada pelo time** (Ayrton e Kenia), com a mesma frota e a mesma estratégia, mas com a estação anfitriã mais a jusante, na barra `m1069504`. A estação oficial do S3 continua em `m1125976`.

> ⚠️ **O S3 ainda não é o caso oficial do repositório.** Em `configs/` continuam valendo `strategy: heuristic`, `vehicle_source: vehicles`, `departure_rule: arrival` e `discharge_window_h: [17, 21]`, e `configs/evcs.yaml` não muda. O S3 roda com uma pasta de configuração **gerada** por `python -m v2g.s3_configs` (seção *Como reproduzir*). Com `configs/`, os resultados S0–S4 dos colegas ficam idênticos aos de `origin/feature/v2g-cesar`.

## Arquivos

| Arquivo | O que faz |
|---|---|
| `model.py` | `Vehicle` (reserva de viagem, alvo de saída) e a heurística `schedule`. |
| `availability.py` | Sessões, inclusive as noturnas (dia cíclico: chega à tarde, sai de manhã). |
| `fleet.py` | Sorteia a frota do bloco `fleet:` (semente fixa: mesma frota sempre). |
| `strategies.py` | Estratégias `heuristic`, `template` e `cesar_v1`, e `fleet_profile`, que valida toda matriz de potências. |
| `runner.py` | `python -m v2g` (módulo sozinho, sem rede) e `run_integrated` (contrato com o coordenador). Não importa `report.py` nem `s3_configs.py`. |
| `s3_configs.py` | `python -m v2g.s3_configs`: gera a pasta de configuração do S3 (A) e da V2G-B a partir de `configs/` + bloco `s3_case:`. |
| `report.py` | `python -m v2g.report`: figuras e tabelas S0/S1/S3 de uma integração, economia do dono e, com `--compare`, a comparação das duas barras. |

## Decisões e hipóteses do S3

| # | Tema | Valor | Tipo e origem |
|---|---|---|---|
| 1 | Passo de tempo | 1 h (24 passos) | acordo do time (recomendação de Ayrton e Kenia) |
| 2 | Frota V2G | 150 carros, 80 % residenciais e 20 % comerciais, **parte dos 3.000** do S1: os públicos caem para 2.850 (`integration.evs`) | acordo dos três |
| 3 | Estação anfitriã (A) | barra `m1125976`, 154 portas de 11 kW, 1.694 kW de conexão, capex 770.000 USD | acordo dos três; capex = hipótese de 5.000 USD por porta |
| 4 | Janela de descarga | 17–20 h (descarga às 17, 18 e 19 h) | indicação de Ayrton |
| 5 | Janela sem carga | 17–22 h | decisão de Cesar |
| 6 | Piso de descarga | **reserva da viagem** (nunca até `soc_min`) | metodologia §11.0 |
| 7 | Regra de saída | `arrival_plus_trip`: sai com a energia de chegada + viagem | decisão de Cesar |
| 8 | Chegada e saída | horas inteiras; residenciais chegam 17–19 h e saem 6–8 h; comerciais chegam 14–16 h e saem 23–24 h | hipótese |
| 9 | SOC de chegada | uniforme por carro em `[0,1; 0,9 − E_viagem/B]` | hipótese do time (fecha o dia cíclico) |
| 10 | Bateria e viagem | 44,9 ± 15 kWh, 40 km/dia × 0,18 kWh/km = 7,2 kWh (classe *private* do `evcs.yaml`) | hipótese, igual ao S1 |
| 11 | Tarifas | seção *Tarifas* | dado (CREE, BCH) + estrutura de Moreira et al. + decisão de Cesar |
| 12 | V2G-B | mesma configuração do S3 com a estação em `m1069504` | variante de comparação aprovada pelo time; a estação oficial do S3 continua em `m1125976` |
| 13 | Critério de sucesso | contra o S1; linhas já acima de 100 % no S0 não contam; linhas fora do caminho elétrico da estação são reportadas como "não alcançáveis" | acordo |

Todos os valores estão em `configs/v2g.yaml` (blocos `fleet:`, `cesar_v1:`, `tariff_basis:` e `s3_case:`), cada um com comentário de origem.

## Estratégia `cesar_v1`

Programação linear da frota (metodologia §11.2, com a binária δ relaxada como permite a §11.4; HiGHS via `scipy.optimize.milp`):

- **Objetivo:** custo da carga − receita do V2G, com a tarifa dinâmica, mais um desempate pequeno (`tie_break_usd_kw`) que achata a recarga total e reparte a descarga na janela.
- **Restrições:** energia de cada carro na ordem da sessão (`E_{k+1} = E_k + η_c·c·Δt − d·Δt/η_d`), limites de SOC, sem carga na janela sem carga nem em passo com descarga permitida, descarga só na janela, energia ≥ reserva da viagem desde a primeira descarga, saída ≥ alvo.
- **Participação:** um carro participa se consegue chegar acima da reserva antes da primeira hora de descarga; os outros só carregam.
- **S1 (`enable_v2g=False`):** cada carro carrega assim que chega, sem tarifa nem janela (carga sem controle).
- `fleet_profile` valida o resultado (conexão, potência, SOC, reserva, portas, alvo de saída) e não é contornado.

## Tarifas

| Horário | Carga (USD/kWh) | Descarga (USD/kWh) |
|---|---|---|
| 00–14 h e 22–24 h | 0,2421 (1×) | 0,2421 (1×) |
| 14–17 h | 0,4841 (2×) | 0,2421 |
| 17–20 h | 2,4207 (10×) | 0,6052 (2,5×) |
| 20–22 h | 2,4207 (10×) | 0,2421 |

- **Base:** L 6,5102/kWh, tarifa residencial "siguientes kWh" (consumo > 50 kWh/mês), pliego tarifário da CREE do IV trimestre de 2026, ÷ 26,8936 HNL/USD (taxa de referência do BCH em 20-out-2026) = 0,24207 USD/kWh.
- **Estrutura:** multiplicadores adaptados de Moreira et al., *Vehicle to Grid for Demand Response Optimization: A Case Study*, Fig. 5. O artigo paga 5× pela descarga; aqui é 2,5× (decisão de Cesar: 5× é alto demais para Honduras).
- **S1** é cobrado com a tarifa base **plana** o dia todo: a carga sem controle é justamente a ausência de sinal de preço.
- **Mês** = 22 dias (Moreira et al., eq. (5)–(6)). A degradação da bateria não é cobrada (`degradation_usd_kwh: 0.0`, como no artigo).
- `demand_value_hnl_kw_month` (L 340,4170/kW-mês, CREE, Serviço em Média Tensão, potência) só dá um valor **indicativo** ao corte da ponta; não é uma cobrança.
- Os testes reconstroem os vetores de `cesar_v1:` a partir de `tariff_basis:` e conferem a política de preços.

## Como reproduzir

Na raiz do repositório, com `$env:PYTHONPATH="src"` no PowerShell (ou o pacote instalado):

```powershell
# 1. Pastas de configuração do S3 (A) e da V2G-B (segundos); S3_CHANGES.md lista cada mudança
python -m v2g.s3_configs --out results\configs_s3_final\A
python -m v2g.s3_configs --out results\configs_s3_final\B --variant B

# 2. Módulo sozinho, sem rede (segundos)
python -m v2g --configs results\configs_s3_final\A --output results\modules_s3_final

# 3. Integração S0–S4 na rede, uma depois da outra (~15–20 min cada)
python -m integration.coordinator --configs results\configs_s3_final\A --output results\ieee8500\integration_s3_final_A --frozen-taps
python -m integration.coordinator --configs results\configs_s3_final\B --output results\ieee8500\integration_s3_final_B --frozen-taps

# 4. Relatórios (minutos): <pasta>\v2g\ com figuras, v2g_s0_s1_s3.csv, v2g_economics.csv e v2g_tariff.png
python -m v2g.report results\ieee8500\integration_s3_final_A --configs results\configs_s3_final\A
python -m v2g.report results\ieee8500\integration_s3_final_B --configs results\configs_s3_final\B

# 5. Comparação das barras (minutos)
python -m v2g.report --compare results\ieee8500\integration_s3_final_A results\ieee8500\integration_s3_final_B --configs-a results\configs_s3_final\A --configs-b results\configs_s3_final\B --output results\ieee8500\s3_final_bus_comparison
```

`v2g.report` confere o `manifest.json` da integração contra os YAML passados em `--configs` e para se não forem os da corrida. Não use a pasta `results\ieee8500\integration`, que é a oficial.

### O que `--compare` calcula

- **Grupos de linhas pela topologia** (`network.topology.feeder_graph`, só elementos fechados; os 5 interruptores abertos do IEEE 8500 criariam laços falsos): `trunk_out` = caminho da subestação até `m1125976`; `trunk_south` = caminho de `m1125976` até `m1069504`; `other` = o resto. Lista em `s3_line_groups.csv`.
- `s3_bus_comparison.csv`: S0, S1_A, S3_A, S1_B, S3_B e as diferenças `S3_A − S1_A`, `S3_B − S1_A` (as duas barras contra a mesma referência), `S3_B − S3_A` (efeito de mover a estação) e `S1_B − S1_A` (os 150 carros carregando sem controle em `m1069504`), nas duas visões (reguladores atuando e taps travados no S0).
- `s3_success.csv`: critério de sucesso (decisão 13). Grupos alcançáveis: `trunk_out` em A; `trunk_out` e `trunk_south` em B.
- `s3_new_overloads_outside_window.csv`: linhas-hora acima de 100 % no S3 e não no S1, fora de 17–20 h (pré-carga e recarga noturna).
- Figuras `v2g_bus_*.png`.

## Resultados (corrida de 30-out-2026)

Números de `results/ieee8500/s3_final_bus_comparison/s3_bus_comparison.csv` e `s3_success.csv`, visão com reguladores atuando (a visão com taps travados está no mesmo arquivo). Regenere com os comandos acima.

| | S0 | S1_A | S3_A | S1_B | S3_B |
|---|---|---|---|---|---|
| Ponta da subestação (kW, 18 h) | 11.782,9 | 14.230,9 | 13.390,4 | 14.252,2 | 13.345,3 |
| Carregamento máx. `trunk_out` (%) | 91,7 | 110,6 | 104,7 | 110,2 | 103,5 |
| Carregamento máx. `trunk_south` (%) | 96,4 | 100,4 | 101,1 | 107,1 | 96,2 |
| Tensão mínima (pu) | 0,9515 | 0,9393 | 0,9370 | 0,9381 | 0,9459 |
| Consumidores < 0,95 pu | 0 | 50 | 68 | 59 | 2 |
| Perdas (kWh/dia) | 14.377,7 | 17.845,1 | 17.753,0 | 17.943,2 | 17.734,1 |

- A frota entrega 1.360,7 kWh nas 17–19 h (~453,6 kW por hora), igual em A e B.
- **Nem A nem B eliminam a sobrecarga do tronco de saída** (`trunk_out`): ainda há 21 linhas acima de 100 %, até 104,7 % em A e 103,5 % em B. O V2G de 150 carros não basta; ver `s3_success.csv` para o excesso e as horas.
- Só a V2G-B alivia o tronco sul (`trunk_south` cumpre em B). A partir de `m1125976` ele não é alcançável.
- Nenhuma sobrecarga nova fora de 17–20 h, em A nem em B.
- Economia do dono (`v2g/v2g_economics.csv`, igual em A e B): o S1 custa 275,20 USD/dia (tarifa plana); o S3 tem custo líquido de −79,93 USD/dia (dinâmica); benefício de 355,13 USD/dia, 7.812,82 USD/mês, 52,09 USD por carro e mês.

## Para o S4

- Use a **pasta A** (`python -m v2g.s3_configs --out ...`) como o S3 oficial: estação em `m1125976`.
- A **V2G-B** (`--variant B`) é a alternativa a combinar com a BESS em `m1125976`: o V2G alivia o tronco sul e a BESS, o tronco de saída.
- **As linhas S2 e S4 das integrações acima não valem:** usam a BESS de `configs/bess.yaml` (120 kWh), não a da Kenia.
- Para o S3 virar o caso oficial: (1) Ayrton muda `station:` e `integration.evs` em `configs/evcs.yaml`; (2) Cesar muda os valores em vigor de `configs/v2g.yaml`; (3) Kenia roda de novo o S2; (4) o time decide se a estação fica em `m1125976` ou vai para `m1069504`.

## Limitações

- **Passo de 1 h:** a média horária suaviza as pontas; o estudo do EVCS a 15 min mostra pontas maiores.
- SOC de chegada uniforme, com teto por carro; horários e frota são hipóteses.
- O multiplicador de descarga de 2,5× é hipótese; a degradação da bateria não é cobrada.
- 39 dos 150 carros não participam (chegam sem energia acima da reserva antes da janela; `participating_vehicles` em `dados/S3_fleet.json`).
- O transformador da estação não é modelado.
- Os 2.850 públicos incluem táxis, mas a frota V2G não tem táxis.
- A V2G-B é uma variante de comparação (aprovada pelo time): a estação oficial do S3 continua em `m1125976`.
- Com reguladores atuando, o S3_A tem mais consumidores abaixo de 0,95 pu que o S1_A (68 contra 50); com taps travados, menos (241 contra 390). Os reguladores escondem parte do efeito.
- Nenhum limite usado (ANSI C84.1 faixa A, 100 % de carregamento) é a norma de Honduras.
