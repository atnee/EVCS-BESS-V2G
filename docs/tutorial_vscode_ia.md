# Trabalhar no VS Code com um assistente de IA

Para: **Kenia** (BESS, S2) e **Cesar** (V2G, S3) · De: Ayrton

Este tutorial mostra como preparar o VS Code e como usar um assistente de IA (Claude Code, GitHub Copilot ou outro) sem quebrar o trabalho dos outros.

O repositório já traz as regras para a IA. Elas estão no arquivo [AGENTS.md](../AGENTS.md), na raiz. Os assistentes leem esse arquivo sozinhos:
- **Claude Code** pelo `CLAUDE.md`, que importa o `AGENTS.md`;
- **Copilot** pela opção `chat.useAgentsMdFile`, que já vem ativada em `.vscode/settings.json`.

O que o `AGENTS.md` ensina à IA:
- o que é o projeto e qual pasta é de quem;
- os contratos que não podem quebrar;
- as convenções de unidades e de sinal;
- quais simulações demoram;
- como testar e quando fazer commit.

Você não precisa repetir isso nos pedidos.

## 1. Instalar (uma vez)

1. **Git** (https://git-scm.com) e **Python 3.12 ou mais novo** (https://www.python.org). No Windows, marque "Add python.exe to PATH".
2. **VS Code** (https://code.visualstudio.com).
3. **Assistente de IA**, um dos dois:
   - **Claude Code:** extensão "Claude Code" da Anthropic;
   - **GitHub Copilot:** extensões "GitHub Copilot" e "GitHub Copilot Chat". Use o modo *Agent* do chat.
4. Ao abrir a pasta do projeto, o VS Code sugere as extensões recomendadas (Python, Pylance, Jupyter, YAML, Mermaid). Aceite.

## 2. Preparar o projeto (uma vez)

No terminal do VS Code (PowerShell):

```powershell
git clone https://github.com/atnee/EVCS-BESS-V2G.git
cd EVCS-BESS-V2G
git switch feature/bess-kenia          # Cesar: feature/v2g-cesar
git merge origin/feature/ieee8500      # traz o IEEE 8500 e tudo o que já foi feito
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest tests/test_bess.py tests/test_strategies.py tests/test_module_independence.py -q
```

Cesar troca `bess` por `v2g` nos comandos.

**Branch de referência.** Hoje a versão comum mais recente é a `feature/ieee8500`, não a `main`. As branches de vocês e a `main` ainda estão 11 commits atrás dela. Quando ela entrar na `main`, avisamos e vocês passam a usar `origin/main` no lugar.

**Ajustes do VS Code.**
- `Ctrl+Shift+P` → **Python: Select Interpreter** → escolha o `.venv`.
- Se o PowerShell bloquear o `Activate.ps1`, rode uma vez: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
- A aba **Testing** (ícone de frasco) mostra e roda os testes. A pasta `.vscode/` do repositório já configura pytest, `PYTHONPATH=src` e a pasta dos notebooks.

## 3. Rotina de cada dia

```powershell
git switch feature/bess-kenia
git fetch origin
git merge origin/feature/ieee8500      # pega o que os outros entregaram
python -m bess                         # roda o módulo sozinho, em segundos
python -m pytest tests/test_bess.py tests/test_strategies.py tests/test_module_independence.py -q
```

Seu trabalho fica em:

| | Kenia | Cesar |
|---|---|---|
| Algoritmo | `src/bess/strategies.py` | `src/v2g/strategies.py` |
| Parâmetros | `configs/bess.yaml` | `configs/v2g.yaml` |
| Testes | `tests/test_bess.py` | `tests/test_v2g.py` |
| Notebook | `notebooks/02_bess_kenia.ipynb` | `notebooks/03_v2g_cesar.ipynb` |
| O que entregar | [S2_BESS_Kenia.md](S2_BESS_Kenia.md) | [S3_V2G_Cesar.md](S3_V2G_Cesar.md) |

## 4. Como pedir as coisas à IA

Trabalhe em ciclos curtos, nesta ordem:

1. **Planejar sem codar.** Peça um plano e leia antes de aprovar.
2. **Implementar um passo pequeno,** com teste.
3. **Rodar os testes** e ler a saída.
4. **Revisar a diferença** (`git diff` ou a aba Source Control) antes de aceitar.
5. **Commit** quando você estiver de acordo.

### Pedidos que funcionam bem

**Kenia (BESS).**
> Leia o docs/S2_BESS_Kenia.md e o docs/metodologia.md (seção 11). Sem escrever código ainda: proponha uma estratégia de despacho da BESS que reduza a ponta das 18–20 h do S1, termine o dia com o SOC inicial e respeite os limites. Liste as hipóteses e os parâmetros novos que iriam para o configs/bess.yaml, dizendo quais são dados e quais são hipóteses.

> Implemente o plano como a estratégia `kenia_v1` em src/bess/strategies.py, registrada em STRATEGIES, com um teste em tests/test_bess.py. Rode os testes do módulo e me mostre a saída.

> Explique o diff linha por linha: unidades de cada variável, sinal da potência e onde entra o dt_h.

**Cesar (V2G).**
> Leia o docs/S3_V2G_Cesar.md e o src/v2g/strategies.py. Proponha, sem codar, como escalar a frota V2G para 50–100 carros com chegada, saída e energia de viagem realistas. Diga o que precisa mudar na estação anfitriã (bloco `station:` do configs/evcs.yaml, que é do Ayrton) e o que fica só no configs/v2g.yaml.

> Implemente a estratégia `cesar_v1` usando fleet_profile, que deve garantir a energia de saída de todos os veículos. Rode tests/test_v2g.py e tests/test_strategies.py.

**Os dois.**
> Rode a integração com --frozen-taps em segundo plano e, ao fim, compare S2 (ou S3) com S1 em summary.csv: ponta, linha mais carregada, tensão mínima com e sem os reguladores, perdas.

A integração leva ~15 min. Peça só quando o algoritmo já passar nos testes.

### O que não aceitar da IA

- Mudanças em `src/core/`, `src/network/`, `src/integration/`, `configs/network.yaml`, ou na pasta de outra pessoa. A IA deve parar e avisar; se não avisar, recuse a mudança e fale com o grupo.
- Mudança na assinatura de `run_integrated` ou da estratégia.
- Teste apagado, afrouxado ou marcado como `skip` para "passar".
- Número no relatório que não veio de um arquivo em `results/` ou de um cálculo que ela mostrou.
- Parâmetro ajustado só para o resultado ficar bonito, sem justificativa.
- Execução longa (integração, S1, triagem de 1,5 h) que você não pediu.
- Commit ou push que você não pediu, ou qualquer `--force`.

### Dicas

- Peça para a IA **dizer o que verificou e o que não verificou** no fim de cada tarefa.
- Se a resposta usar unidade ou sinal estranho, pergunte. As convenções estão na seção 11.0 da [metodologia](metodologia.md): injeção positiva entrega energia à rede; potência em kW; energia em kWh.
- Para dúvidas sobre o EVCS (demanda das estações, onde estão, por que o S1 sobrecarrega), aponte a IA para o [src/evcs/README.md](../src/evcs/README.md) e para o `results/ieee8500/resumo/LEIA-ME.md` (gerado localmente).
- Uma conversa por tarefa. Conversas muito longas perdem o contexto; comece outra e diga onde parou.

## 5. Resultados

As saídas ficam em `results/`, fora do Git: cada um gera as suas. Comece por `results/ieee8500/resumo/` e pelo mapa em [results/README.md](../results/README.md).

Para mudar só a aparência de um gráfico, não simule de novo: altere a função do gráfico e rode `python -m integration.redraw` (~3 min).

## 6. Entregar (Pull Request)

1. `python -m pytest -q`: a suíte completa leva ~12 min (a maior parte é o teste da integração) e precisa passar.
2. Limpe as saídas do notebook: **Clear All Outputs** e salvar.
3. Faça o commit só dos seus arquivos e o push da sua branch.
4. No GitHub, abra o Pull Request da sua branch para a `feature/ieee8500` e preencha o modelo: problema, impacto nos outros módulos, evidências.
5. Os critérios de "pronto" estão na seção 6 do seu documento: [S2_BESS_Kenia.md](S2_BESS_Kenia.md) ou [S3_V2G_Cesar.md](S3_V2G_Cesar.md).

## 7. Problemas comuns

| Sintoma | Causa e solução |
|---|---|
| `ModuleNotFoundError: No module named 'bess'` | O pacote não foi instalado, ou o terminal foi aberto antes do `.venv`. Rode `python -m pip install -e ".[dev]"` ou `$env:PYTHONPATH="src"`. |
| `FileNotFoundError: configs/...` | O comando rodou fora da raiz. Volte para a pasta `EVCS-BESS-V2G`. |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |
| Aviso `CRLF will be replaced by LF` | Normal no Windows; pode ignorar. |
| Conflito no `git merge` | Arquivos seus: resolva e rode os testes. Arquivos compartilhados: não escolha um lado sozinho, fale com o grupo. Para desistir: `git merge --abort`. |
| A IA não segue as regras | Confira se a pasta aberta no VS Code é a raiz do repositório (onde está o `AGENTS.md`). No Copilot, use o modo *Agent* e confirme `chat.useAgentsMdFile`. |
| Integração muito lenta | É normal (~15 min). Feche outros programas pesados; `--workers N` controla os processos nos estudos do EVCS. |

Dúvidas sobre a rede, o EVCS ou os contratos: falem comigo (Ayrton).
