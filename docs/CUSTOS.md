# Custos da FuturaNext

Preços públicos consultados em **14/11/2026**, em dólares americanos por mês. Os cenários usam o teto mensal dos Droplets Basic Regular, nas regiões onde essa configuração está disponível. Impostos, câmbio, domínio, assentos e excedentes são adicionais. Confirme a cotação e a região no painel antes da contratação.

## Opção B · piloto sem clientes críticos

| Infraestrutura | USD/mês | Limite ou risco |
| --- | ---: | --- |
| Cloudflare Pages Free | 0,00 | Hospedagem estática, 500 builds/mês e um build concorrente. |
| Droplet Basic Regular, 1 GiB RAM e 1 vCPU | 6,00 | API e proxy no mesmo servidor. Testar uso de memória antes de adicionar worker. |
| Backup semanal opcional do Droplet | 1,20 | 20% × US$ 6,00. A imagem cobre o disco do servidor. |
| Supabase Free | 0,00 | 500 MB de banco, 1 GB de arquivos e 5 GB de saída mais 5 GB de saída em cache. Pausa após uma semana de inatividade, com limite de dois projetos ativos. |
| **Subtotal fixo com backup** | **7,20** | US$ 6,00 com Droplet avulso. Antes de repositório pago, destino de backup independente, e-mail e observabilidade adicional. |

O piloto aceita interrupções. Use dados sintéticos nos testes e avalie os requisitos de privacidade, retenção e disponibilidade antes de usar dados de clientes. Exporte o banco regularmente para um destino independente. Backups automáticos do banco ficam fora do Free. Uma pausa do projeto afeta autenticação e acesso aos dados mesmo com o Droplet ativo. [Cloudflare Pages](https://developers.cloudflare.com/pages/platform/limits/), [Droplet Basic](https://www.digitalocean.com/pricing/droplets), [DigitalOcean Backups](https://docs.digitalocean.com/products/backups/details/pricing/), [Supabase Free](https://supabase.com/pricing), [Supabase Backups](https://supabase.com/docs/guides/platform/backups).

## Opção A · produção inicial

| Infraestrutura | 2 GiB · USD/mês | 4 GiB · USD/mês | Premissa |
| --- | ---: | ---: | --- |
| Cloudflare Pages Free | 0,00 | 0,00 | Site estático, com requisições de assets sem cobrança. Recursos da zona e funções têm limites próprios. |
| Droplet Basic Regular | 12,00 | 24,00 | 1 vCPU/50 GiB SSD ou 2 vCPUs/80 GiB SSD, respectivamente. |
| Backup diário do Droplet | 3,60 | 7,20 | 30% do Droplet. |
| Supabase Pro, um projeto Micro | 25,00 | 25,00 | Crédito de compute cobre um Micro no cenário base. |
| **Subtotal fixo de PROD** | **40,60** | **56,20** | Frontend, API, banco, Auth e backup do host. |
| Exemplo de destino independente para exports | 5,00 | 5,00 | Spaces Standard, 250 GiB incluídos. Criptografe as cópias e avalie a região e a separação de contas. |
| Observabilidade inicial | 0,00 | 0,00 | DigitalOcean Monitoring e uma checagem externa coberta pelo crédito de Uptime. Checagens adicionais custam US$ 1,00 por mês cada. |
| **Total fixo ilustrativo de PROD** | **45,60** | **61,20** | Antes de SMTP, domínio, assentos, excedentes e impostos. |

Supabase Pro inclui 8 GB de disco de banco por projeto, 100 GB de arquivos, 250 GB de saída mais 250 GB de saída em cache, backup diário do banco com sete dias de retenção e sete dias de logs. O backup do banco inclui metadados de Storage. Os objetos precisam de cópia própria. O Pro mantém o projeto ativo. Com um único Droplet, uma falha no host ou no proxy pode parar toda a API. [Cloudflare Pages](https://developers.cloudflare.com/pages/functions/pricing/), [Droplets](https://www.digitalocean.com/pricing/droplets), [DigitalOcean Backups](https://docs.digitalocean.com/products/backups/details/pricing/), [Supabase Pro](https://supabase.com/pricing), [Supabase Backups](https://supabase.com/docs/guides/platform/backups), [DigitalOcean Spaces](https://docs.digitalocean.com/products/spaces/details/pricing/), [Monitoring](https://docs.digitalocean.com/products/monitoring/), [Uptime](https://docs.digitalocean.com/products/uptime/details/pricing/).

| Homologação isolada | Acréscimo mensal | Exemplo com PROD 2 GiB¹ | Exemplo com PROD 4 GiB¹ |
| --- | ---: | ---: | ---: |
| Projeto HML adicional na organização Supabase Pro, Droplet 1 GiB e backup semanal | 17,20 | 62,80 | 78,40 |
| Organização Supabase Pro exclusiva para HML, Droplet 1 GiB e backup semanal | 32,20 | 77,80 | 93,40 |

¹ Totais com o destino independente de US$ 5,00 de PROD. HML usa seu próprio projeto Pages, Droplet, banco, Auth, buckets e credenciais. O primeiro cenário estima US$ 10,00 para um Micro adicional na mesma organização, onde os administradores e o orçamento podem estar compartilhados. Uma organização separada restringe esse compartilhamento. Um Supabase Free em HML exige aceitar pausas e menor cobertura de backups. [Cobrança de compute do Supabase](https://supabase.com/docs/guides/platform/manage-your-usage/compute), [Supabase Free e Pro](https://supabase.com/pricing).

## Crescimento e disponibilidade

| Componente | Exemplo em USD/mês | Condição |
| --- | ---: | --- |
| Dois Droplets 2 GiB para API e um 4 GiB para worker | 48,00 | Distribuir as instâncias da API de acordo com os domínios de falha. Replicar o worker se o prazo de processamento exigir. |
| Backup diário dos três Droplets | 14,40 | 30% × US$ 48,00. |
| Load Balancer regional HTTP | 12,00 | Preço inicial por nó. Para redundância entre regiões, orçar balanceamento global e tráfego. |
| Supabase Pro com compute Small | ≈30,00 | US$ 25 de plano + US$ 15 de compute − US$ 10 de crédito. Medir conexões, I/O e consultas antes do aumento. |
| Fila durável no PostgreSQL | 0,00 incremental | Consome CPU, I/O e conexões do banco. Orçar Valkey/Redis e operação em separado se as métricas justificarem a mudança. |
| Backup independente e CDN | 5,00 | Exemplo com Spaces para backups e Pages Free para assets públicos. |
| **Subtotal ilustrativo de crescimento** | **≈109,40** | API redundante em uma região. Banco primário e worker únicos no exemplo. |
| Redundância para gravações no banco | Cotação do fornecedor | Exigir failover com SLA, compatibilidade de Auth/Storage e teste de recuperação. Réplicas de leitura do Supabase atendem consultas e ficam sujeitas a atraso de replicação. |
| Recuperação ponto a ponto | ≈100,00 adicionais | Supabase PITR para sete dias, por projeto, mais a diferença de compute aplicável. |
| Auditoria e retenção | Variável | O cenário usa logs locais rotacionados e registros de auditoria no banco. Log Drain no Supabase Pro custa US$ 60 por destino/projeto, além do destino de ingestão. |

Valide failover dos componentes, restauração de objetos e acesso ao Auth antes de acordar um SLA. A oferta Multigres do Supabase ainda está em alfa privada, sem cobertura do SLA. Como referência para cotar uma futura migração de banco, a DigitalOcean lista PostgreSQL Advanced a partir de US$ 130 por nó. Primário e standby nesse preço somam US$ 260 antes de disco adicional e integração de identidade. Confirme a região e o perfil no painel. Uma mudança de banco exige migrar e validar a estratégia de Auth, Storage e políticas. [Droplets](https://www.digitalocean.com/pricing/droplets), [Load Balancers](https://docs.digitalocean.com/products/networking/load-balancers/details/pricing/), [Supabase Compute](https://supabase.com/docs/guides/platform/compute-and-disk), [Supabase Read Replicas](https://supabase.com/docs/guides/platform/read-replicas), [Supabase PITR](https://supabase.com/docs/guides/platform/manage-your-usage/point-in-time-recovery), [Supabase Log Drains](https://supabase.com/pricing), [Supabase Multigres](https://supabase.com/docs/guides/database/multigres), [DigitalOcean PostgreSQL](https://docs.digitalocean.com/products/databases/postgresql/details/pricing/), [Mudança de planos DigitalOcean](https://docs.digitalocean.com/release-notes/upcoming/dbaas-plan-changes/).

DigitalOcean App Platform tem API e worker gerenciados tarifados separadamente, a partir de US$ 5,00 por componente com 0,5 GiB, sujeito a região e dimensionamento. Seu preço inicial serve como referência de comparação com o esforço operacional do Droplet. Cloud Run em São Paulo é uma alternativa para requisitos de residência ou latência. Calcule instâncias mínimas, vCPU e memória, worker ou jobs, saída de dados e logs com carga medida. Render Free suspende o serviço web após 15 minutos sem tráfego de entrada e leva em torno de um minuto para retomá-lo. O tempo de retomada é incompatível com respostas imediatas. [App Platform](https://docs.digitalocean.com/products/app-platform/details/pricing/), [Cloud Run regiões](https://docs.cloud.google.com/run/docs/setup), [Cloud Run preços](https://cloud.google.com/run/pricing), [Render Free](https://render.com/docs/free).

## Custos variáveis e despesas separadas

| Categoria | Base para orçamento |
| --- | --- |
| E-mail transacional | Exemplo AWS SES avulso a US$ 0,10 por mil envios e US$ 0,12/GB de anexos. Para 2.000 envios sem anexos, US$ 0,20 em envio. Use SMTP próprio para convites e recuperação. Um Send Email Hook pode substituir o SMTP se o envio for implementado via API. |
| WhatsApp | Estimar mensagens entregues por categoria, mercado e tarifa Meta. Somar mensalidade, taxa por número ou sobretaxa se houver parceiro. |
| NFS-e, certificados e terceiros | Cotar por prestador, município, volume e serviços contratados após escolher o conector. Acordar quem custeia certificado e renovação. |
| IA opcional | Calcular tokens de entrada e saída e chamadas de ferramenta por módulo e empresa. A execução começa na fase correspondente. |
| Excedentes de banco, armazenamento e tráfego | Medir separadamente disco, objetos, transferência cacheada e sem cache, tráfego do servidor e retenção de backups. |
| GitHub privado e registry | GitHub Team anuncia US$ 4 por usuário/mês. O plano do GitHub define as proteções e franquias de CI. Se usar DigitalOcean Container Registry, Starter oferece 500 MiB e Basic custa US$ 5/mês com 5 GiB. |
| DNS, WAF, domínio, tributos e suporte | Orçar renovação do domínio, eventual plano Cloudflare, tributos e câmbio, horas de plantão e suporte contratado. |

[Amazon SES](https://aws.amazon.com/ses/pricing/), [Supabase SMTP](https://supabase.com/docs/guides/auth/auth-smtp), [Meta WhatsApp Business](https://whatsappbusiness.com/products/platform-pricing/), [GitHub Team](https://github.com/team), [DigitalOcean Container Registry](https://docs.digitalocean.com/products/container-registry/details/pricing/), [Cloudflare WAF](https://developers.cloudflare.com/waf/).

## Medição e dimensionamento

| Medir | Ação |
| --- | --- |
| Latência p50/p95 da API desde o Brasil, frequência de 5xx e tempo no banco | Medir em HML com tráfego representativo. Revisar índices, round trips e pool de conexões quando houver degradação. |
| CPU do host, RSS por processo, memória disponível, swap, OOM e espaço em disco | Validar 2 GiB com Linux, proxy e API. Testar 4 GiB ou separar processos se a RAM se mantiver perto de 80%, houver OOM ou CPU sustentada comprometer a latência. Ajustar o limiar com a linha de base medida. |
| Tamanho do backlog, idade do job mais antigo, retries e falhas permanentes | Ao introduzir o worker, acompanhar as séries por tipo de job. Isolar jobs fiscais ou bots em host próprio se drenagem, memória ou tempo da API piorarem sob carga concorrente. |
| Custo por tenant e produto | Medir mensagens, emissões, minutos de worker, bytes e uso de IA conforme cada integração entrar. Aplicar tarifa por unidade aos contadores e reconciliar com faturas. Ratear custos compartilhados com regra contábil explícita. |

O dimensionamento, os custos unitários por tenant e o desempenho de filas precisam de medições com a plataforma executável e a carga homologada.
