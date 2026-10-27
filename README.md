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
