# Revisão das Figuras 1 e 10 — Applied Energy Rev09

**Manuscrito de referência:** `EVCS_BESS_V2G_Applied_Energy_Rev09_Model_Dependent_No_New_Simulations.docx`, em `~/Downloads`, SHA-256 `7cc5da65…6576f`. O arquivo com sufixo "(1)" citado no pedido não existe; foi usado o único Rev09 encontrado.
**Data:** 11 nov. 2026
**Pasta de trabalho:** `Rev09_Fig01_Fig10_revision/`. As figuras originais foram preservadas sem alteração em `original_rev09_figures/`, extraídas do Rev09. O Word não foi modificado: estava aberto (havia arquivo de bloqueio `~$…docx`) e as mudanças de texto ficam aqui como proposta.

**Resumo:**
- Nenhuma otimização, análise estocástica ou simulação de cenário novo foi executada.
- Três séries horárias da Fig. 10 são arquivos armazenados, idênticos byte a byte aos do manifesto da auditoria de 9 nov. 2026 (S0, S1 e S4 distribuído com política de fase C).
- Quatro séries horárias (S4 histórico, S4 distribuído nativo, S2 e S3 distribuídos) não existiam nesta cópia. Com autorização do autor, elas foram **regeneradas pela reexecução dos replays OpenDSS arquivados** (seção 2.4): mesmas injeções salvas, verificadas por hash, mesmo código do motor e as mesmas versões do OpenDSS da auditoria.
- As séries reexecutadas só foram aceitas depois de três casos de controle reproduzirem os arquivos guardados e de cada caso novo reproduzir o resumo auditado de 24 h. Nenhum número das Tabelas 13 e 14 mudou.

---

## 1. Entregáveis

| Arquivo | Conteúdo |
|---|---|
| `figures/Fig01_Methodological_Framework.{svg,pdf,png}` | Fig. 1 revisada (190 mm de largura) |
| `figures/Fig10_OpenDSS_Comparative_Results.{svg,pdf,png}` | Fig. 10 revisada, painéis (a)–(f) |
| `scripts/make_fig01.py` | Gera a Fig. 1 (esquema, sem dados numéricos calculados) |
| `superseded/` | Versões anteriores da Fig. 1 (v1, v2, v3) e seus scripts |
| `scripts/prepare_fig10_data.py` | Localiza, verifica por SHA-256 e deriva os dados da Fig. 10; não executa fluxo de potência |
| `scripts/make_fig10.py` | Gera a Fig. 10 a partir de `data/` |
| `scripts/reproduce_fig10_hourly.py` | Reexecuta os replays OpenDSS arquivados que faltavam (seção 2.4); sem otimização |
| `reproduced_opendss/` | Saídas reexecutadas (7 casos) e `manifest.json` com ambiente, hashes de entrada, regra de aceitação e resultados |
| `reproduce_fig10_hourly.log` | Log da execução aceita |
| `data/fig10_hourly_traces.csv` | Valores horários efetivamente plotados em (a)–(c) |
| `data/fig10_summary_metrics.csv` | Métricas de 24 h plotadas em (d)–(f) |
| `data/fig10_provenance.json` | Origem, hash, status e conferência de cada caso |
| `original_rev09_figures/` | Figuras 1 e 10 originais do Rev09 |

**Formatos:**
- **PDF:** uma página, com Arial incorporada como TrueType subconjunto (FontFile2) e sem fontes Type 3.
- **SVG:** todo o texto convertido em contornos (`svg.fonttype = path`), sem dependência de fonte instalada.
- **PNG:** 600 dpi. A Fig. 1 tem 4488 × 3144 px; a Fig. 10 tem 4488 × 4860 px.

---

## 2. Auditoria das fontes

### 2.1 O que foi encontrado

| Fonte | Local | Uso |
|---|---|---|
| Métricas de 24 h, S0–S4 distribuídos, controles nativos e política C | `validation_applied_energy_final/distributed_s2_s3/comparison.csv` | Fig. 10(d)–(f). Coincide com a Tabela 13 em todas as casas impressas |
| Métricas de 24 h do S4 histórico reproduzido no OpenDSS | `validation_applied_energy_final/exact_profile_pilot/summary.csv` | Fig. 10(d)–(f), barra/ponto "Hist. S4 replay". Coincide com a Tabela 14 |
| Séries horárias S0 e S1 (OpenDSS completo, controles nativos) | `EVCS-BESS-V2G/results/replay_s0_s4_opendss.rar` → `S0/`, `S1/` | Fig. 10(a)–(c). **Idênticas byte a byte** aos arquivos `exact_profile_pilot/S0_auto` e `S1_auto` do manifesto |
| Série horária S4 distribuído, política de fase C salva | `EVCS-BESS-V2G-Distributed-Stochastic/results/v301_continuacao_ac_segura.rar` → `best/AC/` | Fig. 10(a)–(c). **Idêntica byte a byte** a `alternative_controls/distributed_saved_policy`; injeção com o mesmo hash (`61cf9d7d…`) registrado pela auditoria |
| Definições das grandezas horárias | `validation_applied_energy_final/build_paper_assets.py` (`hourly_case`) | Reimplementadas sem mudança em `prepare_fig10_data.py` |
| Hashes de referência | `validation_applied_energy_final/paper_insertions/manifest.json` | Critério de aceitação das séries |
| Fluxograma, quantidades e termos | Texto do Rev09, Seções 2.1–2.7, Tabelas 1–3 e 13–15 | Fig. 1 |

### 2.2 O que **não** estava disponível como arquivo

As pastas abaixo existem, mas estão **vazias** nesta cópia local. Não há arquivo compactado delas: todos os `.rar` do projeto foram examinados e são anteriores à auditoria de 9 nov.

| Série horária | Pasta esperada (manifesto) | Solução |
|---|---|---|
| S4 histórico, replay exato, controles nativos | `exact_profile_pilot/S4_auto/` | Reexecutada e verificada (2.4) |
| S4 distribuído, controles nativos | `alternative_controls/distributed_native/` | Reexecutada e verificada (2.4) |
| S2 distribuído (BESS), controles nativos | `distributed_s2_s3/native_S2_auto/` | Reexecutada e verificada (2.4) |
| S3 distribuído (V2G), controles nativos | `distributed_s2_s3/native_S3_auto/` | Reexecutada e verificada (2.4) |
| Pontos traçados da Fig. 10 original | `paper_insertions/figure_data/fig10_hourly_profiles.csv` | Não reutilizados |

Nenhuma série foi interpolada nem digitalizada a partir da imagem original.

### 2.3 Série rejeitada: S4 histórico do replay de 8 nov.

O arquivo `replay_s0_s4_opendss.rar` contém uma série horária do S4 histórico, mas ela **não** é a série auditada.

Ela usa o substituto legado da BESS. Seu SHA-256 difere do manifesto, e o carregamento máximo de 24 h é 120,18%, contra 120,80% na Tabela 14. O número total de linhas novas (23) e a Vmin (0,92357 p.u.) coincidem, mas a trajetória horária de linhas novas às 17–20 h é 18/23/13/4. Na Fig. 10 original (série exata) ela é ≈21/23/14/14.

Por isso a série foi rejeitada. O script só a aceita com `--allow-predecessor`, e mesmo assim o painel não a desenha. A série exata, reexecutada na seção 2.4, dá 21/23/14/14, como a Fig. 10 original.

### 2.4 Reexecução verificada dos replays arquivados

**O que foi feito.** Os quatro casos da seção 2.2 foram regenerados reexecutando, sem nenhuma mudança, os replays OpenDSS que a auditoria rodou:
- `exact_profile_pilot` para o S4 histórico;
- `alternative_controls/distributed_native` para o S4 distribuído;
- `distributed_s2_s3` para S2 e S3.

Não houve otimização, sorteio nem ajuste de parâmetro: cada caso aplica injeções horárias já salvas no alimentador IEEE 8500 completo e resolve 24 fluxos de potência. O script `reproduce_fig10_hourly.py` usa o laço por hora de `check_alternatives.run` e grava apenas em `reproduced_opendss/`. Cada caso leva cerca de 2 s.

**Ambiente.**

| Item | Auditoria (9 nov.) | Reexecução (11 nov.) |
|---|---|---|
| Sistema | Windows, x86-64 | macOS 26.4.1, arm64 |
| Python | 3.13.5 | 3.12.14 (ambiente isolado `.venv_repro`) |
| DSS C-API / DSS-Python / OpenDSSDirect.py | 0.14.5 / 0.15.7 / 0.9.4 | 0.14.5 / 0.15.7 / 0.9.4 (revisão `87d85c26`) |
| numpy / pandas | 2.3.3 / 2.3.3 | 2.3.3 / 2.3.3 |
| `rebuild/engine.py`, `contracts.py`, `coordenar_controles_v24.py` | — | SHA-256 idêntico ao inventário da auditoria |

**Entradas, todas somente leitura e conferidas por hash.**
- **Injeções históricas S0, S1 e S4:** extraídas de `EVCS-BESS-V2G.rar` → `results/ieee8500/integration/dados/`. O SHA-256 é idêntico ao de `exact_profile_pilot/manifest.json`.
- **Injeções do S4 distribuído e política de fase C:** `injections_reoptimized.csv` e `control_policy_v25.json`, extraídos de `v301_continuacao_ac_segura.rar` → `best/`. O SHA-256 é idêntico ao de `distributed_s2_s3/manifest.json`.
- **Perfis S2 e S3 distribuídos:** `distributed_s2_s3/S{1..4}_injections.csv`, gravados pela auditoria sem hash. Foram conferidos estruturalmente:
  - S2 + S3 − S1 = S4 salvo, com diferença máxima de 8,5×10⁻¹⁴ kW;
  - o S4 do CSV é igual ao S4 salvo.
- **Alimentador e configurações:** os `.dss` do IEEE 8500 e os YAMLs vêm do projeto original. O `ieee8500.json` vem de `snapshot_fontes.zip` da auditoria. Todos foram copiados para um espelho no diretório temporário e têm hash idêntico ao inventário (`02_inventario_modelos.csv`). O OpenDSS muda o diretório de trabalho ao compilar, e o espelho evita escrever nas pastas originais.

**Regra de aceitação e o que aconteceu.**
1. **Primeira tentativa, com tolerância absoluta de 10⁻⁹ em todas as colunas: falhou** no controle S0. As diferenças em relação ao arquivo guardado eram 3×10⁻¹¹ p.u. na tensão, 1,8×10⁻⁷ A na corrente e 1,3×10⁻⁶ kvar na potência da fonte. Os taps e os conjuntos de linhas sobrecarregadas eram idênticos, e duas execuções na mesma máquina saíram idênticas bit a bit. A diferença é de arredondamento entre plataformas, cerca de 10⁻¹¹ em termos relativos. A execução foi interrompida antes de qualquer caso novo e o resultado foi levado ao autor.
2. **Regra adotada com o acordo do autor e registrada em `reproduced_opendss/manifest.json`:**
   - **Controles** (S0 e S1 históricos e S4 distribuído com política de fase C, cujas saídas guardadas existem): taps idênticos; conjunto de linhas >100% idêntico; erros máximos ≤ 10⁻⁹ p.u. (tensão), 10⁻⁵ % e 10⁻⁵ A (carregamento e corrente) e 10⁻⁴ kW/kvar (fonte).
   - **Casos novos:**
     - pico, perdas, Vmin e carregamento máximo de 24 h com erro relativo ≤ 10⁻⁸ em relação ao resumo auditado;
     - contagens de linhas sobrecarregadas e de linhas novas idênticas;
     - distância mínima aos limites de 100% e 0,95 p.u. maior que 100 vezes a tolerância, para que nenhuma classificação dependa do arredondamento.
   - Qualquer falha interrompe a execução.
3. **Resultado: os 7 casos foram aceitos** (`COMPLETE_ALL_ACCEPTED`), e as entradas continuaram inalteradas.

| Caso | Referência | Maior erro | Linhas >100% / novas | Menor distância a 100% / 0,95 p.u. |
|---|---|---|---|---|
| Controle S0 nativo | arquivos guardados | 3,2×10⁻¹¹ p.u.; 1,4×10⁻⁸ % | idênticas | — |
| Controle S1 nativo | arquivos guardados | 3,6×10⁻¹¹ p.u.; 1,9×10⁻⁸ % | idênticas | — |
| Controle S4 distribuído, política de fase C | arquivos guardados | 7,0×10⁻¹¹ p.u.; 1,6×10⁻⁸ % | idênticas | — |
| S4 histórico nativo | `exact_profile_pilot/summary.csv` | 2,1×10⁻¹¹ (relativo) | 33 / 23 | 8,0×10⁻³ % / 1,5×10⁻⁴ p.u. |
| S4 distribuído nativo | `comparison.csv`, `native_S4_auto` | 1,0×10⁻¹¹ | 13 / 0 | 9,4×10⁻² % / 9,8×10⁻⁷ p.u. |
| S2 distribuído nativo | `comparison.csv`, `native_S2_auto` | 6,3×10⁻¹¹ | 13 / 0 | 7,5×10⁻² % / 6,3×10⁻⁵ p.u. |
| S3 distribuído nativo | `comparison.csv`, `native_S3_auto` | 3,2×10⁻¹¹ | 45 / 32 | 3,9×10⁻² % / 3,5×10⁻⁷ p.u. |

A menor distância a um limite (3,5×10⁻⁷ p.u., no S3) é cerca de 5.000 vezes maior que o maior erro de tensão medido nos controles (7×10⁻¹¹ p.u.).

**Status no `fig10_provenance.json`.** Esses quatro casos aparecem como `reproduced_verified`. `prepare_fig10_data.py` só os aceita se o manifesto da reexecução estiver `COMPLETE_ALL_ACCEPTED`, se o caso tiver sido aceito e se os hashes dos arquivos coincidirem com o manifesto. Em seguida o script confere de novo cada caso contra o resumo auditado de 24 h.

**Limite.** As séries reexecutadas não são idênticas byte a byte às da auditoria, porque diferem em cerca de 10⁻¹¹ em termos relativos. Elas são numericamente equivalentes dentro da regra acima. Para obter os arquivos idênticos, basta copiar as quatro pastas da máquina Windows (seção 7); os scripts dão prioridade a elas automaticamente.

---

## 3. Figura 1 — mudanças

**Problema da versão Rev09:** a figura mostrava apenas o fluxo histórico em pandapower. Faltavam a reaplicação no OpenDSS, a comparação S0 e a configuração distribuída.

**Histórico das versões** (todas guardadas em `superseded/`):
- `Fig01_blocks_v1`: três blocos verticais. Descartada a pedido do autor por perda de qualidade.
- `Fig01_v2_dense`: estilo da original, 28 blocos. O autor a achou sobrecarregada.
- `Fig01_v3_28blocks`: os mesmos 28 blocos com textos mais curtos. O autor ainda achou que havia blocos demais.
- **Versão final (v4):** 18 blocos e 133 mm de altura (a v2 tinha 155 mm).

**Linguagem visual mantida da Fig. 1 original:**
- legenda de categorias por função no topo;
- painéis cinza com cadeias horizontais de etapas coloridas;
- caixas de entrada à esquerda;
- Δt e T no canto de cada painel;
- laço "no: resize" no planejamento.

**Como os blocos foram reduzidos**, sem tirar etapa metodológica:

| Parte | v2/v3 | Final | Fusões |
|---|---:|---:|---|
| Entradas pandapower | 3 | 2 | curva de carga entra na caixa do alimentador |
| (a) Planejamento EVCS | 6 | 4 | "Sessions and sizing"; indicadores entram no fluxo de potência |
| (b) Cenários integrados | 6 | 5 | definição dos cenários entra no fluxo de potência; calibração a, b, s entra em "calibrated line-limit LP"; campanha de sensibilidade (63 configurações) no cabeçalho do painel |
| Faixa auxiliar | 3 | 1 | resta só a comparação S0 pareada (diagnóstico) |
| (c) Diagnóstico de transferência | 3 | 2 | recursos históricos → replay OpenDSS e indicadores |
| (d) Configuração distribuída | 4 | 2 | recursos distribuídos (3 BESS, 3 hospedeiros V2G) → replay OpenDSS e indicadores |
| Alimentador OpenDSS, avaliação, conclusão | 3 | 3 | — |

Os detalhes retirados da figura continuam no texto do Rev09 e nas Tabelas 1–3 e 15:
- identificadores l3029502 e m1069504;
- divisão 67/67/16;
- raio de 500 m;
- horários 03:00/18:00 e 3.820 pares;
- 315 resultados da sensibilidade;
- 54 linhas calibradas.

**Conteúdo de cada parte:**
- **Entradas:** IEEE 8500 em pandapower (2.521 barras MT; curva de carga de 24 h) e frota de 3.000 EV (85% particulares, 15% táxi/aplicativo).
- **(a) EVCS planning and impact study (S0, S1) — pandapower, Δt = 15 min:** sessões e dimensionamento (Monte Carlo com resolução de 1 min; quantil de 95% da simultaneidade) → localização (DC: cobertura máxima; AC: MILP) → operação (filas FIFO; ≥95% das sessões AC atendidas) → fluxo de potência (duas visões de regulador; tensão, carregamento, perdas, taps). Detalhes conferidos em `configs/evcs.yaml`: `resolution_min`, `sizing_quantile`, `min_served_share`.
- **(b) Integrated scenarios S0–S4 — pandapower, Δt = 1 h:** demanda EVCS (2.850 EV, 75 locais) → despacho V2G (LP, 150 EV, exportação 17–20 h) → despacho da BESS (2,2 MW / 7,2 MWh; LP de limite de linha calibrado) → fluxo de potência (S0 base; S1–S4 = EVCS ± BESS ± V2G; duas visões) → critério (nenhuma sobrecarga nova; Vmin ≥ 0,95 p.u.).
- **S0 paired comparison (diagnostic):** pandapower comparado com o equivalente MT do OpenDSS, com taps fixos. Liga-se ao fluxo de potência de (b) por seta tracejada dupla.
- **(c) Model-transfer diagnostic — historical single-site resources:** recursos históricos (um local de BESS, um hospedeiro V2G com 150 EV) → replay OpenDSS e indicadores (taps nativos e congelados no S0; sobrecargas novas vs. pré-existentes; faixa de tensão).
- **(d) Distributed BESS–V2G configuration:** recursos distribuídos (3 BESS somando 2,2 MW / 7,2 MWh; 3 hospedeiros V2G com 150 EV) → replay OpenDSS e indicadores (S2, S3, S4; controles nativos e política de fase C; benefício incremental, violações residuais).
- **Comparative assessment:** seis itens, conforme pedido, e o quadro final **"Model-conditional findings, not feeder-wide feasibility certification"**.
- **Sem marcas de versão:** nenhum rótulo interno de versão aparece na figura.
- **Legenda:** a categoria "OpenDSS" (verde-azulado) e os dois tipos de seta ("Data flow", "Comparison only").

"ZIP loads" foi trocado por "voltage-dependent loads", termo do Rev09 (§2.1), e depois resumido em "24-h load curve". A sensibilidade passou de "62 cases" para as 63 configurações do Rev09.

**Conexões:**
- **Setas sólidas (fluxo de dados real):**
  - cadeias de etapas dentro de cada painel;
  - entradas para os painéis;
  - laço "no: resize", uma iteração real do dimensionamento;
  - injeções S0–S4 salvas, do fluxo de potência de (b) para o replay de (c), sem rótulo na figura (fica na legenda: "saved injections replayed without re-optimization");
  - S0 e S1 nativos compartilhados, do replay de (c) para o de (d), também sem rótulo. O `manifest.json` de `distributed_s2_s3` usa as mesmas S0/S1 nativas;
  - saídas do OpenDSS e do diagnóstico S0 para a avaliação comparativa, por um barramento à direita.
- **Setas tracejadas (somente comparação ou diagnóstico):**
  - fluxo de potência do pandapower ↔ comparação S0 pareada;
  - replay de (c) ↔ replay de (d): "configuration comparison (siting and dispatch differ)".
- Na figura só restam dois rótulos cinza: "no: resize", herdado da original, e "configuration comparison (siting and dispatch differ)", que impede ler a comparação como efeito isolado da localização.
- Nenhuma seta vai do pandapower para uma "confirmação" no OpenDSS.
- A expressão "validated model" não aparece.

**Correção factual incorporada:** o RMSE de 0,07277 p.u. (18:00, 3.820 pares) vem do modo `mt_equivalente` da auditoria (`04_validacao_s0.csv`, `03_diagnostico_eletrico.md`). Nesse modo o OpenDSS é um **equivalente MT**: secundários removidos, taps conjuntos, YNyn, capacitores ligados. Não é o modelo completo com secundários; no modo `completo` o RMSE às 18:00 é 0,06208 p.u. A caixa da comparação S0 diz exatamente isso: "vs OpenDSS MV equivalent, fixed taps". Ver a seção 6, item 3.

**Legenda proposta:**

> *Fig. 1. Overview of the reduced-model screening, cross-representation diagnostic, and full-feeder BESS–V2G operating comparisons. Solid arrows denote data flow (saved S0–S4 injections are replayed in OpenDSS without re-optimization, and the native S0 and S1 cases are shared by both OpenDSS stages); dashed arrows denote comparisons or diagnostics that do not imply confirmation. Historical and distributed schedules represent distinct experimental configurations, and their comparison does not isolate independent siting effects.*

---

## 4. Figura 10 — mudanças

### Parte A — Comportamento horário, painéis (a)–(c)

Os três painéis foram mantidos:
- **(a) Substation active-power input:** em MW. O original estava em kW; a conversão por 10⁻³ é a única transformação.
- **(b) Newly overloaded lines:** linhas acima de 100% na hora que nunca estavam sobrecarregadas no S0 nativo. É a mesma definição do audit, com 13 linhas pré-existentes.
- **(c) Minimum MV physical-phase voltage:** em p.u., com linha pontilhada discreta de 0,95 p.u. Os arquivos de tensão contêm apenas as 2.521 barras primárias (3.820 fases físicas por hora), portanto "MV" é o rótulo correto.

**Séries desenhadas (as mesmas cinco configurações da Fig. 10 original):**

| Série | Origem |
|---|---|
| S0, controles nativos | arquivo guardado, idêntico byte a byte |
| S1, controles nativos | arquivo guardado, idêntico byte a byte |
| S4 histórico, controles nativos | reexecução verificada (2.4) |
| S4 distribuído (BESS + V2G), controles nativos | reexecução verificada (2.4) |
| S4 distribuído (BESS + V2G), política de fase C salva | arquivo guardado, idêntico byte a byte |

**Conferência:** os valores de 24 h recalculados das séries coincidem com os resumos auditados: diferença 0 nos arquivos idênticos e erro relativo ≤ 10⁻⁸ nos reexecutados. Coincidem também com as Tabelas 13 e 14. As trajetórias coincidem com as curvas correspondentes da Fig. 10 original: S1 com 41/41/41/17 linhas novas às 17–20 h e S4 histórico com 21/23/14/14.

**Distinção S4 histórico × distribuído:** aparece nos painéis horários (linha preta traço-ponto contra as duas linhas do S4 distribuído) e nos painéis (d)–(f). O S4 distribuído tem 0 linhas novas em todas as horas, nas duas políticas. O S4 histórico tem até 23 linhas novas às 18 h.

**S2 e S3 horários:** foram regenerados (2.4) e estão em `data/fig10_hourly_traces.csv`. Às 17–20 h, o S3 tem 21/32/21/14 linhas novas e o S2 tem 0. Eles não são desenhados em (a)–(c), por duas razões:
- com sete curvas os painéis ficam ilegíveis;
- o texto do Rev09 já diz que S2/S3 são quantificados na Tabela 13, e é isso que os painéis (d)–(f) mostram.

Para desenhá-los, basta acrescentar `S2_dist_native` e `S3_dist_native` à tupla `HOURLY` em `make_fig10.py`.

**Cores:**
- S0 cinza; S1 `#D55E00`; S2 `#0072B2`; S3 `#009E73`; S4 `#AA3377`; S4 histórico em preto.
- A paleta categórica foi testada com o validador de paletas (luminosidade, croma, separação para daltonismo protan/deutan/tritan, separação em visão normal e contraste): todas as verificações passaram.
- A política de fase C usa um tom mais claro do mesmo matiz (`#E28FC0`, tracejado), desenhado por cima. Assim ela continua visível onde quase coincide com o S4 nativo, sobretudo em (a) e (b).
- A política é codificada também pelo tipo de linha e pela legenda, nunca só pela cor.

### Parte B — Comparação entre cenários, painéis (d)–(f)

| Cenário | Linhas novas | Carregamento máximo (%) | Tensão mínima (p.u.) |
|---|---:|---:|---:|
| S0 | 0 | 111.03 | 0.93707 |
| S1 | 41 | 132.13 | 0.91915 |
| S2 | 0 | 114.57 | 0.92438 |
| S3 | 32 | 129.70 | 0.92558 |
| S4 | 0 | 108.51 | 0.93648 |
| S4 histórico (Tab. 14) | 23 | 120.80 | 0.92357 |

- **(d)** Barras de contagem a partir de zero, com rótulo "0" visível nos cenários sem linhas novas.
- **(e)** e **(f)** Hastes partindo das referências de 100% e 0,95 p.u. Assim o leitor vê de imediato que **todos** os cenários ultrapassam 100% e ficam abaixo de 0,95 p.u.
- A figura não tem notas explicativas internas. O fato de serem extremos e contagens de 24 h, e não observações horárias, fica na legenda.

### Parte C — S2 × S4

**Anotações:**
- em (e): guia pontilhada no nível do S2, seta até o S4 e "S4 vs S2: −6.06 pp";
- em (f): guia pontilhada no nível do S2, seta até o S4 e "S4 vs S2: +0.01210 p.u.".

**O que a figura não afirma:**
- O painel (d) mostra S2 e S4 ambos com 0 linhas novas; a figura não diz que só o S4 elimina sobrecargas novas.
- Os painéis (e) e (f) mostram o S4 ainda acima de 100% e abaixo de 0,95 p.u.; a figura não sugere atendimento integral dos limites.

### Parte D — Histórico × distribuído

O S4 histórico aparece depois de um separador vertical, com barra hachurada e marcador vazado. A legenda chama isso de **"configuration comparison"** e registra que local e despacho diferem. A expressão "isolated siting improvement" não é usada.

### Organização

A figura tem 190 × 206 mm, dentro da área útil de página Elsevier: três painéis horários em cima e três comparativos embaixo. A composição ficou legível, com fontes de 6–7,5 pt no tamanho final, sem necessidade de dividir em duas figuras.

### Legendas propostas

> *Fig. 10. OpenDSS operating behavior and scenario-dependent electrical performance: (a–c) hourly trajectories of substation input, newly overloaded lines and minimum MV physical-phase voltage for S0 and S1 under native controls, the historical single-site S4 replay under native controls, and the distributed S4 under native controls and under the saved phase-C regulator policy; (d–f) 24-h summary metrics for the distributed S0–S4 scenarios under native regulator controls, with the historical S4 replay shown as a configuration comparison in which siting and dispatch both differ. Summary metrics represent case-level extrema and counts, not hourly observations. Dotted lines mark the 100% loading and 0.95-p.u. study screens.*
---

## 5. Verificações realizadas

As verificações abaixo são automáticas em `prepare_fig10_data.py`: o script falha se qualquer uma não passar.

1. Para `source.csv`, `voltages.csv` e `branches.csv`, cada série plotada atende a uma de duas condições:
   - tem SHA-256 idêntico ao `paper_insertions/manifest.json`;
   - vem da reexecução aceita (2.4), com hash igual ao registrado no manifesto da reexecução.
2. Cada série plotada reproduz o resumo auditado de 24 h (pico, Vmin, carregamento máximo, linhas novas e perdas): diferença 0 nos arquivos idênticos e erro relativo ≤ 10⁻⁸ nos reexecutados, com contagens idênticas.
3. Os 24 intervalos estão presentes e não há observações fase-hora duplicadas.
4. Tabela 13: os cinco cenários coincidem com `comparison.csv` (casos `native_*_auto`) nas casas impressas.
5. Tabela 14: as três linhas coincidem (`exact_profile_pilot/summary.csv` e `changed_S4_auto`).
6. As diferenças S2→S4 coincidem com o Rev09 (ver 6.1).
7. Os PDFs têm fontes incorporadas, os SVGs não têm texto dependente de fonte e os PNGs estão em 600 dpi.
8. As figuras foram renderizadas e inspecionadas visualmente quanto a sobreposição de rótulos e cortes.

---

## 6. Revisão de consistência com o manuscrito (Tarefa 3)

Nenhum resultado ou conclusão foi alterado. Os pontos abaixo são observações para decisão dos autores.

1. **6,06 × 6,07 pp.** A diferença de carregamento S2 − S4 calculada sem arredondamento é 6,0656 pp, que arredonda para **6,07**. O Rev09 (Resumo, §3.9, §4.1) usa **6,06**, que é a diferença dos valores tabelados 114,57 − 108,51. A figura usa 6,06 para coincidir com os valores impressos. Alternativa: manter 6,06 e acrescentar "(difference of tabulated values)", ou passar a 6,07 nos três lugares. O ganho de tensão (+0,01210 p.u.) é consistente nos dois critérios.

2. **Fig. 1 não é citada no texto.** Proposta: acrescentar ao fim do §1, parágrafo 4 ("This paper addresses one bounded question…"):
   > *Fig. 1 summarizes the two stages, their data links and the comparative assessment.*

3. **§2.7, segundo parágrafo.** O texto diz que as injeções foram reaplicadas no modelo OpenDSS "phase-resolved, secondary-inclusive" e emenda "For the S0 model-transfer comparison, 3,820 matched physical bus–phase voltages…". Os arquivos mostram que o RMSE de 0,07277 p.u. vem de um **equivalente MT no OpenDSS com taps fixos e secundários removidos**; o modelo completo dá 0,06208 p.u. às 18:00. Sugestão, sem alterar o número:
   > *For the S0 model-transfer comparison, an OpenDSS medium-voltage equivalent with fixed reference taps and primary-equivalent loads was paired with the pandapower model; 3,820 matched physical bus–phase voltages were available at 18:00.*

   A legenda da Fig. 9 poderia receber a mesma qualificação.

4. **Frase da Fig. 10 no §3.9** (fim do parágrafo após a Tabela 13). A frase atual, "Figure 10 displays the selected hourly S0/S1/S4 traces; S2/S3 are quantified in Table 13 but are not plotted there.", continua correta para os painéis (a)–(c), mas não menciona (d)–(f). Proposta:
   > *Figure 10(a–c) displays hourly OpenDSS traces for S0, S1 and the historical and distributed S4 configurations, and Fig. 10(d–f) compares the 24-h Table 13 metrics for S0–S4 under native controls together with the historical S4 replay of Table 14; the S2/S3 contrasts are therefore shown as case-level summaries rather than hourly trajectories.*
5. **Título da Fig. 10.** O título atual ("Hourly OpenDSS trajectories…") deve ser substituído pela legenda da seção 4, porque a figura agora inclui métricas de 24 h.

6. **Tabela 3.** As células de S1–S3 citam os locais históricos (l3029502, m1069504, "150 host EVs"). A Fig. 1 e o §2.5 deixam claro que na configuração distribuída S2/S3 correspondem a três BESS e três hospedeiros. Sugere-se uma nota na Tabela 3: *"Site identifiers refer to the historical reduced-model stage; distributed sites are listed in Table 15."*

7. **Cabeçalho da Tabela 13.** "Minimum physical-phase voltage" refere-se às 2.521 barras primárias (3.820 fases físicas por hora). Os secundários não estão nos arquivos de tensão, embora o modelo os contenha. Sugere-se "Minimum MV physical-phase voltage", como na Fig. 10(c).

8. **Termos usados nas figuras e definidos no Rev09:** newly overloaded line (§2.5, Eq. 6), native controls, saved phase-C policy (§2.7), frozen taps (§2.5), model-transfer diagnostic (§2.7), physical phases (§2.1), V3.0.1 (§2.6–2.7). "Configuration comparison" não aparece literalmente, mas corresponde ao §2.7 ("historical-versus-distributed S4 comparison jointly changes dispatch and location").

9. **Correspondência S0–S4:** os rótulos funcionais S0–S4 da Fig. 1 coincidem com a Tabela 3 e o §2.5. Os valores da Fig. 10(d)–(f) coincidem com as Tabelas 13 e 14 (verificação automática).

10. **"No new simulation was performed for this manuscript revision".** O nome do arquivo e o texto do Rev09 dizem isso.
    - Nenhum cenário, despacho ou resultado novo foi criado.
    - Mas duas das séries horárias desenhadas na Fig. 10 (S4 histórico e S4 distribuído nativo) foram **regeneradas pela reexecução dos replays OpenDSS já arquivados** (2.4), e a frase, se mantida literalmente, deixa de ser exata.
    - Proposta para a seção Data availability ou para a nota da Fig. 10:
    > *Hourly OpenDSS traces for the historical S4 and the distributed S4 under native controls were regenerated by re-executing the archived replays (identical saved injections, engine code and OpenDSS version); the re-executed outputs reproduce the archived 24-h summaries to within 10⁻⁸ relative error and the stored control cases to within 10⁻¹⁰ p.u. No scenario, dispatch or optimization was re-run.*

    Alternativa: copiar as pastas originais da máquina Windows (seção 7). Assim a figura volta a usar só arquivos idênticos byte a byte e a frase atual pode ser mantida.

---

## 7. Como substituir as séries reexecutadas pelos arquivos originais

As pastas foram geradas na máquina Windows onde a auditoria rodou (`<root>\EVCS-BESS-V2G_v0.1\…`). Para que a Fig. 10 use só arquivos idênticos byte a byte:

1. Copie para as mesmas posições nesta pasta do OneDrive, cada uma com `source.csv`, `voltages.csv` e `branches.csv`:
   - `EVCS-BESS-V2G-Distributed-Stochastic/validation_applied_energy_final/exact_profile_pilot/S4_auto/`
   - `…/alternative_controls/distributed_native/`
   - `…/distributed_s2_s3/native_S2_auto/`
   - `…/distributed_s2_s3/native_S3_auto/`
2. Rode os comandos da seção 8.

Os dois primeiros só são aceitos se o hash coincidir com o manifesto. S2 e S3 não têm hash no manifesto e são aceitos se reproduzirem exatamente `comparison.csv`. Pastas originais presentes têm prioridade sobre `reproduced_opendss/`; os status passam de `reproduced_verified` para `audit_exact`.

---

## 8. Reprodução

Requisitos para as figuras: Python 3 com pandas, numpy e matplotlib (testado com matplotlib 3.9.4), `bsdtar` (nativo no macOS) e a fonte Arial.

```bash
cd Rev09_Fig01_Fig10_revision/scripts
python3 make_fig01.py
python3 prepare_fig10_data.py      # verifica as fontes e grava data/
python3 make_fig10.py
```

Esses três scripts não executam fluxo de potência, otimização nem sorteio.

**Reexecução dos replays** (seção 2.4). Ela só é necessária se `reproduced_opendss/` for apagada. As versões são as da auditoria. `reproduce_fig10_hourly.py` recusa gravar sobre uma pasta existente.

```bash
cd Rev09_Fig01_Fig10_revision
python3.12 -m venv .venv_repro
.venv_repro/bin/pip install opendssdirect.py==0.9.4 dss-python==0.15.7 dss-python-backend==0.14.5 \
    numpy==2.3.3 pandas==2.3.3 PyYAML==6.0.3 scipy==1.16.1
# Entradas (somente leitura), extraídas para um diretório temporário $T:
#   EVCS-BESS-V2G.rar -> EVCS-BESS-V2G/results/ieee8500/integration/dados/S{0,1,4}_injections.csv
#   EVCS-BESS-V2G/results/replay_s0_s4_opendss.rar -> replay_s0_s4_opendss/S0, S1  (controles)
#   EVCS-BESS-V2G-Distributed-Stochastic/results/v301_continuacao_ac_segura.rar -> best/
#   espelho $T/repo_mirror: data/ieee8500/source/*.dss e configs/*.yaml (projeto original),
#     src/network/data/ieee8500.json (snapshot_fontes.zip), opendss_rebuild/settings.yaml
PYTHONDONTWRITEBYTECODE=1 .venv_repro/bin/python scripts/reproduce_fig10_hourly.py \
    --mirror $T/repo_mirror --hist-injections $T/EVCS-BESS-V2G/results/ieee8500/integration/dados \
    --v301-best $T/v301_continuacao_ac_segura/best --stored-replay $T/replay_s0_s4_opendss \
    > reproduce_fig10_hourly.log 2>&1
```

---

## 9. SHA-256 dos produtos

| Arquivo | SHA-256 |
|---|---|
| `figures/Fig01_Methodological_Framework.pdf` | `b997835f1a98e40a655eb689c84f61d7fd1acd89a19ea97948ac9aacde0f906f` |
| `figures/Fig01_Methodological_Framework.png` | `c4b114e19edd63e00adc7c9661c0317186f96230105cf8c69cf66755b746ef2e` |
| `figures/Fig01_Methodological_Framework.svg` | `6499ba74e5e99525d8cabfc52afc7f7640a3b22789c49d25e0784177a77a1f68` |
| `figures/Fig10_OpenDSS_Comparative_Results.pdf` | `b8575e78290cfa29f9e341dad0f32401c8bea49c1e6c1fd61234dac8d7bcec75` |
| `figures/Fig10_OpenDSS_Comparative_Results.png` | `1d06a2dffd4645684ad120719ff968443cd72adb87373add1f736b443d88bb36` |
| `figures/Fig10_OpenDSS_Comparative_Results.svg` | `873ea72a93dd782ce070d9b75764f34423cf6950d753190c1a76e5b127a34135` |
| `data/fig10_hourly_traces.csv` | `3b36f656e0f2be20ce45f8dc1a1f6d247022a3fd8b21b607c9b2d57c35d96df0` |
| `data/fig10_summary_metrics.csv` | `0a3065962af79f4d16e83689812f7f084b414eb31c37e70b2698add1e11dbb04` |

Os hashes das figuras mudam a cada renderização porque o matplotlib grava data e identificadores internos nos arquivos. O conteúdo é reprodutível pelos scripts.
