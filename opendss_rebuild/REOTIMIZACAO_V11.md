# V1.1 — Otimização BESS–V2G AC-guided com guardas físicas locais

**Nova frente de pesquisa com periódico-alvo Applied Energy.** O estudo baseado nos despachos legados deve permanecer separado, destinado a outro periódico. Este pacote não altera o manuscrito, as configurações de Kenia/Cesar ou os resultados antigos.

## Dependências

- Projeto `EVCS-BESS-V2G` na raiz do repositório, com os arquivos instalados de V0.8, V0.9 e V1.0.
- Python, `numpy`, `pandas`, `scipy`, `PyYAML` e OpenDSSDirect conforme o ambiente que já executou a V1.0.
- Repositório e `configs/` inalterados desde a V1.0: o script compara SHA-256 dos arquivos físicos/configurações.
- Resultados existentes `results/replay_s0_s4_opendss` e `results/joint_opendss_v10`.
- As pastas de saída da nova execução devem estar vazias.

## Instalação no PowerShell (raiz do repositório)

```powershell
Expand-Archive -Path "$env:USERPROFILE\Downloads\opendss_rebuild_patch_v11_guardas_ac.zip" -DestinationPath . -Force
```

O ZIP contém **dois arquivos de código essenciais**: `opendss_rebuild/reotimizar_guardas_v11.py` e `opendss_rebuild/rebuild/joint_optimization.py`. O segundo adiciona as restrições de guarda ao MILP; instalar apenas o primeiro causa erro de compatibilidade.

## Comando recomendado para a primeira execução

```powershell
python .\opendss_rebuild\reotimizar_guardas_v11.py `
  --results .\results\replay_s0_s4_opendss `
  --v10-dir .\results\joint_opendss_v10 `
  --output .\results\joint_opendss_v11 `
  --task run `
  --seeds i01_iteration_01_trial_01_thermal,i01_iteration_01_trial_01_under_guard `
  --profiles thermal_guard,voltage_guard `
  --max-iterations 1 `
  --max-candidates 4 `
  --bess-trust-kw 180 `
  --v2g-trust-kw 150 `
  --delta-kw 100
```

Opcional: para inspeção das sementes sem OpenDSS, usar `--task inspect` e **outra** pasta de saída vazia, por exemplo `--output .\results\joint_opendss_v11_inspect`. Uma pasta não pode ser reutilizada depois da inspeção.

## Metodologia

1. Importa apenas candidatos V1.0 com CSVs completos de replay AC e que estejam na fronteira salva. A sexta simulação V1.0, se ainda incompleta, não é importada nem declarada válida.
2. Reconfere a demanda S1, as injeções da frota e os contratos operacionais individualizados dos 150 veículos. Carregamento do V2G antigo é substituído pelo novo, evitando duplicação.
3. Para cada semente escolhida, recompõe 24h de OpenDSS com reguladores automáticos e estima diferenças finitas locais com controles congelados durante a perturbação.
4. No MILP, impõe limites superiores de 100% nas **41 linhas que ficaram sobrecarregadas pela primeira vez no S1** em todas as suas combinações hora–fase (2.952 verificações para 41 linhas trifásicas, se todas tiverem três fases).
5. Também impõe pequenos cortes de melhoria em amostras críticas de linhas que já violavam os limites em S0 e em pontos que apresentam agravamentos de subtensão/sobretensão em comparação ao S0. Restrições de melhoria somente são selecionadas quando a estimativa de controlabilidade permite efeito local.
6. Mantém as restrições de potência, carga/descarga do BESS, SOC final, mobilidade, disponibilidade e limites individuais de Cesar/Kenia. Protege contra alterações excessivas de despacho por regiões de confiança.
7. **Reexecuta o OpenDSS** com controles automáticos para cada candidato. Se uma nova linha distinta apresentar sobrecarga em relação ao S0, ele é registrado no histórico, mas **não entra na fronteira elegível**.
8. Preserva as soluções V1.0 não dominadas, as candidatas elegíveis da V1.1 e os motivos das falhas. Os resultados não dominados são classificados em quatro objetivos AC: severidade térmica adicional, subtensão adicional, sobretensão adicional e pico de importação.

A opção de zero novas linhas distintas não significa ausência de agravamentos linha–fase–hora. O arquivo `guardas_ac_verificacao.json` registra se os cortes específicos se confirmaram ou foram frustrados pela resposta automática de taps/capacitores. Nenhum candidato é declarado eletricamente seguro sem verificação de ausência total de agravamentos contra S0.

## Saídas

- `resumo_final_v11.json`: resumo, quantidade de candidatos AC, fronteira, bandeiras de não agravamento e limitações.
- `fronteira_pareto_ac_v11.csv`: apenas candidatos AC elegíveis e não dominados.
- `todos_candidatos_ac_v11.csv`: candidatos importados e novos AC, inclusive rejeitados por reabertura de linha.
- `historico_v11.csv`: erros do MILP e da simulação, perfis e resultados.
- `iteration_*/seed_*/sensitivity/`: coeficientes recalculados, prova de reprodução do AC da semente.
- `candidates/*/`: despacho dos 150 veículos, SOC, amostras elétricas, `guardas_lineares.json`, `guardas_ac_verificacao.json`, `indicadores_economicos_proxy.json` e relatório AC.

## Limitações científicas fundamentais

- As restrições de guarda são **lineares locais**. Taps e bancos de capacitores são fixados somente no cálculo das derivadas, mas são automáticos no replay AC; portanto cortes não garantem valores AC.
- Não se demonstra otimalidade global AC nem viabilidade irrestrita do alimentador original. S0 já contém violações e a fonte original de 1,05 p.u. contribui para sobretensões.
- Os perfis são horários sintéticos. Faltam robustez sub-horária, sensibilidade à corrente nominal de estudo, controles/estado da rede e validação de custos com tarifas reais.
- `indicadores_economicos_proxy.json` usa a tarifa de referência dos veículos como **proxy** para o custo de importação na fonte; não equivale a tarifa de suprimento validada. Remuneração V2G e desgaste são exibidos separadamente, sem pressupor que se possa somar tudo numa avaliação definitiva.
- Com guards mais exigentes, o MILP pode ser inviável. Nesse caso, o programa preserva a fronteira V1.0 e registra o motivo em `error.json`, sem inventar resultados AC.
- A execução inteira não pôde ser reproduzida no ambiente de geração do pacote por indisponibilidade local do OpenDSSDirect; foram feitos testes unitários, de integração MILP sintética e auditoria dos arquivos reais V1.0.

## Próximo passo depois de executar

Compactar `results/joint_opendss_v11` e enviar a pasta. Examinar a fronteira, os pontos que mudaram de tap, a quantidade de cortes AC satisfeitos, o perfil de recarga adicional e as possíveis violações preexistentes antes de escolher uma metodologia definitiva para o periódico.
