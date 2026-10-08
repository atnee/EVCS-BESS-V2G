# Validação IEEE123 / pandapower — 08/10/2026

Ambiente: Windows, Python 3.13.5, pandapower 3.4.0. Versões diretas em `requirements-tested.txt`.

- `python -m pip install -e ".[dev]"`: concluído.
- `python -m pytest -q`: **34 testes aprovados**, 78,65 s.
- `python -m integration.coordinator --output results/ieee123`: S0–S4, 24 intervalos cada, concluídos com resultados finitos. CSV/JSON/PNG exportados; gráfico inspecionado.
- Wheel construído com `pip wheel . --no-deps` e carregado fora da raiz do projeto: JSON IEEE123 e licença presentes; 130 barras computacionais e 118 linhas carregadas sem acesso à fonte externa.
- Os cinco notebooks foram executados integralmente com kernel Jupyter via `nbclient`. Cópias com saídas estão em `results/ieee123/notebooks/`; os originais permanecem sem saídas. Houve avisos do transporte/encerramento ZMQ no Windows, sem falha nas células.

Os novos testes verificam hashes dos dados-fonte, total nominal 3.490 kW / 1.920 kvar, fases, comprimento/impedâncias, chaves, cargas delta, PQ/I/Z e capacitores. Verificam ainda resposta ao sinal das injeções, ausência de estado residual entre cenários, rejeição de fases inválidas, não finitude mesmo quando o backend informa convergência e balanço ativo com tolerância absoluta de 0,001 kW em todos os cenários. Os testes anteriores de EVCS, BESS, V2G e XLSX continuam aprovados.

Snapshot nominal sem DER: P_fonte = 3.618,079 kW; V_min = 0,997795 pu e V_max = 1,043661 pu. Estes números descrevem o equivalente implementado; **não são uma comparação independente com a solução publicada pelo IEEE**.

Na curva diária assumida, taps fixos geram sobretensões em carga leve (máximo próximo de 1,0713 pu). Os indicadores térmicos usam a hipótese de 400 A nas linhas. Portanto, convergência e balanço não certificam fidelidade integral nem viabilidade operacional. Veja `data/ieee123/README.md` para todas as aproximações.

---

# Validação

## Histórico da demonstração sintética — 08/10/2026

Python 3.12.14, Linux. `python -m pytest -q`: **29 testes aprovados**.
Instalação editável do pacote aprovada com `python -m pip install -e . --no-deps --no-build-isolation`, usando as dependências existentes. O comando completo de instalação com extras e ambiente virtual é fornecido no README, mas não foi repetido em ambiente limpo. CI foi preparado para 3.12/3.13 e não foi executado no GitHub.

Cobertura: conservação de energia, eficiências, SOC/potência, SOC terminal, disponibilidade V2G, mobilidade inviável, duplicação de veículos, reserva de portas/potência, importação preliminar, identificadores, fases, horizonte temporal, topologia, balanço de potência e perdas, caso analítico de uma linha, fluxo reverso, bloqueio do backend para redes reais e execução S0–S4.

As células Python dos cinco notebooks foram executadas sequencialmente na raiz, em namespaces independentes, sem erro. A inicialização de kernel Jupyter via nbclient falhou por restrições do ambiente a sockets TCP/IPC (Operation not permitted). Portanto, execução completa via kernel e interface Jupyter permanece a verificar em computador local. Os notebooks entregues estão sem outputs.

O modelo XLSX foi exportado e suas 11 abas renderizadas e inspecionadas. É intencionalmente vazio e sem fórmulas. O teste confirma que o importador recusa a rede vazia. Tabelas normalizadas válidas foram testadas em memória; importação de dados reais da concessionária não foi validada.

S0–S4 foram executados e CSV/JSON/PNG gerados em results/demo_reference. O gráfico foi inspecionado. BESS e V2G reduzem picos no exemplo, mas aumentam energia comprada devido às perdas. Estes resultados não validam IEEE123 nem estimam benefícios da rede real.

## Dependências usadas nesta execução
- numpy: 2.3.5
- pandas: 2.2.3
- PyYAML: 6.0.3
- openpyxl: 3.1.5
- matplotlib: 3.10.8
- pytest: 9.1.1
- nbclient: 0.11.0
- nbformat: 5.11.1
- ipykernel: 7.4.0
- setuptools: 84.0.0
