# Reotimização sequencial V0.9 — IEEE 8500 / OpenDSS

## Objetivo

Testar a hipótese de que um processo **MILP–OpenDSS com relinearização e região de confiança** pode melhorar o despacho conjunto de BESS (Kenia) e V2G (Cesar), mantendo o modelo físico IEEE 8500 e os perfis de recarga S1 existentes. A V0.8 foi validada matematicamente, mas sua solução não passou na auditoria elétrica AC; esta V0.9 **não promete** zerar as violações ou superar o despacho legado.

## O que muda em relação à V0.8

- A referência elétrica é o **S4 efetivamente calculado no OpenDSS**, e não necessariamente S1.
- Recalibração das derivadas por diferenças finitas em cada um dos 24 horários, congelando reguladores/capacitores *somente durante a perturbação*. O replay cronológico permanece com controles automáticos.
- MILP de 24 horas com *trust regions* horárias em torno das potências líquidas de BESS e frota V2G. As restrições individuais da bateria e dos veículos continuam sendo impostas pela implementação original V0.8.
- Verificação no OpenDSS de **cada proposta**, comparação com S0 e rejeição de propostas que não melhoram o mérito AC ou excedem os orçamentos de agravamento de tensão e de sobrecarga.
- Se uma proposta for rejeitada, reduzir a região de confiança e testar nova proposta. Se nenhuma melhorar, **manter a V0.8 como melhor solução encontrada** e registrar essa conclusão.
- Comparação explícita com o S4 legado (se disponível no replay de entrada).

## Como instalar

Extrair o ZIP na **raiz** do repositório `EVCS-BESS-V2G/`, no mesmo nível das pastas `configs`, `src`, `data` e `results`.

```powershell
Expand-Archive -Path "$env:USERPROFILE\Downloads\opendss_rebuild_patch_v09_iterativo.zip" -DestinationPath . -Force
```

A V0.8 deve estar instalada. A atualização substitui apenas `opendss_rebuild/rebuild/joint_optimization.py` e acrescenta `reotimizar_sequencial_v09.py`, documentação e testes; **não altera** `configs/`, `src/` ou as saídas S0–S4 antigas.

## Execução sugerida: uma iteração diagnóstica

Na raiz do repositório:

```powershell
python .\opendss_rebuild\reotimizar_sequencial_v09.py `
  --results .\results\replay_s0_s4_opendss `
  --v08-dir .\results\joint_opendss_v08 `
  --output .\results\joint_opendss_v09_teste `
  --max-iterations 1 --max-backtracks 2 `
  --delta-kw 100 --bess-trust-kw 300 --v2g-trust-kw 250
```

Para ampliar a experiência a três iterações, use **outra pasta** de saída e `--max-iterations 3`. Não repita a V0.8: a V0.9 reutiliza seus dados AC e reotimiza a partir do S4 existente.

## Arquivos principais

- `initial_ac_score.json`: avaliação AC auditável do S4 V0.8.
- `legacy_s4_ac_score.json`: benchmark AC legado (se o replay contiver S4).
- `iteration_XX/sensitivity/{lines,voltages,source}.csv`: derivadas OpenDSS no ponto de operação vigente.
- `iteration_XX/trial_YY/`: MILP, despacho, CSVs e auditoria AC de cada proposta.
- `iteration_XX/trial_YY/acceptance.json`: aceitação ou rejeição fundamentada.
- `best/`: melhor solução aceita no critério definido; começa como cópia integral do S4 V0.8.
- `history.json` e `final_report.json`: histórico e comparação final com os benchmarks.
- `failure.json`: erro não recuperado, sem falsa afirmação de viabilidade.

## Critérios de mérito — escolhas experimentais, não normas

O mérito AC é a combinação ponderada de:

1. Severidade térmica acima de `max(100%, carregamento_S0)` por hora/linha/fase.
2. Severidade de subtensão abaixo de `min(0.95, tensão_S0)`.
3. Severidade de sobretensão acima de `max(1.05, tensão_S0)`.
4. Pico de importação na subestação.

O programa também exige que o agravamento da severidade térmica relativa não exceda 5% e o de tensão total relativa não exceda 10% por passo, por padrão. Esses percentuais são **parâmetros exploratórios** e não significam conformidade de rede. O relatório registra os valores completos para fundamentar escolhas de pesos e compromissos multiobjetivo.

## Limitações importantes

- A região de confiança atua nas **injeções líquidas agregadas** por hora; a viabilidade individual dos 150 veículos continua sendo verificada pelo módulo de Cesar.
- A solução pode permanecer com sobrecargas, subtensões ou sobretensões. O status de conclusão nunca significa conformidade automática.
- Recalibração em pontos de operação diferentes não resolve, por si só, descontinuidades de taps ou de chaveamento de capacitores. A aceitação depende do replay AC.
- Esta é uma aproximação **MILP–AC sequencial local**; não produz certificado de ótimo global do problema elétrico não linear.
- O S0 tem violações preexistentes e a tensão da fonte é 1,05 p.u.; nenhuma nova solução pode ser declarada segura apenas por reduzir uma função ponderada.
- As ampacidades são hipóteses do estudo; a rede IEEE 8500 não é uma medição de um alimentador hondurenho.
- É necessário comparar separadamente o desempenho com **S2, S3 e S4 legado** e, depois, executar as sensibilidades de ampacidade, fonte, resoluções de 15 minutos e configurações de controle, antes de reivindicar resultados aptos à *Applied Energy*.

## Validação realizada antes da entrega

Testes automatizados do MILP, referência móvel, região de confiança, impedimento de injeção dupla e regra de aceitação AC. A função de pontuação foi conferida com os dados reais da V0.8. **Não houve execução do fluxo IEEE 8500 completo neste ambiente**, pois OpenDSS não estava disponível aqui.
