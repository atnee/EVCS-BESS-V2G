# Protocolo de validação — objetivo *Applied Energy*

## Etapa A — Integridade dos dados (implementada)

- Verificar que os 2.850 VEs de eletropostos e 150 V2G somam 3.000 sem duplicação.
- Conferir BESS Kenia: 2,2 MW / 7,2 MWh, SOC inicial 0,30, barra `l3029502`.
- Conferir V2G Cesar: estratégia `cesar_v1`, 150 veículos, janela 17–20h,
  estacionamento em `m1069504`, 154 portas e potência 1.694 kW.
- Hash dos YAML, rede primária normalizada e `Master.dss`.
- Exigir sinais corretos: consumo negativo, geração/despacho positivo.
- Não permitir cargas/injeções em fase ausente, passo não-horário, ou dupla
  aplicação do perfil de carga.

## Etapa B — Circuito IEEE 8500 original (motor implementado; execução pendente)

- Compilar `Master.dss`, manter transformadores de serviço e triplex.
- Fonte original 1,05 p.u., transformador delta–estrela; reguladores independentes
  por fase e bancos capacitivos de acordo com seus controles.
- Solucionar 24 horas com série de carregamento **sintética** de `ieee8500.yaml`.
- Conferir 24 soluções convergidas, potência na subestação, perdas,
  tensões físicas MT, correntes físicas das linhas e estados de controle.
- Executar sensibilidade com fonte 1,00 p.u. **em experimento separado**, não
  misturar os dois regimes.
- Comparar com a referência IEEE disponível e documentar qualquer diferença.

## Etapa C — Replay da operação da equipe (motor implementado; execução pendente)

Executar as mesmas injeções horárias dos cinco cenários:

| Cenário | Eletropostos | BESS | V2G |
|---|---|---|---|
| S0 | Não | Não | Não |
| S1 | Sim | Não | V2G sem descarga, conforme contrato original |
| S2 | Sim | Sim | Sem descarga |
| S3 | Sim | Não | Sim |
| S4 | Sim | Sim | Sim |

Reaproveitar indicadores dos módulos em `kpis.json` e tabelas `soc.csv`,
`dispatch.csv`, `vehicle_energy.csv`, `public_station_power.csv` sempre que
os respectivos módulos os produzirem.

Pontos cruciais:

- **Não** se deve exigir que o OpenDSS reproduza a redução de média tensão do
  pandapower. O objetivo é testar a viabilidade física de cada injeção.
- Sobrecarregamentos novos devem ser identificados por `(hora,linha,fase)`
  em relação a S0, e resumidos por linhas distintas.
- S4 deve ser comparado a **S1 e S0**: uma melhoria em relação a S1 não garante
  ausência de sobrecargas absolutas ou novas sobrecargas frente a S0.
- A distinção `overload_samples` vs. `overloaded_unique_lines` é obrigatória.
- A contagem de cargas sob 0,95 ou acima de 1,05 p.u. é por fase/instante,
  não por barras únicas.
- Verificar o corredor de `m1069504` por fase e por hora.

## Etapa D — Reconstrução da otimização (NÃO implementada)

Os coeficientes de `configs/bess.yaml/line_limit` foram calibrados com pandapower.
Eles **não** constituem restrições validadas do OpenDSS e precisam ser recalculados.

Procedimento recomendado:

1. Construir base S3 AC convergida (com eletropostos e V2G, sem BESS).
2. Perturbar BESS trifásico em `l3029502` com uma injeção conhecida, mantendo
   os reguladores em regime coerente e registrando mudanças nas correntes
   de **cada fase** das linhas críticas.
3. Calibrar sensibilidades por hora e fase, validar com perturbações fora da
   amostra e capturar mudanças nos taps/estados dos capacitores.
4. Resolver LP/MILP de Kenia com limites de potência, energia, SOC, eficiência,
   ciclagem e potência terminal; acrescentar limites térmicos e de tensão.
5. Validar por OpenDSS cada despacho AC encontrado; se falhar, enriquecer
   restrições/cortes e resolver novamente até convergir sem violações ou
   documentar inviabilidade.
6. Verificar o V2G de Cesar com compromissos de mobilidade, SOC individual,
   disponibilidade, limite de 154 portas, potência total 1.694 kW e energia
   de viagem. A mesma frota é usada em S1–S4; evitar contabilização dupla.

## Etapa E — Critérios para inserir no artigo (NÃO implementada)

- Números e figuras apenas de simulações **convergidas e rastreáveis**.
- Métricas: `peak_import_kw`, `import_kwh`, `export_kwh`, `loss_kwh`,
  `v_min_pu`, `v_max_pu`, `loading_max_pct`, `voltage_violation_samples`,
  `overload_samples`, linhas/fases únicas, custo, CAPEX, throughput/ciclos,
  energia entregue V2G, atendimento EVCS e SOC/mobilidade.
- Testes: série 24h, resolução 15min, demanda, diversidade de comportamento,
  bandas do regulador, capacidade BESS, V2G, disponibilidade e cenários críticos.
- Apresentar limitações reais: dados hipotéticos/IEEE e localização de
  eletropostos, ausência de calibração de campo da rede hondurenha e
  escolha de ampacidades sintéticas.
- Só usar “eliminação de congestionamento” se a reotimização AC demonstrar
  que as linhas anteriormente não sobrecarregadas continuam dentro do limite
  e a regra de comparação for explicitada.
