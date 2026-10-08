# Trabalho colaborativo

Os três trabalham no mesmo repositório, cada um em sua branch. Evitar editar simultaneamente o mesmo notebook. Código compartilhado fica nos módulos Python.

| Responsável | Pasta principal | Branch |
|---|---|---|
| Ayrton | src/evcs/ | feature/evcs-ayrton |
| Kenia | src/bess/ | feature/bess-kenia |
| Cesar | src/v2g/ | feature/v2g-cesar |

## Independência dos módulos
Cada equipe pode alterar seu algoritmo, YAML, testes e notebook sem executar os outros módulos:

| Equipe | Arquivos de trabalho | Executar | Testes do módulo |
|---|---|---|---|
| EVCS | `src/evcs/`, `configs/evcs.yaml`, notebook 01 | `python -m evcs` | `python -m pytest tests/test_evcs.py -q` |
| BESS | `src/bess/`, `configs/bess.yaml`, notebook 02 | `python -m bess` | `python -m pytest tests/test_bess.py -q` |
| V2G | `src/v2g/`, `configs/v2g.yaml`, notebook 03 | `python -m v2g` | `python -m pytest tests/test_v2g.py -q` |

As saídas individuais ficam em `results/modules/evcs/`, `bess/` e `v2g/`. Execuções de equipes diferentes podem usar a mesma pasta-pai sem sobrescrever arquivos das outras. Para duas experiências simultâneas do **mesmo** módulo, use pais diferentes, por exemplo `python -m bess --output results/experimento_kenia_02`.

Cada YAML possui seu próprio horizonte temporal. BESS usa `standalone_demand_kw`; V2G usa `standalone.station`, `standalone.independent_evcs_kw` e IDs de ocupação. São entradas fixas para desenvolvimento local, não previsões atualizadas automaticamente pelas outras equipes. O comando independente não roda fluxo de potência e não atesta viabilidade elétrica.

`core.assets.Battery` e `core.assets.Station` são contratos imutáveis compartilhados. V2G não importa implementações EVCS/BESS. Os caminhos antigos `bess.model.Battery` e `evcs.model.Station` permanecem como aliases para compatibilidade. Interfaces comuns e mudanças em `core/`, `network/` e `integration/` continuam sendo decisões coordenadas.

### Entrega para integração
Preserve o contrato `Profile`: potência positiva injeta na rede, negativa consome, em kW/kvar por barra/fase/intervalo. Um pacote contém `profile.csv` e `manifest.json` com versão, horizonte, IDs de ativos/veículos, métricas e hash do perfil. `core.artifacts.read_profile_bundle(path, network)` valida e recupera esse perfil; `integration.coordinator.combine` rejeita horizontes incompatíveis e veículos/ativos duplicados.

O coordenador executa S0–S4 e publica `results/ieee123/modules/<módulo>/<cenário>/`. A soma das três contribuições recompõe exatamente as injeções integradas. Módulos desativados têm perfis zero e `context.active=false`; V2G inclui a frota unidirecional em S1/S2, identificada por `v2g_discharge_enabled=false`.

O coordenador chama apenas uma função por módulo. Todas retornam `(Profile, tabelas)`, e `Profile.metadata["kpis"]` traz as colunas do `summary.csv` (`capex_usd` é somado entre módulos). Fora dessas assinaturas, cada equipe pode renomear, dividir ou substituir seu código; `test_integration_uses_only_run_integrated` impede novos acoplamentos:

| Módulo | Função | Recebe do coordenador |
|---|---|---|
| EVCS | `evcs.runner.run_integrated(config_dir, grid)` | horizonte; deve incluir `metadata["station"]` |
| V2G | `v2g.runner.run_integrated(config_dir, grid, evcs_profile, enable_v2g)` | perfil EVCS (estação e demanda atendida) |
| BESS | `bess.runner.run_integrated(config_dir, grid, demand_kw)` | demanda agregada antes do BESS |

Alterar essas assinaturas ou as chaves de `kpis` é mudança de interface compartilhada e exige revisão dos três.

Na execução integrada, infraestrutura EVCS e demanda atendida são entradas do V2G; demanda nominal da rede mais EVCS/frota alimenta o BESS. Portanto, resultados integrados podem mudar quando outro módulo muda. O alvo é independência de implementação, testes e arquivos, preservando coerência elétrica. Os horizontes dos três YAML devem coincidir para integrar; divergências geram erro explícito. O bloco `standalone` do V2G e a curva `standalone_demand_kw` do BESS não são usados pelo coordenador.

Antes de entregar: executar testes do próprio módulo e `python -m pytest tests/test_module_independence.py -q`. A integração final deve passar pela suíte completa e pelo fluxo IEEE123.

## Iniciar localmente
Extraia o ZIP e abra a pasta EVCS-BESS-V2G. Se ainda não houver um repositório Git:
```bash
git init -b main
git add .
git commit -m "Initial research framework"
git branch feature/evcs-ayrton
git branch feature/bess-kenia
git branch feature/v2g-cesar
```
Não foram criados remotos nem executado push nesta entrega. Para publicar posteriormente, criar o repositório na conta autorizada, conferir os arquivos staged, adicionar o remote correto e enviar as branches. Cada colaborador clona sua própria cópia.

## Ciclo diário (exemplo Ayrton)
```bash
git switch feature/evcs-ayrton
git fetch origin
git merge origin/main
python -m pytest -q
git add src/evcs tests/test_evcs.py
git diff --cached
git commit -m "Describe EVCS change"
```
Quando o remoto estiver configurado e autorizado, publicar a branch e abrir PR para main. Descrever problema, alteração, evidências e impacto nas interfaces. Para Kenia/Cesar substituir branch e pasta.

## Conflitos
Antes de integrar, salvar alterações em commit. Em conflito, abrir os arquivos sinalizados por `git status`, escolher conscientemente a versão correta (ou combinar), remover marcadores, rodar testes, `git add` e `git commit`. Para desistir da integração: `git merge --abort`. Não resolver conflitos em interfaces compartilhadas sem os três revisarem. Não usar force-push como rotina.

## Proteção recomendada (configuração ainda não aplicada)
Proteger main com Pull Requests, check `tests` aprovado e revisão. Preencher e descomentar CODEOWNERS com usernames reais. Para alterações compartilhadas, combinar revisão dos três. CODEOWNERS lista responsáveis, mas a exigência de todos não surge automaticamente: ajustar regras/revisões e processo. Restringir bypass e force-push conforme acordo da equipe.

## Próximas tarefas
Ayrton: validar população de veículos independente, demanda horária, filas e critérios de seleção de barras. Evoluir dimensionamento EVCS.
Kenia: avaliar dimensionamento P/E, objetivos, tarifa, degradação e política de SOC terminal. Substituir heurística por despacho otimizado.
Cesar: definir disponibilidade, necessidades de viagem, sessões e degradação V2G. Refinar compartilhamento de carregadores preservando IDs únicos.
Todos: aprovar contrato, licença e hipóteses econômicas; validar o equivalente pandapower do IEEE123 contra resultados publicados; acordar interface XLSX com a concessionária e regras de confidencialidade.
