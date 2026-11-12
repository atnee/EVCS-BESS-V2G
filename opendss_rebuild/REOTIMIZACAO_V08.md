# Reotimização BESS–V2G no OpenDSS — v0.8 (protótipo de pesquisa)

## Objetivo científico

Construir uma NOVA versão do estudo de flexibilidade para uma possível submissão à **Applied Energy**, sem depender das sensibilidades de corrente/tensão calibradas anteriormente no pandapower. O manuscrito anterior, com o replay legado, é mantido para avaliação de um periódico distinto e não é reclassificado como um resultado OpenDSS otimizado.

A implementação é **inicial**: não demonstra, por si só, que o despacho satisfaça os limites elétricos nem que esteja pronto para publicação.

## Instalação

Extraia o ZIP na **raiz** do repositório `EVCS-BESS-V2G`. A estrutura adicionada é:

```text
EVCS-BESS-V2G/
  opendss_rebuild/
    reotimizar_bess_v2g.py
    rebuild/joint_optimization.py
    tests/test_joint_optimization.py
    REOTIMIZACAO_V08.md
    DECISAO_EDITORIAL.md
```

Preserva `configs/`, `src/`, `data/` e todos os resultados existentes. Requer as versões anteriores de `opendss_rebuild` que já executaram o replay de S0 a S4, além de `opendssdirect.py`, `scipy`, `pandas`, `numpy` e `PyYAML`. Não altera os YAMLs da equipe.

Em PowerShell, **na raiz**:

```powershell
python -m pip install -r .\opendss_rebuild\requirements.txt
python -m pip install scipy
python -m pytest -q .\opendss_rebuild\tests\test_joint_optimization.py
```

### Primeiro: recalibrar o alimentador (todas as 24 horas)

```powershell
python .\opendss_rebuild\reotimizar_bess_v2g.py `
  --results .\results\replay_s0_s4_opendss `
  --output .\results\joint_opendss_v08 `
  --task calibrate
```

Os resultados são escritos em `results/joint_opendss_v08/sensibilidades/`. A etapa recompila a rede original IEEE 8500, reproduz **S1 completo em ordem cronológica** e compara tensão/corrente/fonte ao replay existente. Rejeita a calibração se esse S1 não coincidir.

Para cada hora, mede a resposta a +200 kW distribuídos igualmente pelas três fases no ponto BESS e no ponto V2G. Os reguladores/capacitores permanecem **congelados apenas durante as perturbações**; a referência S1 segue com controles automáticos. Isso não equivale a usar os controles congelados no replay final.

### Depois: resolver o MILP conjunto e verificar S4 em AC

```powershell
python .\opendss_rebuild\reotimizar_bess_v2g.py `
  --results .\results\replay_s0_s4_opendss `
  --output .\results\joint_opendss_v08 `
  --task optimize --mode S4
```

A execução final contém uma **otimização de horizonte completo** e a verificação **horária, cronológica, de 24 horas com reguladores e capacitores automáticos** no OpenDSS. Os arquivos são gerados em `results/joint_opendss_v08/S4/`.

Opcionalmente, com a MESMA calibração, podem ser otimizados comparadores independentes:

```powershell
python .\opendss_rebuild\reotimizar_bess_v2g.py --results .\results\replay_s0_s4_opendss --output .\results\joint_opendss_v08 --task optimize --mode S2
python .\opendss_rebuild\reotimizar_bess_v2g.py --results .\results\replay_s0_s4_opendss --output .\results\joint_opendss_v08 --task optimize --mode S3
```

S2: otimiza o BESS, mantendo EXATAMENTE o carregamento individual da frota S1. S3: otimiza o V2G com o BESS em zero. S4: otimiza ambos simultaneamente. O cenário S1 é sempre a referência elétrica e o S0 é a referência para distinguir agravamentos e violações preexistentes.

### Diagnóstico combinado para execução direta

Se preferir, uma única chamada executa a calibração e a otimização S4:

```powershell
python .\opendss_rebuild\reotimizar_bess_v2g.py --results .\results\replay_s0_s4_opendss --output .\results\joint_opendss_v08 --task all --mode S4
```

**A saída deve ser uma pasta nova.** Não sobrescreva S0/S1/S4 legados. O script recusa apagar arquivos existentes por padrão.

## Contratos físicos e operacionais

- **Kenia:** BESS 2.200 kW/7.200 kWh em `l3029502`, SOC inicial/final de 30%, limites de 10–90%, eficiências 95%/95%. A variável binária impede carregar e descarregar simultaneamente.
- **Cesar:** 150 veículos na estação `m1069504`, disponibilidade por veículo, potência AC de 11 kW, SOC e energia de viagem, recarga noturna, reservas individuais e requisito energético na partida. Descarga somente 17:00–20:00 (passos 17, 18, 19) e bloqueio de recarga 17:00–22:00. Usa o validador original `v2g.strategies.fleet_profile` antes da verificação elétrica.
- **Estação:** 154 portas e conexão 1.694 kW, considerando a demanda independente do ponto. O carregamento original da frota S1 é removido antes de inserir o novo despacho (evita dupla contagem).
- **Elétrica:** monitoramento por **linha e fase** e por **barra e fase**. Excedentes térmicos e de tensão são representados por slacks penalizados; opcionalmente respeita o patamar de violação preexistente no S0 em cada hora. Todos os resultados elétricos finais são extraídos novamente do OpenDSS.
- **Custos:** preços e remuneração de `configs/v2g.yaml`, degradação BESS de `configs/bess.yaml`, com pesos explícitos do MILP. São hipóteses de pesquisa, não tarifas operacionais verificadas.

## Formulação resumida

Para cada hora `t`, o MILP decide `c_B,t`, `d_B,t` e o SOC do BESS; para cada veículo `i` e hora conectada decide `c_i,t`, `d_i,t` e energia armazenada. O vetor agregado do V2G é a soma das descargas menos as recargas individuais.

A predição elétrica segue a linearização local por diferenças finitas de OpenDSS:

```text
L_pred,l,ph,t = L_S1,l,ph,t + a_B,l,ph,t * P_B,t
                            + a_V,l,ph,t * (P_V,t - P_V,S1,t)
V_pred,b,ph,t = V_S1,b,ph,t + b_B,b,ph,t * P_B,t
                            + b_V,b,ph,t * (P_V,t - P_V,S1,t)
```

O MILP minimiza uma soma ponderada de excesso térmico, violações de tensão, pico de potência, energia movimentada pela bateria e saldo de custos da frota. As restrições elétricas são **suaves**: se não houver solução que resolva todas as violações com a flexibilidade disponível, o algoritmo pode terminar com slacks positivos. Isso é registrado sem simular falsa conformidade.

## Saídas de S4

- `dispatch_24h.csv` — injeções e consumos líquidos do BESS e da frota, por hora.
- `bess_soc.csv` — energia e SOC do BESS nas 25 fronteiras temporais.
- `vehicle_dispatch.csv` e `vehicle_energy.csv` — decisões e SOC individuais dos 150 veículos.
- `financial_and_fleet_kpis.json` — energia, custos hipotéticos, ciclos, CAPEX fixos.
- `milp_diagnostic.json` — status, lacuna de otimalidade e slacks elétricos previstos.
- `injections_reoptimized.csv` — perfil assinado, por hora, barra e fase, aplicado ao circuito.
- `source.csv`, `branches.csv`, `voltages.csv`, `regulators.csv`, `capacitors.csv` — AC OpenDSS com controles automáticos.
- `comparison_24h.csv`, `thermal_aggravations_vs_s0.csv`, `voltage_aggravations_vs_s0.csv` e `ac_verification.json` — auditoria independente do MILP.
- `manifest.json` — hashes dos arquivos, origem e limitações.

## Critérios antes de publicar na Applied Energy

1. **Não chamar o despacho de eletricamente viável** se `ac_verification.json` registrar agravamentos importantes ou violações remanescentes. Reportar explicitamente os resíduos.
2. **Não afirmar equivalência global** do modelo linear só porque a superposição foi boa para 200 + 200 kW. O MILP pode selecionar comandos muito maiores; verificar erros por barra/fase/linha.
3. Se a verificação AC divergir, fazer iterações sequenciais: recalibrar as sensibilidades em torno do despacho proposto, resolver novamente e reexecutar AC até critério numérico/operacional previamente definido. A v0.8 ainda **não automatiza** essa realimentação.
4. Avaliar perfil de 15 minutos, ampacidades alternativas, reguladores/CapControls, cargas estocásticas e capacidade/transformadores de conexão DER antes de redigir resultados definitivos.
5. Os dados de carga são sintéticos e o IEEE 8500 não é uma rede hondurenha medida. Isso deve ficar evidente na metodologia.
6. Não ajustar limites/pesos após observar os resultados sem registrar a alteração como hipótese e análise de sensibilidade. Os pesos são parâmetros de preferência, não constantes físicas.

## Solução de problemas

- `S1 ... diferente na reprodução`: confirmar hashes dos YAMLs e do `Master.dss` e se o replay foi gerado com a mesma configuração de `opendss_rebuild/settings.yaml`.
- `Infeasible departure energy`: rever os dados de presença da frota e a política de não recarga 17–22; **não desativar silenciosamente** mobilidade/SOC.
- `MILP não produziu solução`: verificar limites do BESS, duração e disponibilidade da frota. Há slacks elétricos, portanto não seria esperado que a rede sozinha causasse inviabilidade.
- `S4_completely_satisfies_no_aggravation: false`: é um resultado negativo auditável, não um erro de instalação.
- Lentidão no OpenDSS: o script mede sensibilidades em todas as fases e em todas as 24 horas. Calibre uma vez; depois reutilize os CSVs com `--task optimize`.
