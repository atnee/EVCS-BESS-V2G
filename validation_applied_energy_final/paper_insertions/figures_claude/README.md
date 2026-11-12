# Figuras 9–11 redesenhadas (candidatas)

Versões alternativas das Figuras 9–11 do pacote `paper_insertions/`, para uma revisão do manuscrito Applied Energy ou para um artigo separado. A decisão editorial continua em aberto. Nada aqui altera o Word, os resultados históricos, as configurações, os scripts da rede ou as figuras atuais em `../figures/`: os hashes destas últimas foram conferidos contra `../manifest.json` depois da geração.

## Arquivos

| Arquivo | Conteúdo |
|---|---|
| `fig9_model_voltage_discrepancy_claude.{pdf,png,tif}` | Fig. 9, 190 × 70 mm |
| `fig10_native_operational_replay_claude.{pdf,png,tif}` | Fig. 10, 190 × 150 mm |
| `fig11_thermal_voltage_control_tradeoff_claude.{pdf,png,tif}` | Fig. 11, 190 × 70 mm |
| `CAPTIONS_en.md` | As três legendas em inglês. Os números são gerados pelo script a partir das fontes. |
| `build_figures_claude.py` | Script reproduzível, sem fluxo de potência nem otimização |
| `manifest_sha256.json` | SHA-256 das 51 entradas, do script e de todas as saídas, mais as versões do ambiente |
| `verification.json` | Resultado de cada verificação e indicadores recalculados |
| `data/` | Valores efetivamente traçados (resumo por fase, séries horárias, severidades) |

Formatos:
- **PDF:** vetorial, uma página, sem nenhuma imagem raster, com Arial TrueType incorporada (fonttype 42).
- **TIFF:** RGB a 1.000 dpi com LZW, o mínimo da Elsevier para arte de linha.
- **PNG:** RGB a 600 dpi, só para revisão.

O tamanho é o de impressão (largura de duas colunas, 190 mm), sem recorte `bbox_inches`. Por isso, insira a figura a 100 %, como nas Figuras 1–8. O estilo vem de `src/integration/paper_style.py`, o mesmo das Figuras 1–8: Arial de 7–8 pt, linhas finas e cores de cenário S1/S4 iguais.

## Reprodução

```powershell
python validation_applied_energy_final/paper_insertions/figures_claude/build_figures_claude.py --replace
```

- Sem `--replace`, o script recusa sobrescrever as saídas existentes.
- O script nunca escreve fora desta pasta.
- Duas execuções seguidas geram arquivos com SHA-256 idênticos: o PDF sai sem data de criação.
- Tempo de execução: cerca de 30 s.

## Verificações (todas aprovadas; ver `verification.json`)

1. **Recalculo a partir dos brutos.**
   - Fig. 9: os dois CSV pareados do Projeto A, com `delta_pu = opendss_pu − pandapower_pu` conferido linha a linha.
   - Figs. 10–11: `source.csv`, `voltages.csv` e `branches.csv` de cada replay.
   - Os valores batem com a Tabela S1, as Tabelas 13 e 14, `figure_data/` e `alternative_controls/comparison.csv` até a 10ª casa significativa.
   - Valores de conferência da revisão:
     - RMSE de 0,02270 pu (03h) e 0,07277 pu (18h);
     - linhas novas: 41 (S1), 23 (S4 histórico) e 0 nos dois S4 distribuídos;
     - V_min de 0,92357, 0,93648 e 0,93752 pu nos três S4.
2. **Estrutura.**
   - Fig. 9: 3.820 pares barra–fase por horário, sem chaves duplicadas.
   - Figs. 10–11: os sete replays têm 24 intervalos de 1 h, 3.820 linhas barra–fase e 3.714 linhas linha–fase por hora, sem chaves hora–barra–fase ou hora–linha–fase duplicadas e sem valores ausentes. O conjunto de nós físicos é o mesmo em todos os casos.
   - `loading_pct = current_a / ampacity_a × 100` em todas as linhas.
3. **Procedência dos controles (Fig. 11).**
   - `applied_policy.json` é idêntico nos três casos com política: −0,5 V em `feeder_regc`, `vreg2_c`, `vreg3_c` e `vreg4_c`, na base do secundário do TP (126,5→126,0 V e 125,0→124,5 V), sem mudança nos capacitores.
   - Os taps travados coincidem exatamente com os taps horários do S0 correspondente: S0 nativo do piloto e S0 da política salva.
   - Nas duas visões travadas, 6 capacitores mudam de estado ao longo do dia, o que confirma o controle automático.
4. **Recursos citados nas legendas.** As três BESS distribuídas somam 2.200 kW e 7.200 kWh, os mesmos valores da BESS única em `EVCS-BESS-V2G/configs/bess.yaml`. Os 150 veículos V2G estão em três locais, cada um em um só local.
5. **Inspeção visual.** Os três PDFs foram renderizados com PyMuPDF a 300 dpi e examinados: nada cortado, nenhuma legenda sobreposta aos dados, eixos com unidade, sem notação científica.

**Divergências numéricas: nenhuma.** Nenhuma figura foi bloqueada.

## Diferenças em relação às Figuras 9–11 atuais

Os dados são os mesmos; as diferenças estão na forma de mostrá-los.

### Fig. 9

- **Mesma estrutura da original:** dois painéis, (a) 03h e (b) 18h, com eixo y comum. Foi escolhida depois de duas tentativas rejeitadas, uma com nuvem de pontos e outra com violinos.
- **Bigodes do mínimo ao máximo** (`whis=(0, 100)`), em vez de 1,5 × IQR. Todo par fica dentro do desenho e não há *outliers* ocultos. A versão atual usa `showfliers=False`, o que esconde 217 pares da fase B às 03h. A frase "outliers hidden" sai da legenda.
- **Cores por horário, não por fase.** Azul-claro para as 03h e azul-escuro para as 18h; a mediana aparece em preto ou branco para contrastar com o preenchimento. As caixas azul, laranja e verde da versão atual repetiam cores de cenário das outras figuras.
- **Rótulos:** RMSE com 5 casas no canto de cada painel (a versão atual usa 4 casas); eixo y rotulado explicitamente como |V_OpenDSS − V_pandapower|; estilo Arial das Figuras 1–8.
- **Fato novo na legenda, calculado:** o sinal da diferença é sistemático. O OpenDSS fica abaixo do pandapower na fase A em ≥ 99,6 % dos pares e acima na fase C em ≥ 99,9 %.

### Fig. 10

- **Degraus em vez de linhas interpoladas.** Cada valor ocupa o intervalo [t, t+1), conforme a convenção de que t é o início do intervalo, e o eixo vai até 24 h. A versão atual ligava os pontos horários por retas, o que sugeria valores intermediários inexistentes. Por exemplo, 20 linhas novas "entre" 16h e 17h.
- **S0 fora do painel (b),** porque é zero por definição. A legenda explica, e uma anotação indica que o S4 distribuído vale 0 em todas as horas nas duas políticas.
- **Leitura em impressão P&B.** A linha do S4 histórico é traço-ponto e a da política salva é tracejada. As cores de S1 e S4 são as das Figuras 1–8. A paleta foi validada para daltonismo: separação mínima ΔE 8,6 entre todos os pares.
- **Rótulos mais claros:** "S4, distributed, saved regulator policy" no lugar de "+ control"; kW com separador de milhar; a linha de 0,95 pu tem rótulo próprio.
- **Legenda com os números conferidos.** Ela diz que as duas curvas distribuídas do painel (a) se sobrepõem, com diferença máxima de 4,4 kW.

### Fig. 11

- **Barras horizontais** com rótulos legíveis, no lugar dos rótulos girados a 50°.
- **Visões com taps travados** em hachura, não em tom claro. A distinção sobrevive à impressão em P&B.
- **Nomes explícitos:**
  - "changed control" passou a "saved regulator policy";
  - "native frozen" passou a "taps frozen at native S0";
  - "changed frozen" passou a "taps frozen at saved-policy S0".
- **Valores:** a severidade térmica tem 1 casa decimal (145,1 / 148,9, antes 145 / 149).
- **A legenda deixa explícito que:**
  - a severidade é uma soma sobre fases e locais (exposição agregada, não profundidade num ponto);
  - os 0,5 V estão na base do TP e não são 0,5 pu;
  - os capacitores continuam automáticos;
  - as grandezas são absolutas, não diferenças contra o S0 nem contagens de linhas novas.
- **Valores absolutos do S0 nativo** entram na legenda como referência: 73,4 pp·h, 0,59 pu·h e 2,64 pu·h. Não são subtraídos de nada.

## Cuidados para o uso editorial

- **Fig. 9** compara a redução MT do pandapower com um equivalente MT no OpenDSS, nas mesmas condições de S0. Não demonstra equivalência ao IEEE 8500 completo.
- **Figs. 10–11** usam o alimentador completo no OpenDSS. O S4 distribuído é uma configuração posterior e não faz parte das 63 configurações históricas.
- **Unidade "pu·h":** é somada sobre 3.820 nós barra–fase. Valores de casos diferentes são comparáveis entre si, mas não equivalem a um desvio de tensão num ponto.
- **Linhas novas na visão travada nativa.** Essa visão tem 2 linhas novas (Tabela 14). Esse dado não aparece na Fig. 11, de propósito, para não misturar contagem e severidade.
- **Faixas de tensão.** 0,95 e 1,05 pu são critérios de engenharia do estudo (ANSI C84.1, faixa A), não a norma de Honduras.
