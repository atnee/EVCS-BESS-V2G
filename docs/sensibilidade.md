# Sensibilidade do S0–S4

Análise de robustez do resultado do S4 (BESS + V2G), feita em 4–5 nov. 2026. São 29 casos, definidos **antes** de rodar em `configs/sensitivity.yaml`, e todos são reportados. Cada caso roda a integração S0–S4 inteira, com uma cópia de `configs/` mais as mudanças do caso; a configuração oficial não muda. Comando: `python -m integration.sensitivity`. Tabelas e figuras: `results/ieee8500/sensitivity/` (`RELATORIO.md`, `sensitivity.csv`, `sensitivity_s4_*.png`).

**Critério** (o mesmo do S4): nenhuma linha que estava até 100 % no S0 do próprio caso passa de 100 %, e a tensão mínima com os reguladores atuando fica em 0,95 pu ou mais. ANSI C84.1 (faixa A) e o limite de 100 % são referências do estudo, não a norma de Honduras.

**O que fica fixo em todos os casos:** as 75 estações públicas (alocação do S1 com 3.000 veículos), a barra da BESS (`l3029502`) e a do V2G (`m1069504`), e a calibração da estratégia `line_limit` feita no caso base. A calibração faz parte do método testado. A exceção é `resolution_15min_recal`, que recalibra a 15 min.

## Resumo

| | Resultados do S4 | Sem linha nova > 100 % | Tensão ≥ 0,95 pu | Atende ao critério |
|---|---|---|---|---|
| Todos os casos (base + 28) | 29 | 23 | 12 | 11 |
| Só as 10 sementes | 10 | 8 | 3 | 3 |

1. **Linhas: o resultado é robusto.** BESS + V2G, com os despachos propostos, eliminam as sobrecargas novas em 23 dos 29 casos, inclusive a 15 min, com ±10 % de carga e com BESS de 0,5× a 1,5×. As exceções têm causa identificada (abaixo).
2. **Tensão: o resultado é frágil.** O 0,951 pu do caso base (semente 42) ficou acima da média. Nas 10 sementes, a tensão mínima do S4 foi de 0,945 a 0,954 pu (média 0,9485, desvio-padrão 0,0028). A 15 min, 0,946 pu. Não se pode afirmar que o S4 garante 0,95 pu.
3. **A tensão depende dos reguladores, não do tamanho dos recursos.** Só mudanças no controle dos reguladores moveram a tensão de forma consistente; o tamanho da BESS não mudou nada.
4. **Os despachos propostos importam.** Com regras convencionais, o S4 perde o critério e, com o V2G heurístico, volta a ter sobrecargas.

## Resultados por grupo (S4)

### Despacho convencional

| Caso | O que muda | Linhas novas > 100 % | Tensão mínima (reguladores / taps do S0) |
|---|---|---|---|
| base | `line_limit` + `cesar_v1` | 0 | 0,951 / 0,932 pu |
| conv_bess_peak_shave | BESS corta a ponta até 11.750 kW (ponta do S0) | 0 | 0,949 / 0,930 pu |
| conv_v2g_heuristic | V2G descarrega tudo acima da reserva na janela | 8 (até 116,6 %) | 0,942 / 0,913 pu |
| conv_both | as duas regras convencionais | 47 (até 117,2 %) | 0,937 / 0,906 pu |

O LP do V2G (`cesar_v1`) é o que mais pesa. A heurística descarrega das 17 às 19 h e recarrega logo depois, 1,27 MW às 20 h e 0,66 MW às 21 h, ainda na ponta do alimentador. O `cesar_v1` espalha a recarga pela madrugada (222 kW constantes) e não recarrega das 17 às 22 h (`modules/v2g/S4/profile.csv` dos dois casos). A `peak_shave` resolve as linhas, mas fica 0,001 pu abaixo do limite de tensão.

### Sementes (sessões de recarga e frota V2G)

| | S1 | S2 | S3 | S4 |
|---|---|---|---|---|
| Atende (de 10) | 0 | 0 | 0 | 3 |
| Linhas novas > 100 % (mín–máx) | 49–53 | 8–12 | 21–29 | 0–6 |
| Tensão mínima (mín–máx) | 0,935–0,941 | 0,939–0,947 | 0,937–0,947 | 0,945–0,954 pu |

- Nenhuma semente faz o S2 ou o S3 atenderem sozinhos: a conclusão "só a combinação resolve" se mantém.
- No S4, as sementes 1 e 3 têm 6 e 3 linhas novas, no máximo a 101,3 % e 100,2 %. A calibração da BESS, feita com a semente 42, deixa pouco espaço nessas linhas.

### Reguladores (ajuste do estudo: `vreg` 1,03 pu, banda 2 V)

| Caso | Linhas novas > 100 % | Tensão mínima | Tensão máxima | Leitura |
|---|---|---|---|---|
| `vreg` 1,02 pu | 12 (até 100,8 %) | 0,930 pu | — | piora tudo |
| `vreg` 1,04 pu | 0 | 0,959 pu | **1,055 pu** | atende ao critério, mas passa de 1,05 pu em todos os cenários, inclusive no S0: **não é aceitável** |
| banda 1 V | 0 | **0,954 pu** | 1,043 pu | **atende, com margem, sem sobretensão** |
| banda 3 V | 0 | 0,950 pu | — | não atende (fica 0,0002 pu abaixo) |

O critério do estudo olha só a tensão mínima. O caso de 1,04 pu mostra que, se a mesma rodada for usada no artigo, é preciso citar também a máxima. A banda mais estreita (1 V) é a única mudança que dá margem de tensão sem criar sobretensão.

### Carga do alimentador

| Caso | Linhas novas > 100 % | Tensão mínima | Observação |
|---|---|---|---|
| carga × 0,90 | 1 (`ln6351510-1`, 118,4 %) | 0,950 pu | essa linha já estava a 108,7 % no S0 do caso base; com menos carga, fica em 96,4 % no S0 e passa a contar no critério. É efeito da definição, não uma sobrecarga nova das estações |
| carga × 1,10 | 0 | 0,937 pu | linhas resolvidas; tensão abaixo do limite |

### Dimensionamento (um parâmetro por vez)

| Caso | Linhas novas > 100 % | Tensão mínima | CAPEX total (USD) |
|---|---|---|---|
| BESS 1,1 MW / 3,6 MWh (× 0,50) | 0 | 0,951 pu | 5.600.400 |
| BESS 1,65 MW / 5,4 MWh (× 0,75) | 0 | 0,951 pu | 6.201.600 |
| **base: BESS 2,2 MW / 7,2 MWh** | 0 | 0,951 pu | 6.802.800 |
| BESS 2,75 MW / 9,0 MWh (× 1,25) | 0 | 0,951 pu | 7.404.000 |
| BESS 3,3 MW / 10,8 MWh (× 1,50) | 0 | 0,951 pu | 8.005.200 |
| V2G 50 carros | 0 | 0,944 pu | 6.302.800 |
| V2G 100 carros | 0 | 0,948 pu | 6.552.800 |
| **base: V2G 150 carros** | 0 | 0,951 pu | 6.802.800 |
| V2G 200 carros | 0 | 0,951 pu | 7.052.800 |
| V2G 250 carros | 0 | 0,948 pu | 7.302.800 |

- **A BESS está superdimensionada no caso base.** O despacho do S4 chega a 547 kW, abaixo até da potência de 1,1 MW. Com metade do tamanho, o resultado é idêntico e o CAPEX total cai 1,2 milhão de USD. Não foi testada a BESS menor com outras sementes, que pedem mais energia (throughput de até 3,7 MWh no dia, semente 6).
- **A tensão não melhora sempre com mais V2G.** Com 250 carros, fica pior que com 200, porque o efeito passa pelos taps dos reguladores.

### Resolução de 15 min

| Caso | Linhas novas > 100 % | Tensão mínima |
|---|---|---|
| 15 min, calibração horária da BESS | 0 | 0,946 / 0,934 pu |
| 15 min, BESS recalibrada a 15 min | 0 | 0,946 / 0,934 pu |

As pontas de 15 min não trazem sobrecargas novas no S4. A recalibração a 15 min (margens de 1,95 a 9,41 %, contra 1,94 a 6,21 % na horária) não muda nada: a calibração horária vale para as linhas, e a tensão não depende da BESS.

## O que isso muda no artigo

- **Pode afirmar:**
  - com os despachos propostos, BESS + V2G eliminam de forma robusta as sobrecargas de linha causadas pelas estações;
  - nem a BESS nem o V2G fazem isso sozinhos, em nenhuma das 10 sementes;
  - as regras convencionais não chegam a esse resultado.
- **Não pode afirmar:** que o S4 garante tensão ≥ 0,95 pu. Isso vale em 3 de 10 sementes e não vale a 15 min.
- **Pode propor:** coordenar o controle dos reguladores (banda de 1 V) como parte da solução. É uma mudança de operação, sem investimento, e no caso base dá 0,954 pu sem sobretensão. Não foi testada com as outras sementes nem a 15 min.
- **Deve rever:** o tamanho da BESS. Os resultados indicam 1,1 MW / 3,6 MWh, desde que confirmado com várias sementes.

## Execução e limitações

- **Execução:** 4 processos em paralelo, com uma linha de cálculo por processo; cada caso de 1 h levou ~20 min e o de 15 min ~75 min.
- **Dois casos travados:** `v2g_200` e `v2g_250` ficaram ~5 h sem terminar durante a noite, com só ~1 GB de memória livre. Foram parados e rodados de novo de manhã com as mesmas configurações, e terminaram em 18 min. Cada parte deles (frotas, LPs, fluxo do S1 e do S3) também rodou normalmente sozinha. A causa provável é o ambiente (memória), não o modelo; não foi confirmada.
- **Um parâmetro por vez:** não foram testadas combinações (por exemplo, banda de 1 V com outras sementes, ou BESS menor a 15 min).
- **Poucas repetições:** 10 sementes dão uma primeira ideia da dispersão, mas não um intervalo de confiança.
- **Tabelas compactas:** nos casos de 1 h, as séries brutas por barra e por linha foram apagadas depois de gerar as tabelas compactas (`dados/line_peaks.csv`, `dados/series.csv`, taps em `dados/S*_source.csv`), por falta de espaço em disco. Os casos de 15 min estão completos.
- **Mesmas limitações do S4:** um alimentador de referência adaptado (IEEE 8500 reduzido, componentes de sequência, taps comuns por banco, sem LDC), sem validação contra o OpenDSS e sem dados de Honduras além da tarifa.
