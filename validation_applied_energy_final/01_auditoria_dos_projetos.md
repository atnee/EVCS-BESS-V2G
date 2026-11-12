# Auditoria dos projetos e procedência do artigo

## Decisão

O **Projeto A, EVCS-BESS-V2G**, é a referência documental e histórica do artigo. O **Projeto B, EVCS-BESS-V2G-Distributed-Stochastic**, contém a evolução posterior de localização/despacho e este pacote isolado de auditoria. Recomenda-se continuar o artigo a partir do A, em branch própria futura, adotando seu backend OpenDSS já existente para a reprodução elétrica. O B deve permanecer como experimento distinto de recursos distribuídos; não substituir suas saídas pelas do artigo.

Essa escolha é sustentada pelos dados, não pelos nomes das pastas. O A possui a campanha completa e Git na branch `feature/sensibilidade`, HEAD `58c0e45` (snapshot curado do artigo). O B não tem `.git`. Não houve criação de branch, commit, push, alteração de áreas compartilhadas, movimento ou exclusão de arquivos. O A já apresentava mudanças locais antes da auditoria; consultar [git_original.json](git_original.json).

## Comparação solicitada

| Componente | Projeto original | Projeto stochastic | Diferença | Usado no artigo? | Recomendação |
|---|---|---|---|---|---|
| Rede normalizada | `src/network/data/ieee8500.json` | mesmo caminho e SHA-256 | nenhuma nos bytes atuais | sim, redução MT | preservar como modelo histórico |
| Construção pandapower | `src/network/feeder_loader.py` | cópia idêntica | nenhuma | sim | não atribuir equivalência ao IEEE completo |
| Solver pandapower | `src/network/pandapower_solver.py` | cópia idêntica | nenhuma | sim, S0–S4 histórico | referência de reprodução histórica |
| Fontes DSS | `data/ieee8500/source/` | cópias com mesma procedência | 15 arquivos conferidos contra manifesto LF em cada projeto | fonte do JSON e dos replays | usar conjunto fixado, registrar hashes |
| Gerador de dados | `scripts/build_ieee8500_data.py` | cópia idêntica | nenhuma | sim | não regenerar silenciosamente |
| Configurações oficiais | BESS única 2.200 kW/7.200 kWh em `l3029502`; 150 V2G em `m1069504` | cópias das mesmas configurações | arquivos oficiais não descrevem sozinhos os experimentos distribuídos | sim | usar manifesto específico de cada execução |
| Campanha de sensibilidade | `results/ieee8500/sensitivity/` e integração de referência | não contém a campanha bruta completa; possui cópia curada em `paper/` | localização/profundidade dos resultados | sim, 63 configurações/315 registros | A é fonte da campanha |
| Snapshot editorial | `paper/` | arquivos coincidentes no escopo inventariado | cópia, não campanha nova | sim | não editar à mão |
| Backend físico | `opendss_rebuild/rebuild/engine.py` | cópia idêntica | ambos possuem OpenDSS e pandapower | posterior ao snapshot histórico | reutilizar, com validação explícita |
| Replay físico | `results/replay_s0_s4_opendss/` | consumido por caminhos externos nos experimentos | rede completa, controles nativos, BESS com surrogate histórico | não equivale aos resultados históricos do artigo | tratar como piloto separado |
| V2.0–V3.0.1 | não contém `distributed_stochastic/` | algoritmos, sondagens e resultados V2/V3 | evolução de localização, despacho e controles via OpenDSS | não é a campanha de 63 casos | não misturar com a campanha pandapower |
| Três BESS | não são a configuração oficial do artigo | V3.0.1: três sites em `best/bess_siting_sizing.csv` | mesma soma nominal, outra distribuição espacial | não demonstrado como parte do manuscrito | apresentar como outro experimento |
| Validação pareada | scripts V5/V6/V7 e resultados no A | scripts coincidentes; driver adicional `validar_fases_s0_ae.py` | driver no B, entradas/saídas históricas no A | diagnóstico posterior | uso cruzado confirmado e rastreado, não evidência de corrupção |

## Hashes, cópias e limites do histórico

[02_inventario_modelos.csv](02_inventario_modelos.csv) registra caminho absoluto, tamanho e SHA-256 de 7.732 arquivos. [comparacao_arquivos.csv](comparacao_arquivos.csv) encontrou 178 caminhos comuns com bytes idênticos, 6.225 somente no A e 1.151 somente no B. O escopo é explícito em [audit.py](audit.py): fontes, configurações, scripts, dados DSS, Markdown, CSV e JSON; não é comparação de todos os binários, imagens, caches ou objetos Git. Nenhum arquivo comum desse escopo diferiu.

Igualdade atual de hashes demonstra identidade, mas não a direção nem a data de uma cópia. Sem Git no B, não se pode reconstruir uma cronologia completa de modificações ou substituições. Os resultados V2/V3 têm manifestos e nomes de versão, mas isso não equivale a histórico versionado de todos os bytes executados.

A validação anterior contém explicitamente caminhos do driver no B e dos dados no A. Os sete hashes registrados nessa execução ainda coincidem: [validacao_hashes_historicos.csv](validacao_hashes_historicos.csv). Logo, houve combinação de **pastas**, mas não foi demonstrada combinação indevida de **versões diferentes do modelo** nessa execução. Persistem riscos de imports Python previamente carregados: inserir um caminho em `sys.path` não descarrega módulos de `sys.modules`. Os testes novos usam processo dedicado e registram os módulos locais utilizados.

Há evidência concreta de alteração do diretório corrente pelo OpenDSS: `audit_v301.json` registra `cwd_changed_by_AC=true`, e existem resultados V3.0 sob `data/ieee8500/source/results/`. Não são outro alimentador; são saídas em localização inadequada. Foram inventariadas e preservadas.

## BESS única versus três unidades

A configuração oficial mantém uma BESS em `l3029502`. A V3.0.1 usa:

| Barra | Potência kW | Energia kWh |
|---|---:|---:|
| l2692633 | 703,016613 | 1.070,117635 |
| l2711226 | 1.148,247906 | 4.983,803013 |
| l3029502 | 348,735481 | 1.146,079352 |
| Soma | 2.200 | 7.200 |

Fonte: `results/v301_continuacao_ac_segura/best/bess_siting_sizing.csv`. Igualdade das somas não implica equivalência elétrica: cada injeção percorre outros trechos e interage com outros controles. A frota V2G de 150 veículos e os recursos distribuídos precisam ser rastreados por execução, não inferidos do YAML oficial herdado.

## Fontes IEEE instaladas

O conjunto instalado em Program Files não é byte a byte idêntico ao conjunto fixado no projeto: 11 de 15 entradas coincidem após normalização LF; há diferenças em Master, Lines e Buscoords, e License.txt está ausente. [fontes_ieee_sha256.csv](fontes_ieee_sha256.csv) registra isso. A comparação elétrica nova das duas fontes no mesmo motor é numericamente muito próxima, mas não substitui validação contra soluções publicadas ou outro motor.

Na data da auditoria inicial, não havia sido localizado/fornecido um arquivo inequívoco da versão do manuscrito. O autor adicionou depois `EVCS_BESS_V2G_Applied_Energy_Elsevier_Template_v6.docx` ao diretório pai dos dois projetos. Seu resumo, método, resultados, discussão e conclusões foram lidos para preparar [o pacote de figuras e tabelas adicionais](paper_insertions/README.md). A campanha histórica no Word pertence à aproximação pandapower; a comparação física posterior foi preparada como seção/resultado distinto, sem editar o Word original.
