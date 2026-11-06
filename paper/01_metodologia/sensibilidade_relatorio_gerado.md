# Sensibilidade do S0–S4

Gerado por `python -m integration.sensitivity` em 2026-11-06 19:46. Casos definidos antes de rodar em `configs/sensitivity.yaml`; todos aparecem aqui. Critério: nenhuma linha que estava até 100 % no S0 do próprio caso passa de 100 %, e tensão mínima ≥ 0,95 pu com os reguladores atuando. Tabela completa (S0–S4 de cada caso): `sensitivity.csv`.

## Base

| Caso | Descrição | S4: linhas novas > 100 % | Maior carga | Linha·h | Tensão mínima | Taps do S0 | Ponta (kW) | Critério |
|---|---|---|---|---|---|---|---|---|
| base | official configs (results/<feeder>/integration) | 0 | — % | 0,0 | 0,951 pu | 0,932 pu | 12.700 | **atende** |

## Convencional

| Caso | Descrição | S4: linhas novas > 100 % | Maior carga | Linha·h | Tensão mínima | Taps do S0 | Ponta (kW) | Critério |
|---|---|---|---|---|---|---|---|---|
| conv_bess_peak_shave | BESS peak shaving to the S0 peak (11,750 kW) instead of line_limit | 0 | — % | 0,0 | 0,949 pu | 0,930 pu | 12.896 | não atende |
| conv_v2g_heuristic | V2G heuristic (discharge whatever is above the trip reserve in the window) instead of cesar_v1 | 8 | 116,6 % | 8,0 | 0,942 pu | 0,913 pu | 12.759 | não atende |
| conv_both | both conventional rules together | 47 | 117,2 % | 71,0 | 0,937 pu | 0,906 pu | 13.471 | não atende |

## Sementes

| Caso | Descrição | S4: linhas novas > 100 % | Maior carga | Linha·h | Tensão mínima | Taps do S0 | Ponta (kW) | Critério |
|---|---|---|---|---|---|---|---|---|
| seed_01 | seed 1 | 6 | 101,3 % | 6,0 | 0,947 pu | 0,930 pu | 12.701 | não atende |
| seed_02 | seed 2 | 0 | — % | 0,0 | 0,954 pu | 0,927 pu | 12.685 | **atende** |
| seed_03 | seed 3 | 3 | 100,2 % | 3,0 | 0,945 pu | 0,926 pu | 12.697 | não atende |
| seed_04 | seed 4 | 0 | — % | 0,0 | 0,947 pu | 0,928 pu | 12.705 | não atende |
| seed_05 | seed 5 | 0 | — % | 0,0 | 0,948 pu | 0,929 pu | 12.693 | não atende |
| seed_06 | seed 6 | 0 | — % | 0,0 | 0,947 pu | 0,930 pu | 12.702 | não atende |
| seed_07 | seed 7 | 0 | — % | 0,0 | 0,951 pu | 0,930 pu | 12.685 | **atende** |
| seed_08 | seed 8 | 0 | — % | 0,0 | 0,946 pu | 0,926 pu | 12.707 | não atende |
| seed_09 | seed 9 | 0 | — % | 0,0 | 0,952 pu | 0,933 pu | 12.679 | **atende** |
| seed_10 | seed 10 | 0 | — % | 0,0 | 0,947 pu | 0,928 pu | 12.697 | não atende |

## Reguladores

| Caso | Descrição | S4: linhas novas > 100 % | Maior carga | Linha·h | Tensão mínima | Taps do S0 | Ponta (kW) | Critério |
|---|---|---|---|---|---|---|---|---|
| reg_vreg_102 | vreg 1.02 pu | 12 | 100,8 % | 25,0 | 0,930 pu | 0,922 pu | 12.739 | não atende |
| reg_vreg_104 | vreg 1.04 pu | 0 | — % | 0,0 | 0,959 pu | 0,944 pu | 12.675 | **atende** |
| reg_band_1v | band 1 V (0.0083 pu) | 0 | — % | 0,0 | 0,954 pu | 0,939 pu | 12.697 | **atende** |
| reg_band_3v | band 3 V (0.025 pu) | 0 | — % | 0,0 | 0,950 pu | 0,929 pu | 12.696 | não atende |

## Carga

| Caso | Descrição | S4: linhas novas > 100 % | Maior carga | Linha·h | Tensão mínima | Taps do S0 | Ponta (kW) | Critério |
|---|---|---|---|---|---|---|---|---|
| load_090 | native load x 0.90 | 1 | 118,4 % | 5,0 | 0,950 pu | 0,940 pu | 12.044 | não atende |
| load_110 | native load x 1.10 (growth) | 0 | — % | 0,0 | 0,937 pu | 0,932 pu | 12.982 | não atende |

## Dimensionamento

| Caso | Descrição | S4: linhas novas > 100 % | Maior carga | Linha·h | Tensão mínima | Taps do S0 | Ponta (kW) | Critério |
|---|---|---|---|---|---|---|---|---|
| bess_050 | BESS 1.1 MW / 3.6 MWh (x0.50) | 0 | — % | 0,0 | 0,951 pu | 0,932 pu | 12.700 | **atende** |
| bess_075 | BESS 1.65 MW / 5.4 MWh (x0.75) | 0 | — % | 0,0 | 0,951 pu | 0,932 pu | 12.700 | **atende** |
| bess_125 | BESS 2.75 MW / 9.0 MWh (x1.25) | 0 | — % | 0,0 | 0,951 pu | 0,932 pu | 12.700 | **atende** |
| bess_150 | BESS 3.3 MW / 10.8 MWh (x1.50) | 0 | — % | 0,0 | 0,951 pu | 0,932 pu | 12.700 | **atende** |
| v2g_050 | 50 V2G cars (2950 public) | 0 | — % | 0,0 | 0,944 pu | 0,933 pu | 12.725 | não atende |
| v2g_100 | 100 V2G cars (2900 public) | 0 | — % | 0,0 | 0,948 pu | 0,932 pu | 12.700 | não atende |
| v2g_200 | 200 V2G cars (2800 public) | 0 | — % | 0,0 | 0,950 pu | 0,929 pu | 12.687 | **atende** |
| v2g_250 | 250 V2G cars (2750 public) | 0 | — % | 0,0 | 0,948 pu | 0,926 pu | 12.682 | não atende |

## Resolucao

| Caso | Descrição | S4: linhas novas > 100 % | Maior carga | Linha·h | Tensão mínima | Taps do S0 | Ponta (kW) | Critério |
|---|---|---|---|---|---|---|---|---|
| resolution_15min | S0-S4 at 15 min with the hourly BESS calibration | 0 | — % | 0,0 | 0,946 pu | 0,934 pu | 12.721 | não atende |
| resolution_15min_recal | S0-S4 at 15 min with the BESS recalibrated at 15 min | 0 | — % | 0,0 | 0,946 pu | 0,934 pu | 12.692 | não atende |

## Confirmacao

| Caso | Descrição | S4: linhas novas > 100 % | Maior carga | Linha·h | Tensão mínima | Taps do S0 | Ponta (kW) | Critério |
|---|---|---|---|---|---|---|---|---|
| band1v_seed_01 | band 1 V + seed 1 | 4 | 100,7 % | 4,0 | 0,950 pu | 0,936 pu | 12.698 | não atende |
| band1v_seed_02 | band 1 V + seed 2 | 0 | — % | 0,0 | 0,951 pu | 0,936 pu | 12.686 | **atende** |
| band1v_seed_03 | band 1 V + seed 3 | 1 | 100,1 % | 1,0 | 0,952 pu | 0,935 pu | 12.694 | não atende |
| band1v_seed_04 | band 1 V + seed 4 | 0 | — % | 0,0 | 0,953 pu | 0,936 pu | 12.691 | **atende** |
| band1v_seed_05 | band 1 V + seed 5 | 0 | — % | 0,0 | 0,950 pu | 0,937 pu | 12.692 | **atende** |
| band1v_seed_06 | band 1 V + seed 6 | 0 | — % | 0,0 | 0,952 pu | 0,939 pu | 12.694 | **atende** |
| band1v_seed_07 | band 1 V + seed 7 | 0 | — % | 0,0 | 0,951 pu | 0,937 pu | 12.685 | **atende** |
| band1v_seed_08 | band 1 V + seed 8 | 0 | — % | 0,0 | 0,951 pu | 0,935 pu | 12.695 | **atende** |
| band1v_seed_09 | band 1 V + seed 9 | 0 | — % | 0,0 | 0,951 pu | 0,939 pu | 12.677 | **atende** |
| band1v_seed_10 | band 1 V + seed 10 | 0 | — % | 0,0 | 0,949 pu | 0,934 pu | 12.695 | não atende |
| bess050_seed_01 | BESS x0.50 + seed 1 | 6 | 101,3 % | 6,0 | 0,947 pu | 0,930 pu | 12.701 | não atende |
| bess050_seed_02 | BESS x0.50 + seed 2 | 0 | — % | 0,0 | 0,954 pu | 0,927 pu | 12.685 | **atende** |
| bess050_seed_03 | BESS x0.50 + seed 3 | 3 | 100,2 % | 3,0 | 0,945 pu | 0,926 pu | 12.697 | não atende |
| bess050_seed_04 | BESS x0.50 + seed 4 | 0 | — % | 0,0 | 0,947 pu | 0,928 pu | 12.705 | não atende |
| bess050_seed_05 | BESS x0.50 + seed 5 | 0 | — % | 0,0 | 0,952 pu | 0,929 pu | 12.691 | **atende** |
| bess050_seed_06 | BESS x0.50 + seed 6 | 0 | — % | 0,0 | 0,947 pu | 0,930 pu | 12.702 | não atende |
| bess050_seed_07 | BESS x0.50 + seed 7 | 0 | — % | 0,0 | 0,951 pu | 0,930 pu | 12.685 | **atende** |
| bess050_seed_08 | BESS x0.50 + seed 8 | 0 | — % | 0,0 | 0,946 pu | 0,926 pu | 12.707 | não atende |
| bess050_seed_09 | BESS x0.50 + seed 9 | 0 | — % | 0,0 | 0,952 pu | 0,933 pu | 12.679 | **atende** |
| bess050_seed_10 | BESS x0.50 + seed 10 | 0 | — % | 0,0 | 0,947 pu | 0,928 pu | 12.697 | não atende |
| band1v_15min | band 1 V at 15 min (hourly BESS calibration) | 0 | — % | 0,0 | 0,949 pu | 0,933 pu | 12.706 | não atende |
| band1v_bess050 | band 1 V + BESS x0.50 (seed 42) | 0 | — % | 0,0 | 0,954 pu | 0,939 pu | 12.697 | **atende** |

## Combinada

| Caso | Descrição | S4: linhas novas > 100 % | Maior carga | Linha·h | Tensão mínima | Taps do S0 | Ponta (kW) | Critério |
|---|---|---|---|---|---|---|---|---|
| combo_seed_01 | BESS x0.50 + 1 V band + seed 1 | 4 | 100,7 % | 4,0 | 0,950 pu | 0,936 pu | 12.698 | não atende |
| combo_seed_02 | BESS x0.50 + 1 V band + seed 2 | 0 | — % | 0,0 | 0,951 pu | 0,936 pu | 12.686 | **atende** |
| combo_seed_03 | BESS x0.50 + 1 V band + seed 3 | 1 | 100,1 % | 1,0 | 0,952 pu | 0,935 pu | 12.694 | não atende |
| combo_seed_04 | BESS x0.50 + 1 V band + seed 4 | 0 | — % | 0,0 | 0,953 pu | 0,936 pu | 12.691 | **atende** |
| combo_seed_05 | BESS x0.50 + 1 V band + seed 5 | 0 | — % | 0,0 | 0,950 pu | 0,937 pu | 12.692 | **atende** |
| combo_seed_06 | BESS x0.50 + 1 V band + seed 6 | 0 | — % | 0,0 | 0,952 pu | 0,939 pu | 12.694 | **atende** |
| combo_seed_07 | BESS x0.50 + 1 V band + seed 7 | 0 | — % | 0,0 | 0,951 pu | 0,937 pu | 12.685 | **atende** |
| combo_seed_08 | BESS x0.50 + 1 V band + seed 8 | 0 | — % | 0,0 | 0,951 pu | 0,935 pu | 12.695 | **atende** |
| combo_seed_09 | BESS x0.50 + 1 V band + seed 9 | 0 | — % | 0,0 | 0,951 pu | 0,939 pu | 12.677 | **atende** |
| combo_seed_10 | BESS x0.50 + 1 V band + seed 10 | 0 | — % | 0,0 | 0,949 pu | 0,934 pu | 12.695 | não atende |
| combo_15min | BESS x0.50 + 1 V band at 15 min (hourly BESS calibration) | 0 | — % | 0,0 | 0,949 pu | 0,933 pu | 12.706 | não atende |

## Sementes: todos os cenários

| Cenário | Atende (casos) | Linhas novas > 100 % (mín–máx) | Tensão mínima (mín–máx) |
|---|---|---|---|
| S0 | 10 de 10 | 0–0 | 0,951–0,951 pu |
| S1 | 0 de 10 | 49–53 | 0,935–0,941 pu |
| S2 | 0 de 10 | 8–12 | 0,939–0,947 pu |
| S3 | 0 de 10 | 21–29 | 0,937–0,947 pu |
| S4 | 3 de 10 | 0–6 | 0,945–0,954 pu |

## Operações de tap

Soma, nos 4 bancos, dos degraus de tap movidos no dia (`integration.impact.tap_operations`). Cada linha junta os casos que só diferem do base pela semente: média (mínimo–máximo). O passo de 15 min tem 4 vezes mais passos, então só se compara com o próprio passo.

| Banda | Passo | BESS | Casos | S0 | S1 | S2 | S3 | S4 |
|---|---|---|---|---|---|---|---|---|
| 1 V | 15 min | × 0,50 | 1 | 75 | 108 | 105 | 106 | 124 |
| 1 V | 15 min | × 1,00 | 1 | 75 | 108 | 140 | 106 | 122 |
| 1 V | 60 min | × 0,50 | 11 | 54,0 (54–54) | 66,5 (63–70) | 63,7 (59–68) | 58,5 (56–61) | 60,3 (58–64) |
| 1 V | 60 min | × 1,00 | 11 | 54,0 (54–54) | 66,5 (63–70) | 67,5 (60–73) | 58,5 (56–61) | 63,0 (59–66) |
| 2 V | 15 min | × 1,00 | 1 | 56 | 80 | 88 | 68 | 76 |
| 2 V | 60 min | × 0,50 | 11 | 45,0 (45–45) | 57,4 (52–65) | 52,5 (48–64) | 48,9 (43–57) | 47,9 (41–55) |
| 2 V | 60 min | × 0,75 | 1 | 45 | 58 | 52 | 49 | 47 |
| 2 V | 60 min | × 1,00 | 11 | 45,0 (45–45) | 57,4 (52–65) | 56,0 (50–66) | 48,9 (43–57) | 49,3 (43–57) |
| 2 V | 60 min | × 1,25 | 1 | 45 | 58 | 52 | 49 | 47 |
| 2 V | 60 min | × 1,50 | 1 | 45 | 58 | 52 | 49 | 47 |
| 3 V | 60 min | × 1,00 | 1 | 41 | 52 | 52 | 42 | 50 |

## Figuras

- `sensitivity_s4_new_overloads.png`: linhas novas acima de 100 % no S4, por caso.
- `sensitivity_s4_min_voltage.png`: tensão mínima do S4 por caso, com os reguladores atuando e com os taps do S0.
