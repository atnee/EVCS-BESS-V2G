# Reprodução da auditoria

Execute na raiz do Projeto B. Dependências já presentes no ambiente usado: pandas, numpy, PyYAML, pandapower, opendssdirect.py e pytest. Nenhuma foi instalada nesta auditoria.

```powershell
python validation_applied_energy_final/audit.py
python validation_applied_energy_final/verify_results.py
python validation_applied_energy_final/energy_check.py
$env:PYTHONPATH='src;validation_applied_energy_final'
python -m pytest validation_applied_energy_final/test_audit.py tests/test_bess.py tests/test_v2g.py tests/test_strategies.py tests/test_module_independence.py -q
```

O inventário lê as duas pastas e pode demorar conforme o volume dos resultados. Os caminhos são resolvidos a partir do script, não do diretório alterado pelo OpenDSS. Os scripts de auditoria escrevem somente nesta pasta; não alteram dados de origem. Rodá-los novamente atualiza os produtos derivados desta auditoria; preserve uma cópia desta pasta se quiser comparar auditorias em datas diferentes.

O comando abaixo executou quatro snapshots nativos em poucos segundos e gravou [native_check.log](native_check.log). O diretório de saída tem proteção contra sobrescrita: `mkdir(exist_ok=False)`. Para nova execução, faça uma cópia do pacote para outro diretório de auditoria na raiz do B e execute o script daquela cópia. Não apague os resultados históricos para repetir um teste.

```powershell
python validation_applied_energy_final/native_check.py
```

[native_check.py](native_check.py) reaproveita `ODSSEngine` e a inspeção de fases existentes; a nova função apenas corrige a seleção de tensões e adiciona registros de balanço. O circuito é recompilado para cada snapshot, o diretório corrente é restaurado em `finally`, e exceções são persistidas com traceback. Os quatro casos são snapshots independentes, não a trajetória temporal de um dia.

Metadados do motor, entradas, hashes e condições estão em [native_s0/manifest.json](native_s0/manifest.json). As linhas/correntes usam ampacidade do estudo; ela não é rating IEEE validado em campo. P/Q da fonte usam importação positiva. As injeções do projeto continuam positivas para entrega à rede. As somas de energia/severidade diárias verificadas usam `dt_h=1`; a campanha também contém passos de 15 min e os picos não precisam de integração.

## Testes executados

[tests.log](tests.log): **70 passed, 35 subtests passed**, incluindo cinco testes novos contra inclusão de fase não física, NaN, duplicação, inconsistência de comprimentos e inclusão indevida de transformadores no indicador de linhas. Os testes BESS/V2G, estratégias e independência passaram. Não se executou a suíte completa de integração nem se abriu PR.

## Rastreabilidade

- [comparacao_arquivos.csv](comparacao_arquivos.csv): igualdade e exclusividade entre pastas.
- [validacao_hashes_historicos.csv](validacao_hashes_historicos.csv): sete fingerprints da validação anterior conferidos.
- [campanha_315_verificada.csv](campanha_315_verificada.csv): recálculo por configuração/cenário.
- [piloto_opendss_recalculado.csv](piloto_opendss_recalculado.csv): piloto histórico nativo reaproveitado.
- [v301_recalculado.json](v301_recalculado.json): severidades absolutas recalculadas dos dados por fase.

Os arquivos numerados 04–06 são evidência histórica explicitamente rotulada. As execuções novas ficam separadas em `native_s0/`. Nenhum arquivo vazio foi usado para aparentar conclusão. Consulte o relatório final para distinguir etapas verificadas e pendentes.

## Piloto novo com os perfis exatos

[pilot.py](pilot.py) executou S0/S1/S4 automático e S1/S4 com taps horários do S0, mantendo controles nativos dos capacitores: 120 snapshots, sem chamar otimizadores. [pilot.log](pilot.log) e [exact_profile_pilot/manifest.json](exact_profile_pilot/manifest.json) registram a conclusão; o diretório também recusa sobrescrita. Para repetir, use uma cópia do pacote em outra pasta na raiz do B, como no teste nativo.

```powershell
python validation_applied_energy_final/pilot.py
```

As séries completas de tensões, correntes, fonte, taps, capacitores e balanço foram salvas progressivamente por hora. Entradas históricas permaneceram iguais por hash. O limite diagnóstico de balanço de 1 kW/1 kvar foi atendido em todos os passos; isso não transforma violações elétricas em aprovação.

[finalize.py](finalize.py) gera as métricas P/Q complementares, verifica novamente os arquivos históricos e cria o ZIP de fontes em modo exclusivo. Não repita esse comando sobre um ZIP já existente: preserve o snapshot e use outra pasta. [preservacao_historico.json](preservacao_historico.json) registra 7.732 arquivos inalterados.

## Experimentos de melhoria posteriores

[10_resultados_melhoria.md](10_resultados_melhoria.md) contém a decisão e a comparação de despacho/localização/quantidade. [improvement_settings.yaml](improvement_settings.yaml) registra as hipóteses. Foram usados os algoritmos existentes, sem modificar módulos oficiais.

Para reproduzir sem sobrescrever evidências, prepare outra pasta na raiz do B contendo **somente os scripts e YAMLs**, sem copiar diretórios de saída. Execute primeiro `pilot.py` para criar a referência; depois, nesta ordem, `improve.py`, `check_alternatives.py`, `validate_distributed.py`, `freeze_candidates.py` e `diagnose_controls.py`. Cada nome se refere ao script da nova pasta. O estudo requer os dados históricos nas mesmas pastas A/B. `freeze_candidates.py` termina com erro no último caso (250 VEs), reproduzindo a falha de controle documentada; isso é resultado esperado da campanha atual, não teste aprovado.

O primeiro ensaio de `improve.py` teve uma incompatibilidade de configuração da janela/tarifa, preservada no log inicial. O código atual mantém o contrato S1 e passa a janela alternativa somente ao otimizador. As pastas `retry_01` são registros dessa correção de preparação, não novas versões de otimização. A retomada foi específica dessa falha; para nova campanha, use outra pasta em vez de reaproveitar saídas existentes.

```powershell
$env:PYTHONPATH='src;validation_applied_energy_final'
python -m pytest validation_applied_energy_final/test_improvement.py opendss_rebuild/tests/test_joint_optimization.py -q
```

Resultado executado: 11 testes passaram. Logs de execução e falhas permanecem separados dos relatórios de aprovação.
