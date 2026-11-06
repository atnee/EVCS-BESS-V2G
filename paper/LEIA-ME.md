# Material para o paper — EVCS, BESS e V2G no IEEE 8500

Retrato fixo dos arquivos necessários para escrever o artigo, para os coautores que não rodaram o estudo. Os dados do S4 vêm da rodada oficial de 4 nov. 2026 (`main`, `9a9e35f`); a sensibilidade, das rodadas de 4 a 6 nov. 2026 (`feature/sensibilidade`, `8c00a4a`). Tudo aqui é cópia: os documentos originais ficam em `docs/` e `src/`, e as tabelas de resultados, em `results/`, que não vai para o Git. **Esta pasta é a única exceção à regra de `results/` fora do Git** (decisão de 6 nov. 2026): não edite os arquivos à mão; se o estudo for rodado de novo, gere a pasta outra vez (veja *Como atualizar*).

**Regra:** todo número do artigo deve sair de um arquivo desta pasta, com a fonte citada. Hipóteses (frota, curva de carga, custos, zoneamento) devem aparecer como hipóteses. ANSI C84.1 e PRODIST Módulo 8 são referências do estudo, não a norma de Honduras.

## Por onde começar

1. `01_metodologia/metodologia.md`: modelo, formulação, variáveis, solvers e limitações.
2. `01_metodologia/S4_integracao.md`: decisões do S4, estratégia da BESS, resultados S0–S4 e a questão da margem de tensão.
3. `01_metodologia/sensibilidade.md`: **robustez do S4 em 29 casos** (5 nov. 2026). Leia antes de escrever as conclusões: as linhas são robustas, a tensão ≥ 0,95 pu não.
4. `tabelas_paper.xlsx`: as tabelas principais, uma por aba, com a aba `Indice` dizendo a fonte de cada uma.

## Conteúdo

### `01_metodologia/`: texto de base

| Arquivo | Conteúdo |
|---|---|
| `metodologia.md` | Metodologia do artigo: rede, alocação das estações (MILP), sessões de recarga, indicadores, limitações |
| `S4_integracao.md` | S4: decisões, por que essas barras, estratégia `line_limit`, resultados, margem de tensão e reguladores |
| `S4_equipo_es.md` | O mesmo resumo, em espanhol, para a Kenia e o Cesar |
| `S2_BESS_Kenia.md`, `S3_V2G_Cesar.md` | O que o S2 e o S3 deviam entregar (espanhol) |
| `modulo_evcs_README.md` | EVCS: frota mista, sessões, hubs e eletropostos, S1 de 15 min, transformadores de serviço |
| `modulo_bess_README.md` | BESS (Kenia): tentativas 1–9 e a nota do S4 (espanhol) |
| `modulo_v2g_README.md` | V2G (Cesar): frota, estratégia `cesar_v1`, tarifas, comparação de barras |
| `rede_ieee8500_README.md` | Dados do IEEE 8500, redução à média tensão e os dois ajustes feitos no estudo |
| `sensibilidade.md` | Sensibilidade do S0–S4: 29 casos, conclusões para o artigo e limitações |
| `sensibilidade_relatorio_gerado.md` | Tabelas geradas automaticamente pela rodada de sensibilidade |
| `validacao.md` | Testes, ambiente e o que ainda não foi validado |
| `dicionario_de_dados.md` | Variáveis e unidades das tabelas |

### `02_parametros_yaml/`: hipóteses e dados de entrada

Os YAML exatos da rodada (`evcs.yaml`, `bess.yaml`, `v2g.yaml`, `ieee8500.yaml`, `network.yaml`). Cada parâmetro tem um comentário dizendo se é dado (com fonte) ou hipótese. `manifest_integracao.json` registra as versões e os hashes dos dados e das configurações da rodada.

### `03_tabelas_csv/`: números

| Pasta | Arquivo | Conteúdo | Passo |
|---|---|---|---|
| `s0_rede/` | `inventario_*.csv`, `codigos_de_linha.csv` | Caso base: totais, reguladores, capacitores, transformador, cargas por fase, condutores | — |
| `s1_evcs/` | `frotas_e_estacoes.csv` | Por frota (1.000–3.000): sessões, hubs, eletropostos, potência instalada, CAPEX | — |
| | `sobrecarga_por_frota_15min.csv` | Indicadores da rede por frota (ponta, linha, tensão com e sem reguladores, perdas) | 15 min |
| | `s0_vs_s1_qualidade_3000.csv` | Qualidade de energia S0 × S1 (ANSI, PRODIST, desequilíbrio, fator de potência) | 15 min |
| | `s0_intradiario.csv`, `s1_intradiario_3000.csv` | Curvas do dia: subestação, tensão mínima, linha e equipamento | 15 min |
| | `potencia_estacoes_15min_3000.csv`, `sessoes_3000.csv` | Potência de cada estação e cada sessão de recarga | 15 min |
| | `transformadores_servico_3000.csv` | Uso dos 1.177 transformadores de serviço (hipótese: eletroposto monofásico no transformador do consumidor) | 15 min |
| | `capacidade_de_hospedagem.csv` | Capacidade de cada barra candidata e o que a limita | ponta |
| | `consumidores_zoneamento.csv` | Classe e região de cada consumidor (hipótese) | — |
| `s4_integracao/` | `resumo_cenarios.csv` | S0–S4: ponta, energia, perdas, tensão, carregamento, energia de EV, V2G, BESS, CAPEX | 1 h |
| | `criterio_de_sucesso.csv` | Linhas novas acima de 100 %, sobrecargas do S0, tensão (reguladores e taps travados), atende ou não | 1 h |
| | `impacto_vs_s0.csv` | Cada cenário contra o S0, com reguladores e com taps travados | 1 h |
| | `series_horarias_S0_S4.csv` | Por cenário e hora: subestação, tensão mínima (duas visões), linha mais carregada (todas e só as do critério), EV, V2G, BESS, taps | 1 h |
| | `bess_despacho_S2.csv`, `bess_despacho_S4.csv`, `bess_soc_*.csv` | Despacho e SOC da BESS | 1 h |
| | `bess_calibracao_linhas.csv` | Calibração da `line_limit`: a, b, s, R² e erro máximo de cada linha | 1 h |
| `sensibilidade/` | `sensibilidade_S0_S4.csv` | Os 29 casos (e o base), cenários S0–S4: critério, tensão nas duas visões, ponta, perdas, BESS, V2G, CAPEX | 1 h (15 min nos dois casos de resolução) |

**Atenção ao passo de tempo:** o S1 do estudo do EVCS usa 15 min, e a integração S0–S4 usa 1 h. Não misture os números das duas no mesmo indicador.

### `04_figuras/`: uma figura por arquivo, texto em inglês

| Prefixo | Conteúdo |
|---|---|
| `00_resumo_*` | As 8 figuras do resumo do S1: indicadores por frota, curvas do dia, mapa das estações, tensão e carregamento na ponta |
| `01`–`02` | Caso base: topologia e perfil de tensão na ponta |
| `03`–`04` | Capacidade de hospedagem e zoneamento com as estações |
| `05`–`06` | Transformadores de serviço com 3.000 veículos |
| `07` | Potência na subestação, S0–S4 |
| `08_*`, `09_*` | Carregamento das linhas e mapa de tensão na ponta, S1 a S4 |
| `10`–`11` | S4: taps dos reguladores e mapa da variação de tensão contra o S0 |
| `12`–`13` | Sensibilidade: linhas novas e tensão mínima do S4 em cada caso |

### `tabelas_paper.xlsx`

Uma aba por tabela: `S0_rede`, `S1_frotas`, `S1_sobrecarga`, `S0xS1_qualidade`, `S1_transformadores`, `S4_cenarios`, `S4_criterio`, `S4_impacto`, `S4_horario`, `BESS_calibracao`, `Sensibilidade`. A aba `Indice` traz o conteúdo e a fonte de cada uma. Os valores são os mesmos dos CSV, sem arredondamento.

## Resultado principal (para o resumo do artigo)

`criterio_de_sucesso.csv`, passo de 1 h:

| Cenário | Linhas novas > 100 % | Tensão mínima (reguladores / taps do S0) | Critério |
|---|---|---|---|
| S1 | 49 | 0,938 / 0,915 pu | não atende |
| S2 (BESS) | 8 | 0,940 / 0,928 pu | não atende |
| S3 (V2G) | 21 | 0,946 / 0,931 pu | não atende |
| S4 (BESS + V2G) | 0 | 0,951 / 0,932 pu | atende |

A margem de tensão do S4 é pequena por causa dos reguladores (ver `S4_integracao.md`): o artigo deve mostrar as duas visões.

**Sensibilidade** (`sensibilidade.md`): em 29 casos, 23 não têm linha nova acima de 100 % no S4, mas só 12 chegam a 0,95 pu (3 de 10 sementes; 0,946 pu a 15 min). Pode-se afirmar que BESS + V2G eliminam de forma robusta as sobrecargas de linha; não que garantem a tensão. Na confirmação com as 10 sementes, a banda de 1 V nos reguladores levou a tensão a 0,95 pu ou mais em 9 de 10, e a BESS de metade do tamanho (1,1 MW / 3,6 MWh) deu o mesmo resultado da inteira, com 1,2 milhão de USD a menos. A combinação das duas atende ao critério em 7 de 10 sementes (contra 3 de 10 na configuração atual), com ~22 % mais operações de tap; a 15 min fica em 0,949 pu.

## Como atualizar

Depois de rodar o estudo de novo no repositório (`python -m integration.coordinator --output results/ieee8500/integration --frozen-taps` e, se mudar o S1, `python -m integration.s1_study`), gere a pasta outra vez, na raiz do repositório:

```powershell
python paper/gerar_pasta_paper.py . paper
```

O script `gerar_pasta_paper.py` lê `results/` e os documentos do repositório e reescreve tudo aqui (~2 min; precisa do ambiente Python do projeto e dos `results/` da rodada). Depois, faça o commit da pasta inteira.
