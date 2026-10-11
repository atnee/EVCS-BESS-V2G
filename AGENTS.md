# Instruções para assistentes de IA (Claude Code, GitHub Copilot, Codex, Cursor)

Este arquivo é lido automaticamente pelo assistente de IA no VS Code. Ele vale para os três pesquisadores. O tutorial para as pessoas está em [docs/tutorial_vscode_ia.md](docs/tutorial_vscode_ia.md).

## O projeto

Pesquisa sobre o impacto de estações de recarga de veículos elétricos (EVCS) num alimentador de distribuição, e sobre a mitigação por bateria (BESS) e por *vehicle-to-grid* (V2G). Todos os estudos usam o alimentador **IEEE 8500 nós** (redução de média tensão), resolvido com `pandapower.runpp_3ph`.

| Cenário | Conteúdo | Dono |
|---|---|---|
| S0 | Caso base, sem estações | — |
| S1 | Frota mista de 3.000 veículos (particulares + táxis/aplicativo), 27 MWh/dia em 75 estações públicas; sobrecarrega a rede | Ayrton (EVCS) |
| S2 | S1 + BESS; deve reduzir a sobrecarga do S1 | Kenia (BESS) |
| S3 | S1 com V2G | Cesar (V2G) |
| S4 | S1 + BESS + V2G, depois de S2 e S3 completos | os três |

Leia antes de propor qualquer mudança:
- [docs/metodologia.md](docs/metodologia.md): modelo, formulação, variáveis e unidades, solvers, indicadores e limitações.
- [docs/S2_BESS_Kenia.md](docs/S2_BESS_Kenia.md) e [docs/S3_V2G_Cesar.md](docs/S3_V2G_Cesar.md): o que o S2 e o S3 devem entregar e os critérios de "pronto".
- [src/evcs/README.md](src/evcs/README.md): exemplo de módulo documentado.

## Quem é o usuário e o que ele pode alterar

Descubra o dono pela branch atual (`git branch --show-current`) ou pergunte.

| Branch | Dono | Pode alterar livremente | Só com acordo dos três |
|---|---|---|---|
| `feature/bess-kenia` | Kenia | `src/bess/`, `configs/bess.yaml`, `tests/test_bess.py`, `notebooks/02_bess_kenia.ipynb`, `src/bess/README.md` | todo o resto |
| `feature/v2g-cesar` | Cesar | `src/v2g/`, `configs/v2g.yaml`, `tests/test_v2g.py`, `notebooks/03_v2g_cesar.ipynb`, `src/v2g/README.md` | todo o resto |
| `feature/evcs-ayrton`, `feature/ieee8500` | Ayrton | `src/evcs/`, `configs/evcs.yaml`, estudos do EVCS em `src/integration/` | `src/core/`, `src/network/`, contratos |

**Áreas compartilhadas** exigem acordo dos três:
- `src/core/`, `src/network/` e `src/integration/` (coordenador e cenários);
- `configs/network.yaml`, que define o alimentador de todos;
- o bloco `station:` de `configs/evcs.yaml`, que é a estação usada pelo V2G;
- os dados em `data/`.

Se a tarefa exigir mexer numa dessas áreas, **pare e explique ao usuário o que precisa mudar e por quê**, em vez de alterar.

## Contratos que não podem quebrar

O coordenador chama cada módulo só por estas funções. Nome, parâmetros e retorno `(Profile, tabelas)` com os indicadores em `profile.metadata["kpis"]` ficam iguais:

```python
evcs.runner.run_integrated(config_dir, grid)
bess.runner.run_integrated(config_dir, grid, demand_kw)
v2g.runner.run_integrated(config_dir, grid, evcs_profile, enable_v2g)
```

Algoritmos novos entram como **estratégias** em `src/<módulo>/strategies.py`:
1. Copie `template` para uma função nova.
2. Registre-a em `STRATEGIES`.
3. Selecione-a com `strategy:` no YAML do módulo.

Contratos das estratégias:

| Módulo | Assinatura | Como montar o resultado |
|---|---|---|
| BESS | `fn(battery, demand_kw, config, dt_h) -> Dispatch` | `bess.model.simulate(battery, commands_kw, dt_h, final_soc=battery.soc_initial)` |
| V2G | `fn(station, grid, vehicles, independent_evcs_kw, independent_ids, discharge_mask, enable_v2g) -> Profile` | `fleet_profile(...)` com a matriz veículos × passos |

`simulate` e `fleet_profile` validam limites, SOC, reserva de mobilidade e portas. Não contorne essas validações; se uma falhar, o algoritmo está errado. `tests/test_strategies.py` testa toda estratégia registrada.

## Convenções físicas (verifique em todo código novo)

**Unidades:** potência em kW (kvar), energia em kWh, tensão em pu, corrente em A, carregamento em %, tempo em h (`dt_h`), custos em USD.

**Sinal:** injeção positiva = entrega à rede (descarga); negativa = consumo (carga). Na BESS, comando positivo descarrega.

**Horizonte:**
- Grade de tempo com fuso −06:00.
- Integração com passo de 1 h; estudos do EVCS com 15 min (96 passos).
- Estados (energia, SOC) têm `T+1` valores; potências têm `T`.
- O instante `t` é o início do intervalo.

**Energia:** `sum(P) * dt_h`. Nunca some potências sem `dt_h`, e confira se o passo é 1 h ou 15 min.

**Fases:** a potência de um ativo é dividida igualmente entre as fases declaradas. Tensões e carregamentos usam só as fases físicas (`physical_phase`).

**Limites de referência:**
- Tensão de 0,95 a 1,05 pu (ANSI C84.1, faixa A).
- Linhas e equipamentos até 100 %.
- PRODIST Módulo 8 como segunda régua.
- Nenhuma dessas é a norma de Honduras; diga isso quando citar.

**Reguladores:** compare sempre as duas visões: reguladores atuando e taps travados no S0 (`--frozen-taps` na integração). Os reguladores escondem parte do efeito.

## Comandos

Rode na raiz do repositório. No PowerShell, sem instalar o pacote, defina `$env:PYTHONPATH="src"` antes; com `pip install -e ".[dev]"` não precisa.

| O quê | Comando | Tempo |
|---|---|---|
| Módulo isolado, sem rede | `python -m bess` · `python -m v2g` · `python -m evcs` | segundos |
| Testes do módulo (rodar sempre) | `python -m pytest tests/test_bess.py tests/test_strategies.py tests/test_module_independence.py -q` (trocar `bess` por `v2g`) | ~5 s |
| Suíte sem o teste longo da integração | `python -m pytest -q --deselect tests/test_integration.py::TestIntegration::test_all_scenarios_and_end_states` | ~10 min |
| Suíte completa (antes do PR) | `python -m pytest -q` | ~12 min |
| Integração S0–S4 na rede | `python -m integration.coordinator --output results/ieee8500/integration --frozen-taps` | ~15 min |
| Refazer figuras sem simular | `python -m integration.redraw` | ~3 min |
| S1 do EVCS | `python -m integration.s1_study` | ~35 min |
| Triagem do EVCS (varredura de rede) | `python -m integration.evcs_screening --resweep` | ~1,5 h |

**Execuções longas:**
- Nunca inicie integração, S1 ou triagem sem o usuário pedir; avise o tempo antes.
- Para mudar só um gráfico, altere o código do gráfico e rode `integration.redraw`; não rode a simulação de novo.
- Execuções longas vão em segundo plano, com log em arquivo.
- `--workers N` (ou `EVCS_WORKERS`) controla os processos paralelos.

## Como escrever código

- Imite o código ao redor: nomes, densidade de comentários, estilo. Código, docstrings e comentários em inglês.
- Funções pequenas e puras quando possível.
- Parâmetros novos vão no YAML do módulo, com um comentário dizendo **se é dado (com fonte) ou hipótese**. Nada de número mágico no código.
- Não adicione dependências sem combinar. Já existem: numpy, pandas, scipy, pandapower, networkx, matplotlib, PyYAML. O MILP do EVCS usa `scipy.optimize.milp` (HiGHS); use o mesmo solver para otimizações lineares inteiras.
- Todo comportamento novo ganha teste em `tests/`, com caso analítico simples quando possível (balanço de energia, SOC final, limites).
- Figuras:
  - uma figura por arquivo, com texto em inglês;
  - funções que retornam `{nome: Figure}`, salvas com `save_figures(figures, pasta)` (`integration.s0_study`);
  - nome com prefixo do assunto (`demand_`, `voltage_`, `bess_`, `v2g_`...).
- Relatórios, READMEs e documentação em **português**, exceto os documentos da Kenia e do Cesar (`docs/S2_BESS_Kenia.md`, `docs/S3_V2G_Cesar.md`), em **espanhol**.

## Rigor científico

- Não invente números, resultados, referências nem dados. Todo número citado em documento deve vir de um arquivo em `results/` ou de um cálculo mostrado.
- Não ajuste parâmetros para "dar certo". Se a mitigação não resolve, reporte o que não resolve e por quê.
- Separe o que está implementado do que é proposta. Desvios dos dados IEEE ou das referências ficam documentados.
- Se um teste falhar, mostre a saída e investigue a causa; não apague nem afrouxe o teste.
- Avise quando um resultado depender de hipótese frágil (custos, curva de carga, frota, local da BESS).

## Git

- Trabalhe só na branch do dono. Nunca faça commit direto em `main` nem em `feature/ieee8500`, nunca use `--force` e nunca reescreva histórico publicado.
- Faça commit e push **só quando o usuário pedir**. Mensagem em inglês, dizendo o que mudou e por quê.
- A versão comum mais recente do projeto está **hoje em `origin/feature/ieee8500`** (a `main` ainda não tem o IEEE 8500). Para atualizar a branch: `git fetch origin` e `git merge origin/feature/ieee8500`. Quando ela entrar na `main`, use `origin/main`.
- Em conflito numa área compartilhada, não escolha um lado sozinho: mostre o conflito ao usuário.
- **Fora do Git:**
  - `results/`: saídas geradas, que cada um regenera;
  - dados reais de Honduras;
  - saídas de notebooks (limpe as células antes do commit);
  - `.env` e senhas.
- PR: da branch do dono para a branch comum, preenchendo `.github/pull_request_template.md`.

## Antes de dizer que terminou

- [ ] Testes do módulo passam; para PR, `python -m pytest -q` passa.
- [ ] Contrato `run_integrated` e assinatura da estratégia inalterados.
- [ ] Unidades, sinal e `dt_h` conferidos.
- [ ] Para BESS: SOC final igual ao inicial. Para V2G: energia de saída garantida a todo veículo.
- [ ] Parâmetros novos no YAML, com origem.
- [ ] README do módulo atualizado se o comportamento mudou.
- [ ] Nada de `results/`, dados reais ou saída de notebook no commit.
- [ ] Resumo ao usuário diz o que foi verificado, o que não foi e o que falta.
