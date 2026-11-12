# Relatório final da auditoria — validação elétrica ainda não encerrada

**Complemento posterior:** os experimentos de melhoria solicitados pelo usuário estão em [10_resultados_melhoria.md](10_resultados_melhoria.md). Eles identificam a configuração distribuída já existente como melhor candidata para continuar, ainda com violações absolutas. Este relatório mantém as conclusões da auditoria do caso histórico.

**Pendências fechadas posteriormente:** [S2/S3 e custos de conexão](11_s2_s3_e_custos_conexao.md) apresenta os replays físicos de S2/S3 e a conciliação dos custos assumidos. O valor monetário incremental das conexões permanece desconhecido, por falta de projeto e cotação, e não foi tratado como zero.

## Respostas objetivas

**Qual projeto principal?** O original, EVCS-BESS-V2G, como referência do artigo e da campanha. O Distributed-Stochastic é a evolução experimental posterior. A auditoria atual fica isolada no B e não mudou o workspace nem arquivos do A.

**Qual modelo estava em uso?** O artigo usa redução MT em pandapower, linhas em sequência, reguladores com tap conjunto e secundários agregados. V2.0–V3.0.1 usam o backend OpenDSS completo existente, com novos experimentos de localização, despacho e controle. Ambos os motores estão presentes nas duas árvores; nome da pasta não determina o motor.

**Qual a causa das divergências?** Modelos eletricamente distintos: fases/topologia, matrizes, transformadores, controles e cargas. A ablação de matrizes reduz parte do erro. A correção das tripletas CAP muda a tensão no máximo 0,000208 pu e não explica sozinha a divergência. A causa única ou a contribuição total dos caminhos de fases artificiais não foi demonstrada. O mínimo antigo de 0,060761 pu também sofria de seleção inadequada dos nós nos relatórios.

**O que foi corrigido/testado?** Seleção por barra-fase física em rotina isolada, rejeição de dados não finitos/duplicados, inventário SHA-256, conferência da campanha, balanço P/Q e comparação de fontes DSS. Quatro snapshots novos (3/18 h × fontes do projeto/instalação) e 120 snapshots do piloto com perfis históricos convergiram. No conjunto do piloto, resíduos ficaram abaixo de 0,218 kW/0,149 kvar. Os testes executados passaram: 70 testes e 35 subtestes. BESS S2/S4 e V2G S4 salvos passaram novamente pelos validadores de energia/SOC/limites/portas. A equivalência do pandapower ao IEEE completo continua reprovada pelos dados pareados históricos.

**Quais resultados são confiáveis?** Como resultados do modelo histórico, os 315 registros tiveram picos e contagens de linhas novas confirmados. O caso principal confirma 49→0 novas linhas e 10,8901% de redução de pico, mas S4 ainda chega a 117,7461% numa linha preexistente. O novo piloto físico, com exatamente os comandos históricos, apresenta no S4 automático 23 novas linhas, tensão mínima de 0,923569 pu e carregamento máximo de 120,797968%; com taps S0 travados, 28 novas linhas. A mitigação é parcial, não solução operacional. As três BESS somam o mesmo orçamento nominal da BESS única, mas não são eletricamente equivalentes. A V3.0.1 continua relativamente elegível e absolutamente não certificada.

**O que falta?** Fixar a configuração final e o manifesto físico, conferir referência elétrica externa, decidir como apresentar a mitigação parcial já demonstrada, completar a atribuição S2/S3 e reproduzir a campanha relevante. O replay de S0/S1/S4 com ambas as políticas de taps já foi feito. A lista finita está em [08_pendencias_para_applied_energy.md](08_pendencias_para_applied_energy.md).

**Podemos iniciar a versão final?** Podemos revisar métodos, rastreabilidade e limitações. Não devemos fechar as alegações quantitativas nem submeter o manuscrito como eletricamente validado neste estado.

## Evidências e entregas

- [Auditoria das pastas e tabela de componentes](01_auditoria_dos_projetos.md).
- [Inventário de modelos e hashes](02_inventario_modelos.csv).
- [Diagnóstico e limites da validação](03_diagnostico_eletrico.md).
- [Erros S0 recalculados](04_validacao_s0.csv), [fases/linhas](05_validacao_fases_e_linhas.csv), [controles](06_validacao_controles.csv).
- [Impacto sobre S0–S4 e figuras](07_impacto_nos_resultados_s0_s4.md).
- [Reprodução, testes e escopo](README.md).

Os 7.732 arquivos inventariados foram novamente conferidos por hash e permaneceram inalterados. Foi criado [snapshot_fontes.zip](snapshot_fontes.zip), com [SHA-256](snapshot_sha256.json), preservando uma cópia verificável das fontes; isso não é proteção física contra escrita. Não houve commit/push nem edição do snapshot `paper/`. A ausência de uma nova otimização é deliberada: o piloto já responde que as injeções históricas mitigam parcialmente a rede física, e os resultados do artigo precisam refletir essa conclusão.
