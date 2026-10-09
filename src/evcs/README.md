# Módulo EVCS — eletropostos e hubs de recarga

Responsável: **Ayrton** · branch `feature/evcs-ayrton` · configuração `configs/evcs.yaml`

Este módulo modela a recarga pública de veículos elétricos no alimentador IEEE123. Ele faz três coisas:

1. Simula **sessões de recarga** minuto a minuto: cada carro tem uma bateria, chega com um SOC, espera na fila se preciso e carrega até uma meta.
2. **Dimensiona e aloca** dois tipos de estação, eletropostos AC e hubs DC, respeitando a capacidade da rede.
3. Gera as **curvas diárias e intradiárias** de demanda e o impacto na rede (cenário S1).

> Todos os valores de `planning:` no `evcs.yaml` são **hipóteses** até termos dados de Honduras. O algoritmo de otimização da alocação **ainda não foi definido**: a escolha de locais atual é uma triagem simples, que serve de referência para comparar com ele.

## Arquivos

| Arquivo | O que faz |
|---|---|
| `planning.py` | Frota, tipos de estação, sessões, fila, dimensionamento e escolha de locais por cobertura. Não depende da rede. |
| `strategies.py` | Estratégias de demanda para a integração S0–S4 (`fixed`, `template`). |
| `runner.py` | `python -m evcs` (execução independente) e `run_integrated` (contrato com o coordenador). |
| `model.py`, `candidates.py` | Perfil de demanda limitado pela estação; barras candidatas por fase. |
| `optimization.py` | Lugar reservado para a otimização (ainda `NotImplementedError`). |

Estudos que usam o módulo, em `src/integration/`:

- `evcs_screening.py`: capacidade da rede por barra × frota × cobertura.
- `s1_study.py`: S1 intradiário com as estações escolhidas.

## Como uma sessão de recarga é calculada

Para cada tipo de estação, sorteiam-se sessões até somar a energia pública diária daquele tipo:

```
energia pública/dia = carros × km/dia × kWh/km × parcela pública
energia do tipo     = energia pública × share do tipo           (AC 60 %, DC 40 %)
```

Cada sessão:

| Grandeza | Como sai |
|---|---|
| Bateria | Normal(`battery_kwh`, `battery_std_kwh`), mínimo 10 kWh |
| SOC de chegada | Normal(`arrival_soc_mean`, `arrival_soc_std`), entre 5 % e a meta − 5 % |
| Energia | bateria × (`target_soc` − SOC de chegada) |
| Potência de recarga | mín(potência do carregador, limite do carro): `max_ac_kw` no AC, `max_dc_kw` no DC |
| **Tempo de recarga** | **energia ÷ potência** (arredondado para cima, em minutos) |
| Potência na rede | potência de recarga ÷ `charger_efficiency` |
| Hora de chegada | sorteada com os 24 pesos de `arrival_shape` do tipo, minuto uniforme dentro da hora |

**Fila:** as sessões são distribuídas entre as estações do mesmo tipo. Se todos os carregadores estiverem ocupados, o carro espera. Se a espera passar de `max_wait_min`, ele desiste e a energia conta como **não atendida**. O dia é periódico: uma recarga que passa da meia-noite continua no início do dia.

**Dimensionamento:** os carregadores de cada tipo são o percentil `sizing_quantile` (95 %) do número de carros carregando ao mesmo tempo, sem limite de carregadores. O número de estações é esse total dividido por `chargers_per_site`, arredondado para cima.

**Escolha de locais (triagem):**

1. Primeiro os **hubs**, que exigem mais capacidade da rede; depois os **eletropostos**, nas barras restantes.
2. Para cada tipo, escolhe-se a barra que cobre mais demanda ainda descoberta dentro de `coverage_radius_m`.
3. A barra só entra se aceitar a potência instalada da estação (capacidade da rede) e se estiver a pelo menos `min_spacing_m` das outras do mesmo tipo.

A carga existente em cada barra é usada como indicador de onde os carros estão.

## Parâmetros (`configs/evcs.yaml` → `planning:`)

| Parâmetro | Valor atual | Significado |
|---|---|---|
| `resolution_min` | 15 | Passo do fluxo de potência intradiário (as sessões são simuladas por minuto) |
| `seed` | 42 | Semente: mesma semente → mesmas sessões |
| `charger_efficiency` | 0,95 | Rendimento do carregador |
| `sizing_quantile` | 0,95 | Percentil de simultaneidade usado para dimensionar carregadores |
| `fleet.evs` | 1000 | Quantidade de carros elétricos na área |
| `fleet.battery_kwh` | 60 | **Bateria média** (kWh úteis) |
| `fleet.battery_std_kwh` | 15 | Variação do tamanho das baterias |
| `fleet.daily_km` / `kwh_per_km` | 35 / 0,18 | Uso diário e consumo |
| `fleet.public_share` | 0,30 | Parcela da energia recarregada fora de casa |
| `fleet.arrival_soc_mean` / `_std` | 0,30 / 0,10 | SOC com que o carro chega à estação |
| `fleet.max_ac_kw` | 11 | Carregador de bordo: limita a recarga AC |
| `fleet.max_dc_kw` | 100 | Limite DC do carro: limita a recarga no hub |

| Tipo | Carregadores por estação | `share` | `target_soc` | `max_wait_min` | Raio / espaçamento |
|---|---|---|---|---|---|
| `ac` — **eletroposto** | 6 × 22 kW (132 kW) | 60 % | 90 % | 30 min | 500 m / 400 m |
| `dc` — **hub** | 4 × 150 kW (600 kW) | 40 % | 80 % | 15 min | 1500 m / 800 m |

`arrival_shape`:

- **Eletroposto:** chegadas de manhã (trabalho e compras) e no fim da tarde.
- **Hub:** picos de deslocamento às 8h e às 17h–19h.

## Comandos

```bash
python -m evcs                                    # perfil standalone (sem rede)
python -m integration.evcs_screening              # dimensiona e aloca para 100–3000 carros (~15 s;
                                                  #   reaproveita a varredura de capacidade salva)
python -m integration.evcs_screening --resweep    # refaz a varredura de capacidade da rede (~9 min)
python -m integration.s1_study                    # S1 a cada 15 min: frota do yaml + maior frota aceita (~2 min)
python -m integration.s1_study --evs 500 1500     # frotas escolhidas
python -m pytest tests/test_planning.py tests/test_evcs.py -q
```

## Saídas do S1 (`results/s1/evs_<n>/`)

| Arquivo | Conteúdo |
|---|---|
| `allocation.png` | Onde ficaram os hubs e os eletropostos, com o raio de cobertura |
| `curves_daily.png` | **Curva diária**: demanda por hora (hub × eletroposto) e potência na subestação S0 × S1 |
| `curves_intraday.png` | **Curva intradiária**: demanda a cada 1 min, 15 min e 1 h, e potência na subestação a cada 15 min |
| `voltage_intraday.png` | Tensão mínima do alimentador a cada 15 min (S0 × S1) e a queda causada em cada passo |
| `sessions.png` | Chegadas por hora, tempo de recarga, SOC de chegada e espera na fila |
| `delta_v_map.png`, `voltage_comparison.png`, `voltage_profile.png`, `voltage_envelope.png` | Queda de tensão por barra e perfis |
| `sessions.csv`, `station_power_1min.csv`, `station_power_15min.csv`, `station_power_hourly.csv` | Dados das sessões e curvas por estação |
| `summary.json` | Resumo numérico |

`results/` não vai para o Git: rode os comandos para gerar.

## Resultados atuais (com as hipóteses acima)

| | 1000 carros | 2000 carros |
|---|---|---|
| Estações | 1 hub (barra 1) + 3 eletropostos (63, 18, 79) | 1 hub (1) + 4 eletropostos (63, 18, 79, 450) |
| Sessões/dia | 32 AC (~3h15 cada) + 25 DC (~19 min) | 64 AC + 47 DC |
| Pico das estações: 1 min / 15 min / 1 h | 351 / 291 / 218 kW | 560 / 501 / 407 kW |
| Ponta da subestação S0 → S1 (15 min) | 3609 → 3827 kW | 3609 → 4066 kW |
| Maior queda de tensão | 0,21 % (barra 63, 19h45) | 0,32 % (barra 450, 19h30) |
| Tensão mínima | 0,997 pu | 0,996 pu |
| Recargas não atendidas | 0 | 8 % das AC (eletropostos lotados) |

- **O hub DC é o que mais pesa na rede.** Recargas de cerca de 19 minutos a até 105 kW por carro criam picos curtos.
- **A curva horária esconde esses picos:** 1 hora mostra 218 kW, enquanto 15 minutos mostram 291 kW e 1 minuto, 351 kW (caso de 1000 carros). Por isso o S1 roda a cada 15 minutos.
- **A tensão não é o limite.** O que limita é o **reg1** (regulador da subestação):
  - Com a demanda simulada de cada estação, a rede aceita até **2000 carros** (reg1 a 98 %).
  - Com toda a potência instalada ao mesmo tempo (pior caso teórico), já recusa a partir de **600 carros**.

## Limitações e próximos passos

- **Otimização ainda não definida:** é preciso combinar o que o algoritmo decide, o objetivo, as restrições e o método.
- **Um dia representativo só:** a simulação é de um único dia, com uma semente. Para resultados estatísticos, rodar várias sementes (Monte Carlo).
- **Distribuição das sessões:** as sessões são divididas entre as estações ao acaso, não pela distância do carro à estação.
- **Recarga DC simplificada:** a potência é constante até 80 %, sem a redução real perto do fim da recarga.
- **Resolução da integração:** só o estudo S1 do EVCS roda a cada 15 minutos. A integração S0–S4 com BESS e V2G continua horária. Mudar isso envolve os módulos da Kenia e do Cesar e precisa ser combinado com eles.
- **Limite das linhas:** o limite de 400 A é uma hipótese e define a capacidade no lado leste.
