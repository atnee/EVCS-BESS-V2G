# S4 — EVCS + BESS + V2G no IEEE 8500

S4 = S1 (frota mista e 75 estações públicas) + a BESS da Kenia + o V2G do Cesar, numa só rodada S0–S4 com passo de 1 h. Rodada oficial: `results/ieee8500/integration/` (4 nov. 2026). Resumo: `results/ieee8500/resumo/LEIA-ME.md`. Versão para a Kenia e o Cesar, em espanhol: [S4_equipo_es.md](S4_equipo_es.md).

## Decisões (4 nov. 2026, os três)

| # | Tema | Decisão | Onde está |
|---|---|---|---|
| 1 | Frota V2G | 150 carros **dentro** dos 3.000: 2.850 públicos + 150 V2G | `evcs.yaml` `integration.evs`; `v2g.yaml` `fleet:` |
| 2 | Estação anfitriã do V2G | barra `m1069504` (tronco sul), 154 portas de 11 kW, 1.694 kW, CAPEX 770.000 USD (hipótese de 5.000 USD por porta) | `evcs.yaml` `station:`; `v2g.yaml` `standalone.station` |
| 3 | V2G | estratégia `cesar_v1` (LP da frota), descarga 17–20 h, sem carga 17–22 h, saída com energia de chegada + viagem | `v2g.yaml`; `src/v2g/README.md` |
| 4 | BESS | 2.200 kW / 7.200 kWh em `l3029502` (escolha da Kenia), SOC inicial e final 0,30 | `bess.yaml` `battery:`, `connection:` |
| 5 | CAPEX da BESS | 334 USD/kWh × 7.200 kWh = 2.404.800 USD (NREL/TP-6A40-93281, 2025, sistema de 4 h, USD de 2024, custo dos EUA) | `bess.yaml` `battery.capex` |
| 6 | Despacho da BESS | estratégia `line_limit` (regra da linha da Kenia como LP, sem ajustes manuais) | `bess.yaml` `line_limit:`; `src/bess/strategies.py` |
| 7 | Critério de sucesso | nenhuma linha que estava até 100 % no S0 passa de 100 %, e tensão mínima ≥ 0,95 pu com os reguladores atuando; sobrecargas do S0 reportadas à parte | `integration.resumo.success_criterion` |

ANSI C84.1 (faixa A) e o limite de 100 % são referências do estudo; nenhuma é a norma de Honduras.

## Por que essas barras

Numa rede radial, uma injeção só alivia as linhas entre a subestação e a sua barra. Linhas acima de 100 % no S1 (estudo de 15 min, 3.000 veículos), por grupo:

| Grupo | Linhas | S1 | BESS em `l3029502` | V2G em `m1069504` |
|---|---|---|---|---|
| Tronco de saída, 397 ACSR, 0–2,2 km | 28 | até 108,9 % | alcança | alcança |
| Ramal #4 ACSR, 3,4–5,3 km (inclui `ln6351510-1`, já 108,4 % no S0) | 5 | até 133,2 % | alcança | não |
| Tronco sul, 2/0 ACSR, 8,4–8,7 km | 5 | até 101,6 % | não | alcança |

Só a combinação alcança os três grupos. A prova 9 da Kenia (BESS em `m1069504`) já mostrava o conflito: o sul melhorava e o ramal #4 subia para 129 %.

## Estratégia `line_limit` da BESS

O contrato da BESS (`bess.runner.run_integrated`) entrega só a demanda do alimentador. A estratégia modela cada linha monitorada `l` como:

- carga antes da BESS = `a_l + b_l × demanda` (%; demanda em kW);
- efeito da BESS = `− s_l × injeção` (% por kW; positivo descarrega).

Um LP (HiGHS, `scipy.optimize.milp`) mantém cada linha em `100 % − margem_l` em todas as horas, pagando muito pelo excesso e pouco pela energia ciclada, com a recarga preferindo a demanda mais baixa; o dia termina no SOC inicial. A recarga obedece às mesmas restrições, então não cria sobrecarga visível para o modelo.

**Calibração** (`python -m integration.bess_calibration results/ieee8500/s4_calibration`), numa rodada S0–S4 com estas configurações e o despacho fixo da prova 7 atuando no S2 e no S4:

- `a`, `b`: mínimos quadrados sobre as 48 horas do S1 e do S3; R² de 0,978 a 0,999;
- `s`: inclinação da mudança de carga S1→S2 e S3→S4 contra a injeção da BESS, com os taps travados no S0 (só a rede responde); 0,009 %/kW no tronco de saída, 0,033 %/kW no ramal #4 (a Kenia mediu 0,038 %/kW);
- linhas monitoradas: até 100 % no S0, acima de 90 % no S1 ou no S3, `s ≥ 0,001 %/kW` → 54 linhas;
- margem de cada linha = seu maior erro de ajuste (1,94 a 6,21 %).

Na rodada final, a carga real das linhas monitoradas ficou em no máximo 99,8 % (S2) e 98,4 % (S4). A pior linha passou 1,9 ponto do seu limite próprio (100 % − margem) no S2, ainda abaixo de 100 %: a margem cobriu o erro da reta.

## Resultados (`results/ieee8500/integration/`, passo de 1 h)

| Cenário | Ponta (kW) | Perdas (MWh/dia) | Linhas novas > 100 % | Maior carga nelas | Linha·h > 100 % | `ln6351510-1` (já sobrecarregada no S0) | Tensão mínima | Sem a ação dos reguladores | Critério |
|---|---|---|---|---|---|---|---|---|---|
| S0 | 11.783 | 14,4 | 0 | — | 0 | 108,7 % | 0,951 pu | 0,951 pu | atende |
| S1 | 14.252 | 17,9 | 49 | 110,2 % | 119 | 129,7 % | 0,938 pu | 0,915 pu | não atende |
| S2 | 12.766 | 17,8 | 8 | 107,0 % | 24 | 113,2 % | 0,940 pu | 0,928 pu | não atende |
| S3 | 13.345 | 17,7 | 21 | 103,5 % | 21 | 129,7 % | 0,946 pu | 0,931 pu | não atende |
| S4 | 12.700 | 17,7 | **0** | — | 0 | 117,7 % | **0,951 pu** | 0,932 pu | **atende** |

- **S2:** as 8 linhas que sobram são do tronco sul (`ln5835160-1` e vizinhas, até 107 %), fora do alcance da BESS (`s` = 0,0008 %/kW). No S1 e no S2 os 150 carros do V2G carregam sem controle em `m1069504`, o que sobrecarrega o sul.
- **S3:** o V2G resolve o sul, mas não basta para o tronco de saída (21 linhas, até 103,5 %).
- **S4:** cada recurso cobre o que o outro não alcança. A BESS cicla 2.193 kWh no dia (6.871 kWh no S2), porque o V2G já tira parte da ponta.
- O V2G devolve 1.360,7 kWh das 17 às 20 h no S3 e no S4.

## Margem de tensão e reguladores

O S4 atende com **0,951 pu**, apenas 0,001 pu acima do limite: 34 barras ficam abaixo de 0,9525 pu às 18 h, todas no fim do alimentador (13–16 km), depois do VREG3 (6,0 km) e do VREG2 (9,0 km). A margem pequena vem do controle dos reguladores, não da falta de BESS ou V2G:

| Pior barra (`l3065750`, 14,1 km), 18 h | Reguladores atuando | Taps travados no S0 | Taps VREG2 / VREG3 |
|---|---|---|---|
| S1 | 0,957 pu | 0,915 pu | 6 / 12 |
| S4 | 0,951 pu | 0,942 pu | 4 / 11 |

- Com os taps travados, BESS e V2G sobem essa barra em **0,027 pu** (0,915 → 0,942 pu): é o efeito físico das soluções.
- Com os reguladores atuando, a carga menor sobe a tensão na saída do VREG2, que **desce 2 taps** para voltar à sua faixa (1,03 pu ± 1 V em 120 V). No modelo, o regulador controla a tensão no próprio terminal, sem compensação de queda na linha (LDC), e por isso devolve parte do ganho no fim da linha.
- A BESS (`l3029502`, num ramal que sai antes do VREG3) e o V2G (`m1069504`, 0,3 km antes do VREG2) ficam **antes** do VREG2, que anula o que eles fazem pela tensão no fim do alimentador.

**Teste de ajuste fino (4 nov. 2026).** A estação do V2G foi movida para `m1047521` (9,57 km, trifásica): é a barra mais a jusante do trecho depois do VREG2 que é comum às 34 barras de tensão baixa, e continua abaixo das linhas do tronco sul. Num fluxo de potência do S4 com a mesma injeção, sem reotimizar a BESS, a tensão mínima foi de 0,9510 para **0,9532 pu** (+0,002), sem novas sobrecargas; com os taps travados, 0,9313 pu (sem ganho). As barras de tensão baixa se dividem em vários ramais depois de 9,6 km, então um só ponto reduz a queda apenas nos 0,57 km iniciais. O ganho ficou abaixo do centésimo de pu combinado como mínimo, e a estação **ficou em `m1069504`**. Pela mesma razão, potência reativa ou restrição de tensão na BESS não alcançam essas barras.

**Trabalho futuro:** modelar a compensação de queda na linha (LDC) dos reguladores em `src/network/`. É o que mais se aproxima da operação real e daria margem de tensão de verdade, mas muda todos os cenários, inclusive o S0.

## Limitações e cuidados

- **A tensão do S4 depende dos reguladores:** ver a seção anterior; o artigo deve mostrar as duas visões.
- **A `line_limit` não olha a tensão.** Na rodada de calibração, a prova 7 da Kenia, ajustada à mão também para a tensão, deu 0,949 pu no S2; a `line_limit` dá 0,940 pu.
- **Recalibrar quando o S1 mudar:** `a`, `b` e `s` valem para estas configurações (frota, estações, curva de carga). Rodar uma integração e `python -m integration.bess_calibration` de novo.
- **Passo de 1 h:** a média horária suaviza as pontas; o estudo do EVCS a 15 min mostra pontas maiores.
- **Hipóteses de custo:** CAPEX da BESS com custo dos EUA de 2024 e duração de 4 h (esta BESS tem 3,3 h); CAPEX da estação V2G por porta; tarifas do Cesar (CREE e BCH, estrutura de Moreira et al.).
- **Transformadores:** o da BESS (2,2 MW) e o da estação V2G (1,7 MW) não são modelados.
- Uma semente só para frota e sessões; IEEE 8500 sem validação contra o OpenDSS; nenhum dado de Honduras além da tarifa.

## Como reproduzir

```powershell
$env:PYTHONPATH="src"
python -m integration.coordinator --output results/ieee8500/s4_calibration --frozen-taps   # ~20 min, com strategy: trial7_schedule no bess.yaml
python -m integration.bess_calibration results/ieee8500/s4_calibration                     # segundos; escreve line_limit.yaml
python -m integration.coordinator --output results/ieee8500/integration --frozen-taps      # ~20 min, com strategy: line_limit
```
