# 📊 B3 Quantitative Screening & Valuation Pipeline

Pipeline automatizado em Python para triagem fundamentalista, cálculo de valuation (Preço Teto / Margem de Segurança pelo método de Barsi/Gordon), precificação via Graham/Lynch, análise de Fatores via **Fórmula Mágica de Joel Greenblatt** e varredura qualitativa de notícias por processamento baseado em palavras-chave.

Desenvolvido para transformar dados brutos de mercado em **insights acionáveis para tomada de decisão em investimentos**, mitigando vieses comportamentais e automatizando o trabalho repetitivo de coleta e cruzamento de balanços.

---

## 🚀 Funcionalidades de Negócio

* **Triagem Fundamentalista Automatizada:** Filtra empresas com base em critérios estritos de qualidade (ROE mínimo, alavancagem financeira saudável e consistência de lucros).
* **Tratamento Inteligente de Setores Cíclicos:** Utiliza médias móveis e medianas plurianuais de valores brutos para evitar distorções em commodities (petróleo, mineração, celulose) causadas pelo topo/fundo de ciclos econômicos.
* **Valuation Baseado no Método Barsi / Gordon:** Projeta o Preço Teto com base na média dos dividendos realmente pagos em anos completos ou em estimativas de payout conservadoras para o setor financeiro.
* **Fórmula Mágica de Greenblatt:** Classifica o universo de ações elegíveis combinando o *Earnings Yield* (lucro operacional sobre o valor da empresa) e o *ROIC* (retorno sobre o capital investido).
* **Análise Qualitativa de Manchetes:** Realiza uma varredura nas últimas notícias de cada ativo aprovado mapeando termos-chave de risco e expansão.
* **Auditoria de Dados & Histórico Dinâmico (`Snapshots`):** Constrói uma série temporal própria a cada execução para monitoramento da evolução das margems de segurança ao longo do tempo.

---

## 🛠️ Estrutura do Repositório

```text
├── b3_indices.py                      # Core do pipeline: Coleta, Valuation, Indicadores e Filtros
├── analise_qualitativa.py             # Módulo de varredura e classificação de notícias recentes
├── empresas_perenes_holdings...v2.csv # Base de entrada com os ativos monitorados (input)
├── ranking_valuation_b3.csv           # Output principal com o ranking consolidado
├── top20_recomendadas.csv             # Output executivo com as melhores oportunidades
├── magic_formula_greenblatt.csv       # Output com o ranking cruzado da Fórmula Mágica
├── historico_execucoes.csv            # Base temporal de snapshots gerada incrementalmente
└── docs/
    └── DOC.md                         # Documentação técnica e de negócio detalhada

⚙️ Pré-requisitos e InstalaçãoCertifique-se de ter o Python 3.8+ instalado em seu ambiente (compatível com Linux e Windows).Clone o repositório:Bashgit clone [https://github.com/seu-usuario/b3-quant-screening.git](https://github.com/seu-usuario/b3-quant-screening.git)
cd b3-quant-screening
Instale as dependências necessárias:Bashpip install pandas yfinance
🏃‍♂️ Como ExecutarO fluxo de execução foi desenhado para rodar de forma sequencial:Executar a Triagem Fundamentalista e Valuations:Bashpython b3_indices.py
Este script lê a lista base, coleta dados do Yahoo Finance via API, aplica os filtros de qualidade, calcula os Preços Teto e gera os arquivos consolidados na raiz.Executar a Análise Qualitativa de Notícias:Bashpython analise_qualitativa.py
Este script consome o arquivo top20_recomendadas.csv gerado na etapa anterior para mapear o sentimento recente das manchetes.📖 Documentação de Negócio e Arquitetura (DOC)Nota: Esta seção detalha a arquitetura do projeto. Caso prefira isolar em arquivos separados, mantenha o conteúdo abaixo em docs/DOC.md.1. Visão Geral da SoluçãoO objetivo deste projeto é automatizar o processo de Due Diligence quantitativa de ativos listados na B3. Investidores focados em valor e dividendos enfrentam grande fricção operacional para cruzar dados de balanços trimestrais/anuais, calcular métricas de Graham/Lynch, estimar tetos de preço e monitorar notícias corporativas.Este pipeline centraliza a inteligência de análise em scripts coesos, garantindo rapidez, rastreabilidade e critérios padronizados de decisão.2. Arquitetura e Módulos do SistemaA. Core Quantitativo (b3_indices.py)Responsável por toda a engenharia de dados financeiros:Ingestão (yfinance): Coleta cotações em tempo real, DREs consolidadas, balanços patrimoniais e histórico de proventos.Tratamento de Inconsistências: Realiza validação cruzada entre o P/L reportado por APIs de mercado e o P/L real calculado diretamente via balanço patrimonial e Lucro por Ação (EPS), evitando distorções cambiais comuns em ativos brasileiros.Normalização Cíclica: Para empresas de commodities (Petróleo, Mineração, Siderurgia e Celulose), o motor desconsidera o resultado isolado do último ano e calcula medianas plurianuais (valores brutos) para ROE, Endividamento e Lucratividade, isolando o ruído dos ciclos de mercado.B. Módulo Qualitativo (analise_qualitativa.py)Atua como um filtro complementar de News Mining:Varre os ativos aprovados no ranking quantitativo em busca de fatos relevantes recentes.Aplica dicionários de termos de alerta (ex: investigação, prejuízo, dívida elevada, processo) e de expansão (ex: recorde, acordo, dividendo extra).Atribui uma classificação preliminar para direcionar a leitura humana prioritária antes da alocação de recursos.3. Regras de Negócio e Critérios de ElegibilidadeO motor de decisão classifica os ativos em faixas de conformidade para proteger o capital contra armadilhas de valor (value traps):Filtro de Qualidade Operacional (ROE): Exige rentabilidade mínima sobre o patrimônio líquido (padrão de referência $\ge 12\%$ a.a.).Filtro de Solidez Financeira (Alavancagem): Limita a relação Dívida Líquida / EBITDA a patamares seguros ($\le 3.0x$), excetuando setores regulados por natureza de capital intensivo financeiro (Bancos e Seguradoras).Valuation Cruzado (Graham & Lynch): Avalia o teto de preço utilizando o número de Graham ($P/L \times P/VP$) e o índice PEG (Price/Earnings to Growth), garantindo flexibilidade analítica.Preço Teto (Método Barsi / Gordon):Prioriza a média dos dividendos efetivamente pagos nos últimos anos completos (evitando distorções de proventos extraordinários pontuais).Utiliza taxas de desconto-teto calibradas de acordo com o perfil de risco do ativo ($6\%$ para empresas perenes e $8\%$ para ativos com ressalvas ou maior exposição estrutural).4. Dicionário de Indicadores dos Relatórios Geradospontuacao_final: Nota ponderada de $0$ a $100$ que sintetiza a atratividade do ativo, composta por Margem de Segurança ($40\%$), ROE ($25\%$), Taxa de Crescimento ($20\%$) e Alavancagem ($15\%$).preco_teto: Preço máximo que pode ser pago por um ativo para assegurar o Dividend Yield alvo exigido pela regra de projeto.margem_de_seguranca_pct: Distância percentual entre a cotação atual de mercado e o Preço Teto calculado.soma_dos_rankings (Fórmula Mágica): Menor somatória das posições de Earnings Yield e ROIC, evidenciando empresas baratas e altamente rentáveis simultaneamente.📌 Avisos e Isenção de ResponsabilidadeImportante: Este software é uma ferramenta analítica de engenharia de dados e apoio estatístico para organização pessoal. Não constitui recomendação de compra, venda ou consultoria de investimentos. Os cálculos dependem da disponibilidade e integridade dos dados fornecidos por APIs públicas de mercado. Sempre valide as informações nos relatórios oficiais de Relações com Investidores (RI) e na CVM antes de alocar capital.
* **`soma_dos_rankings` (Fórmula Mágica)**: Menor somatória das posições de *Earnings Yield* e *ROIC*, evidenciando empresas baratas e altamente rentáveis simultaneamente.
