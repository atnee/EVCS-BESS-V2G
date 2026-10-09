# IEEE 8500 nós em pandapower (redução de média tensão)

Alimentador maior que o IEEE123, usado pelos estudos do EVCS (S0, screening, S1) quando `configs/evcs.yaml` → `planning.network: ieee8500`. A integração S0–S4 com BESS e V2G continua no IEEE123.

Como no IEEE123, não há motor OpenDSS: os arquivos de texto públicos são usados **somente como fonte de dados**, normalizados para JSON por `scripts/build_ieee8500_data.py`.

## Proveniência e reprodução
- Fonte: https://github.com/tshort/OpenDSS/tree/5005c668a72d20775f4c2d060feebb2866ba1d38/Distrib/IEEETestCases/8500-Node (mesmo commit do IEEE123), caso de **carga equilibrada** (`Master.dss`).
- Obtenção: 09/10/2026. Originais em `source/`, licença EPRI BSD em `source/License.txt` (o repositório não traz licença na pasta do 8500; é a mesma licença da distribuição OpenDSS usada para o IEEE123).
- Dados de execução: `src/network/data/ieee8500.json`; SHA256 de cada fonte em `provenance.sha256`.
- Reconstrução offline: `python scripts/build_ieee8500_data.py` (inclui o ajuste dos taps, ~1 min).

## O que contém
| Item | Valor |
|---|---|
| Barras computacionais (MT + fonte 115 kV) | 2.521 |
| Linhas de média tensão | 2.483 (170 km) |
| Chaves | 43 (5 abertas) |
| Cargas | 1.177 consumidores, 10.773 kW / 2.700 kvar (fp 0,97) |
| Subestação | 115/12,47 kV, 27,5 MVA, x = 15,51 % |
| Reguladores | 4 bancos (FEEDER_REG na subestação, VREG2–4 na rede) |
| Capacitores | 4 bancos, 3.900 kvar |
| Extensão | ~11 × 12 km; maior distância elétrica da subestação 17 km |

Coordenadas em pés (mesma unidade do IEEE123). 63 barras de chaves sem coordenada no arquivo-fonte recebem a da barra vizinha mais próxima.

## ⚠️ Ajustes do estudo (diferem dos dados de referência IEEE)

Dois valores do caso de referência foram **alterados de propósito** para que o caso base S0 não comece com violações. Ambos estão em `scripts/build_ieee8500_data.py` (constantes `CONDUCTOR_AMPS`, `SOURCE_PU`, `VREG_PU`) e registrados no JSON em `provenance.adjustments`; os valores originais ficam em `vreg_reference_pu` de cada regulador.

| # | Item | Referência IEEE 8500 | Adotado no estudo | Motivo |
|---|---|---|---|---|
| 1 | Ampacidade das linhas | 400 A em todas (padrão do OpenDSS: as linecodes não definem `normamps`) | Capacidade típica do condutor de fase: 397 ACSR 587 A · 4/0 357 A · 2/0 276 A · 1/0 242 A · #2 184 A · #4 140 A · #6 105 A (WPAL pela mesma bitola). Cabos subterrâneos mantêm o `normamps` da fonte (220 A / 190 A). Conectores de comprimento zero recebem a corrente nominal da subestação (1.273 A). | Com 400 A o tronco 397 ACSR já ficava sobrecarregado no caso base (42 linhas > 100 %) e os ramais #4/#2 ficavam superestimados. Os valores são de catálogo (condutor a 75 °C, ambiente 25 °C, vento 0,6 m/s), **hipótese**, não dado IEEE. |
| 2 | Nível de tensão | Fonte 1,05 pu; reguladores `vreg` 126,5 V (1,054 pu, subestação) e 125 V (1,042 pu, VREG2–4) | Fonte 1,00 pu; todos os reguladores com `vreg` = 1,03 pu (banda mantida em 2 V) | A referência começa acima de 1,05 pu perto da subestação (476 barras > 1,05 pu no dia). Com o ajuste, o S0 fica entre 0,951 e 1,043 pu. |

Efeito no caso base S0 (dia com a curva de `configs/ieee8500.yaml`):

| | Referência | Com os ajustes |
|---|---|---|
| Tensão no dia | 0,971 – 1,060 pu | 0,951 – 1,043 pu |
| Barras > 1,05 pu | 476 | 0 |
| Linhas > 100 % na ponta | 42 | 1 (trecho de 1,6 m de #4 ACSR dentro de um tronco trifásico, 152 A; mantido como está nos dados) |
| Taps na ponta (subestação / VREG4 / VREG3 / VREG2) | 2 / 8 / 8 / 4 | 6 / 12 / 12 / 4 |

O alimentador continua carregado na ponta: o tronco sul em 2/0 ACSR fica em ~95 % e trechos do 397 ACSR acima de 90 %.

## Simplificações (além das do IEEE123)
| Componente | Representação |
|---|---|
| Rede secundária | Cada transformador de serviço monofásico (7,2 kV / 120-240 V), seu ramal triplex e a carga são substituídos por uma carga PQ na barra e fase primárias do transformador. Perdas e impedâncias do transformador e do secundário são desprezadas. |
| Reguladores | Bancos de três unidades monofásicas tratados como um tap comum (média das fases). O controle (`vreg`, banda de 2 V em 120 V) é emulado dentro do fluxo de potência: a cada passo, o banco mais a montante fora da faixa move até 4 taps e o fluxo é resolvido de novo; os taps passam de um passo de tempo para o seguinte. Sem atrasos de tempo nem compensação de queda na linha. Reatância de 0,1 % mantida. |
| Capacitores | Sempre ligados; o controle por kvar (`CapControl`) não é modelado. |
| Subestação | Delta-estrela representado por YNyn equivalente; fonte em 115 kV (1,00 pu, ver ajuste 2) com reatância de 14,8 Ω (reator do arquivo-fonte) como potência de curto-circuito. |
| Solver | Partida plana que falha com taps altos é refeita subindo os taps a partir do neutro, cada etapa partindo da solução anterior. |

Os taps gravados no JSON são os da carga nominal com os ajustes (FEEDER_REG +7, VREG4 +11, VREG3 +10, VREG2 +4); não há valores publicados para comparação. Sobrecargas que já existem no caso base são reportadas e excluídas do critério de capacidade da triagem.

Comparação com tensões e perdas publicadas do IEEE 8500 continua pendente; convergência não prova fidelidade.
