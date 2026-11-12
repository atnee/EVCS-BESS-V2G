# OpenDSS Rebuild V1.0 — Pareto AC da coordenação BESS–V2G

## Finalidade e escopo

Nova frente de pesquisa voltada à **Applied Energy**. O artigo que usa os resultados legados é mantido separado, com periódico-alvo a decidir. A V1.0 é uma implementação exploratória de otimização AC-guiada; **não declara que os despachos sejam eletricamente viáveis, nem que a solução seja Pareto-global**.

O algoritmo preserva as decisões de Kenia (BESS 2,2 MW / 7,2 MWh, SOC 10–90%, posição `l3029502`) e de Cesar (150 veículos, ligação `m1069504`, 154 portas/1.694 kW, SOC individual, disponibilidade e energia de viagem). Usa o IEEE 8500 original em OpenDSS com controles automáticos no replay final. A curva de carga é sintética de 24 horas, 1 h/passagem, com ampacidades do estudo. **Os 2.850 VEs dos demais eletropostos e os 150 V2G são mantidos sem duplicar energia.**

## Instalação

Extraia o pacote ZIP na **raiz do projeto `EVCS-BESS-V2G`**, substituindo somente os arquivos da pasta `opendss_rebuild`. Requer a V0.9 previamente instalada e executada, bem como a V0.8 e o replay S0–S4.

Arquivos novos/atualizados:

- `opendss_rebuild/reotimizar_pareto_v10.py` (novo)
- `opendss_rebuild/rebuild/joint_optimization.py` (atualização retrocompatível: três campos opcionais de epsilon)
- `opendss_rebuild/tests/test_pareto_v10.py` (novo)
- `opendss_rebuild/REOTIMIZACAO_V10.md` (este arquivo)

## 1. Apenas inspecionar os candidatos anteriores (sem resolver OpenDSS)

Na raiz do projeto:

```powershell
python .\opendss_rebuild\reotimizar_pareto_v10.py `
  --results .\results\replay_s0_s4_opendss `
  --v08-dir .\results\joint_opendss_v08 `
  --v09-dir .\results\joint_opendss_v09_teste `
  --output .\results\pareto_v10_inspecao `
  --task inspect
```

Esse teste verifica a correspondência das injeções e a validade dos contratos de SOC/mobilidade, importa a V0.8 e as tentativas AC completas da V0.9 e calcula a fronteira não dominada existente. Não faz um novo fluxo de potência.

## 2. Primeira busca controlada com OpenDSS

```powershell
python .\opendss_rebuild\reotimizar_pareto_v10.py `
  --results .\results\replay_s0_s4_opendss `
  --v08-dir .\results\joint_opendss_v08 `
  --v09-dir .\results\joint_opendss_v09_teste `
  --output .\results\joint_opendss_v10 `
  --task run `
  --profiles balanced,under_guard,thermal `
  --max-starts 2 --max-iterations 1 `
  --max-candidates 6 `
  --bess-trust-kw 250 --v2g-trust-kw 200 `
  --delta-kw 100
```

Se a V0.9 contiver uma calibração confiável para a V0.8 a 100 kW, o script a reaproveita. Para as outras sementes AC, recalibra as sensibilidades com os reguladores e capacitores congelados durante as perturbações. A cada solução, executa o OpenDSS nas 24 horas com controles automáticos e registra **todas** as violações restantes.

É esperado que alguns MILPs sejam inviáveis na aproximação local, sobretudo os perfis de proteção da tensão: esses erros ficam isolados por candidato e não são interpretados como resultado físico.

## Indicadores de Pareto

Quatro objetivos AC **separados**, todos minimizados:

1. Severidade térmica **adicional em relação ao S0**, p.p.-h.
2. Severidade da subtensão **adicional em relação ao S0**, p.u.-h.
3. Severidade da sobretensão **adicional em relação ao S0**, p.u.-h.
4. Pico de importação na subestação, kW.

As violações já existentes em S0 são consideradas na métrica de agravamento por **hora, linha e fase** ou **hora, barra e fase**. O objetivo não substitui a obrigação de reportar violações absolutas em todos os cenários.

Os perfis `under_guard`, `over_guard` e `voltage_guard` adicionam **epsilon-limites sobre a soma das variáveis de folga PREVISTAS na linearização**, calculados em relação ao ponto de referência AC. Isso **não garante** que o limite também seja respeitado pelo replay AC, especialmente quando os taps se movem.

## Principais saídas

- `resumo_final.json`: estado, candidatos e limitações explícitas.
- `fronteira_pareto_ac.csv`: candidatos AC não dominados nas quatro dimensões.
- `todos_candidatos_ac.csv`: todos os candidatos importados e novos, inclusive dominados.
- `historico_tentativas.csv`: erros isolados por MILP/replay, parâmetros e resultados.
- `candidates/<nome>/dispatch_24h.csv`: despacho do BESS e frota.
- `candidates/<nome>/vehicle_dispatch.csv`: 150 veículos individualmente.
- `candidates/<nome>/source.csv`, `branches.csv`, `voltages.csv`, `regulators.csv`, `capacitors.csv`: resultados AC de 24h.
- `candidates/<nome>/ac_verification.json`: verificação de agravamentos além do S0.

Todos os benchmarks e as saídas V0.8/V0.9 permanecem intocados; a saída deve ser uma pasta nova e vazia.

## Validação científica adicional necessária para Applied Energy

- Verificar robustez com variação de carga, condições de pico, parâmetros de ampacidade e operação dos reguladores.
- Incluir análise de 15 minutos e atendimento EVCS e comparar S2/S3/S4 em bases homogêneas.
- Documentar efeitos de compensação reativa e limites dos transformadores de conexão, que não são cobertos integralmente por este protótipo.
- Fazer estudo de ablação e comparadores de operação não coordenada.
- Distinguir *solução não dominada encontrada* de *fronteira global de Pareto*: estas são soluções locais amostradas via MILP + replay OpenDSS.
- Jamais ocultar as violações de tensão e carregamento que persistirem no OpenDSS.
