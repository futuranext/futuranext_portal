# Ferramentas fiscais

O portal separa **Emissão de NFS-e** (`nfse`) e **Consulta de NFS-e** (`nfse-consulta`). Cada ferramenta exige licença vigente e acesso à empresa selecionada. Administradores da empresa podem alterar a configuração. O cadastro, a substituição e a remoção de certificado exigem sessão com verificação em duas etapas.

- **Emissão.** Cadastro auxiliar de prestador, município, tributação, NBS, série da DPS, descrição e ambiente previsto. O painel oferece um modelo genérico e o modelo com o layout do robô CCP.
- **Consulta.** Cadastro de até 100 CNPJs por empresa, com nome de referência, ativação individual e ambiente previsto. O servidor valida os dígitos verificadores dos CNPJs numéricos e alfanuméricos. [Cálculo do DV do CNPJ](https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/publicacoes/documentos-tecnicos/cnpj/manual-dv-cnpj.pdf)

## Certificado A1

O painel aceita PKCS#12 (`.pfx` ou `.p12`) de até 128 KiB quando `FN_FISCAL_MASTER_KEY` contém uma chave de 32 bytes codificada em Base64 URL-safe. Mantenha uma chave exclusiva por ambiente, em armazenamento de segredos. O servidor verifica se a chave privada corresponde ao certificado e se o arquivo está no período de validade. O arquivo e a senha ficam cifrados com AES-GCM e vinculados à empresa e à finalidade. A conexão de rotina da API lê apenas as datas. O painel indica vencimento em até 30 dias e permite remoção ou substituição do certificado.

A validade no portal corresponde à vigência temporal do arquivo. O aceite da cadeia ICP-Brasil, do titular e dos poderes de acesso depende da validação com o serviço fiscal. Alterar a chave do ambiente exige reenviar os certificados para uso futuro. A exclusão remove o registro ativo. Backups seguem a política de retenção do ambiente.

## Modelos de emissão

O modelo genérico `modelo-nfse-emissao-v1.0.xlsx` contém uma aba **Emissão** com referência do serviço, tomador, competência, endereço, valor, descrição e NBS. O modelo é um contrato de coleta para o fluxo modular.

O segundo download gera `Base_Emissao_Nota_Fiscal.xlsx` com o layout do importador do robô CCP, versões registradas na aba **Instruções** e dados preenchidos pelo cliente na aba **Base**. O importador lê os cabeçalhos da primeira linha pelo nome, e processa registros a partir da linha 2.

| Coluna | Cabeçalho | Preenchimento |
| --- | --- | --- |
| A | Aluno | Referência do cadastro local, como texto. |
| B | Nome Aluno | Nome do aluno. |
| C | Nome Responsável | Nome do responsável. |
| D | Data Vencimento | Data do Excel ou texto `dd/mm/aaaa`. |
| E | CPF Responsável | CPF de 11 dígitos, como texto para preservar zeros iniciais. |
| F | Numero Endereco | Número do endereço, como texto. |
| G | CEP | CEP, como texto para preservar zeros iniciais. |
| H | Vlr Recebido | Valor positivo. |
| I | NF | Preenchido pelo fluxo legado. Deixe vazio ao preparar as linhas. |
| J | Status | O fluxo lê linhas com status vazio. |
| K | NBS | Código NBS previsto para o serviço. |

Linhas sem **Aluno** e sem **CPF Responsável** são ignoradas pelo leitor. O contrato legado de tomador usa CPF. Para preparar a conferência em um ambiente de homologação, aponte a variável legada `PLANILHA` para uma cópia do arquivo preenchido e use `PLANILHA_ABA=Base`. O caminho padrão é `resource/Base_Emissao_Nota_Fiscal.xlsx`. Mantenha os arquivos operacionais existentes separados dos modelos vazios.

O endpoint protegido `GET /api/v1/tenants/{tenant_id}/tools/nfse/fiscal/template` entrega o modelo genérico. Use `?variant=ccp` para o segundo formato. Os dois XLSX são gerados com cabeçalhos e instruções. A verificação de compatibilidade comparou o esquema com as regras de leitura no ZIP e reabriu o XLSX sintético via OpenPyXL. A execução do robô faz parte da futura homologação.

## Integração fiscal

Para a emissão, validar a montagem e a assinatura da DPS, o leiaute vigente, a elegibilidade do prestador e do município, e os retornos com produção restrita. A publicação do emissor depende de autorização, confirmação antes de transmitir e controles de idempotência e reconciliação.

Para a consulta, validar a representação dos CNPJs no Ambiente de Dados Nacional. A distribuição futura deve guardar o checkpoint NSU por empresa e CNPJ e respeitar a autorização do certificado ou da procuração. [Documentação técnica do Sistema Nacional NFS-e](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual) · [Guia das APIs do ADN](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual/manual-contribuintes-apis-adn-sistema-nacional-nfse.pdf)
