# Dados da rede real
`templates/honduras_template.xlsx` é um modelo vazio. Copie para `private/`, preencha e importe com `network.xlsx_loader.load_xlsx`.
Cabeçalhos na linha 1, sem alteração. IDs devem ser texto. Unidades estão nos nomes dos campos. Todos os campos de uma linha preenchida são obrigatórios, exceto x/y. Abas sem equipamentos podem ficar vazias. `buses` e uma única `substations` precisam conter dados.
`load_profiles`: hora ISO8601 com offset, consumo positivo em kW/kvar. Preencher todas as horas para cada par barra/fase. Não misturar carga estática e curva temporal como demandas adicionais.
Coordenadas x/y são opcionais: documentar o sistema de referência antes de usar mapas.
`lines`: impedâncias totais do trecho em ohms, por fase, sem acoplamento (esquema preliminar).
Transformadores/reguladores/capacitores/chaves/GD são preservados como registros, mas não resolvidos pelo backend sintético. O importador não valida ainda integralmente os parâmetros desses equipamentos.
Não incluir arquivos reais no repositório. `.gitignore` reduz o risco de inclusão acidental, mas não impede `git add -f` nem remove arquivos já rastreados. Revisar `git diff --cached` antes de cada commit. Resultados reais também são restritos.
