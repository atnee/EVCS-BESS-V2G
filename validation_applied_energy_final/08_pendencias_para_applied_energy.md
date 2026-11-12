# Pendências para concluir o artigo

**Atualização após esta auditoria inicial:** S2/S3 foram reproduzidos na rede completa para os perfis históricos e para a configuração distribuída. Consulte [11_s2_s3_e_custos_conexao.md](11_s2_s3_e_custos_conexao.md). O custo incremental de conexão foi auditado quanto ao escopo e continua sem valor monetário verificável; projeto e cotação são as pendências correspondentes.

## Decisão científica

**Ainda não há evidência suficiente para submissão com as alegações elétricas atuais.** Pode começar a revisão final de métodos, procedência e limitações. Resultados, resumo e conclusões devem permanecer provisórios até fechar o caso físico. Isso é uma decisão técnica sobre a evidência disponível, não previsão de decisão da revista.

## Sequência finita de fechamento

1. Fixar o objeto do artigo: configuração histórica de uma BESS ou nova alocação de três unidades. A recomendação para concluir o artigo existente é preservar o objeto histórico e reproduzi-lo com OpenDSS; a alocação distribuída não deve substituí-lo silenciosamente.
2. Fixar um manifesto do modelo físico: arquivos DSS, motor, ampacidades assumidas, fonte, ZIP e limites de transição, reguladores, capacitores, fases energizadas e passo. Resolver conscientemente se se mantém a fonte IEEE nativa ou o ajuste do estudo; não escolher por obter aprovação.
3. Validar contra uma referência externa de tensões/correntes/taps IEEE ou outra implementação OpenDSS independente. A comparação entre dois datasets no mesmo C-API é forte verificação local, mas não essa validação externa.
4. **Replay dos mesmos perfis históricos executado nesta auditoria:** S0/S1/S4 automático e S1/S4 com taps horários do S0, na rede completa nativa. Resultado: S4 ainda tem 23/28 linhas novas nas duas visões. Não é necessário repetir esse piloto sem mudar a pergunta científica. Resta decidir se o artigo relata essa mitigação parcial ou justifica uma alteração física/operacional; não mudar a fonte ou os limites apenas para aprovar o caso.
5. Se houver inviabilidade, reportá-la e decidir se o artigo demonstra mitigação parcial. Reproduzir S2/S3 para atribuir os efeitos. Qualquer novo despacho deve ser estratégia compatível com os contratos e ter hipótese explícita; não ajustar limites para aprovar resultados.
6. Somente depois repetir o subconjunto necessário da campanha de sementes/sensibilidades. Se as conclusões dependerem das 63 configurações, será necessário reproduzir todas no modelo eleito, retendo séries completas. Não tratar 315 linhas de resumo como 315 validações independentes.
7. Atualizar figuras/tabelas e texto a partir desse conjunto único, conservar o histórico com rótulo de aproximação e explicar diferenças de topologia, fronteira, controles, resolução e custos.

## Critérios objetivos para encerrar

- Manifesto de entradas verificável; sem imports de outra raiz ou saída em diretório alterado por Compile.
- Fases/linhas energizadas cobertas, ausências justificadas; convergência e balanço explícitos.
- Cenários comparáveis com perfis, topologia e condições controladas; visões automática e taps S0 travados.
- SOC inicial/final e energia de mobilidade conferidos para o despacho efetivamente escolhido.
- Violações absolutas separadas de agravamentos relativos e de linhas novas.
- Tabelas do manuscrito geradas de arquivos rastreáveis; limitações e hipóteses declaradas.

## Limitações obrigatórias no texto

Rede teste, não alimentador de Honduras; ampacidades de catálogo assumidas; modelo e política de controle específicos; resolução temporal e média das potências; frota/mobilidade e distribuição espacial hipotéticas; transformadores próprios de DER não representados no modelo histórico; custos e tarifas condicionais; um conjunto de condições diárias não prova confiabilidade anual. ANSI C84.1 e PRODIST são referências comparativas, não normas hondurenhas.

## Escopo não executado nesta auditoria

Não se refez a campanha longa, a triagem EVCS nem a otimização. Não se alteraram módulos compartilhados. Não se declarou concluída uma correção global de fases no pandapower. Não se validaram todos os pontos publicados IEEE nem a trajetória completa dos controles contra uma referência externa. As execuções novas são quatro snapshots de comparação de fontes e 120 snapshots do piloto de perfis históricos; as demais verificações são recálculos de evidências existentes. S2/S3 ainda precisam da mesma reprodução controlada para a atribuição final dos efeitos.
