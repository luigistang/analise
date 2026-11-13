# Base matemática do Screener de Valuation (B3 e EUA)

Documento para colar em outro chat. Descreve só as regras e as contas, sem código. Tudo abaixo vem dos scripts `b3_indices.py` e `us_indices.py`.

## 1. Entradas e fonte dos dados

Fonte única: Yahoo Finance (`yfinance`). Sufixo `.SA` na B3. Nos EUA, ponto vira hífen (`BRK.B` → `BRK-B`).
Entrada B3: CSV `Nome, Ticker, Setor, Subsetor` (uma célula pode ter vários tickers separados por vírgula).
Entrada EUA: CSV `Nome, Ticker, Setor, Tipo` (Tipo = `Ação` ou `ETF`).

Campos do `.info` usados: trailingPE, forwardPE, priceToBook, returnOnEquity, dividendYield, totalDebt, totalCash, ebitda, enterpriseValue, bookValue, sharesOutstanding, payoutRatio, freeCashflow, marketCap, dividendRate, currentPrice (ou regularMarketPrice, ou previousClose), e para ETF: annualReportExpenseRatio (ou netExpenseRatio, expenseRatio) e totalAssets.
De `.financials` (anual): Total Revenue, Net Income, EBIT (ou Operating Income), Diluted EPS (ou Basic EPS). De `.cashflow`: Repurchase Of Capital Stock (ou Common Stock Repurchased, Repurchase Of Common Stock). De `.dividends`: histórico de pagamentos.

## 2. Fórmulas básicas

- **CAGR** de uma série anual: `(valor mais recente ÷ valor mais antigo)^(1 ÷ nº de anos entre eles) − 1`. Se algum dos dois for ≤ 0, vale "sem dado". Normalmente são 4 anos de balanço (3 intervalos).
- **Dividendo médio (Barsi):** média da soma anual dos dividendos pagos nos **3 últimos anos calendário completos**. O ano corrente nunca entra.
- **Dívida líquida / EBITDA** = (dívida total − caixa total) ÷ EBITDA. Vazio se EBITDA for 0 ou faltar dado.
- **Número de Graham** = P/L × P/VP. Só vale com P/L > 0 e P/VP > 0.
- **PEG** = P/L ÷ (CAGR do lucro × 100). Só vale com P/L > 0 e CAGR do lucro > 0.
- **Margem de segurança (%)** = (preço-teto − cotação) ÷ cotação × 100.
- **Earnings Yield** = EBIT ÷ valor da empresa (EV), só se EV > 0.
- **ROIC** = EBIT ÷ (dívida + patrimônio − caixa), com patrimônio = valor contábil por ação × ações em circulação. Só vale se o capital investido for > 0.
- **FCF Yield** = fluxo de caixa livre ÷ valor de mercado.
- **Buyback Yield** = recompra média anual ÷ valor de mercado. Recompra média = média do valor gasto nos últimos 3 anos disponíveis no fluxo de caixa, em valor absoluto.
- **Shareholder Yield** = dividend yield + buyback yield.
- **Payout:** o do Yahoo. Se faltar e houver EPS > 0: (dividend yield × cotação) ÷ EPS.
- **P/L verificado pelo balanço** = cotação ÷ EPS do balanço (se EPS > 0). Se diferir do P/L do Yahoo em mais de 50% (`|verificado − yahoo| ÷ yahoo > 0,5`), a linha é marcada como `pl_inconsistente` e o P/L usado passa a ser o verificado.

## 3. Preço-teto (Barsi / Gordon)

`Preço-teto = dividendo ÷ DY mínimo exigido`

**B3.** Dividendo, em ordem de preferência:
1. dividendo médio dos 3 anos completos, se existir e for > 0;
2. se o setor for Financeiro ou Holding e não houver histórico: EPS estimado × 40%, com EPS = cotação ÷ P/L usado;
3. dividendRate do Yahoo.

DY mínimo exigido na B3: **6%** para "Aprovada (Pura)"; **8%** para "Aprovada com Ressalva", "Quase Aprovada" e "Revisar manualmente".

**EUA.** Dividendo por ação (média 3 anos) **+** recompra média por ação (recompra média ÷ ações em circulação). DY mínimo exigido: **4%** para todos os vereditos. Sem dividendo nem recompra, não há preço-teto.

Descartada nunca recebe preço-teto.
**Status:** margem ≥ mínimo → `ELEGÍVEL PARA COMPRA`; abaixo → `AGUARDAR (margem insuficiente)`. Para Quase Aprovada e Revisar manualmente: `REVISAR MANUALMENTE (margem parece boa)` ou `(margem insuficiente)`. Sem preço-teto: `SEM DADOS DE DIVIDENDO`. Descartada: `DESCARTADA`.
Margem mínima: **20%** na B3, **15%** nos EUA.

## 4. Setores cíclicos (mediana, não média)

- B3: Petróleo, Gás e Combustíveis; Mineração e Siderurgia; Papel e Celulose.
- EUA: Energy; Basic Materials.

Para esses, o julgamento usa valores normalizados pelos anos disponíveis (cerca de 4), nunca a média de razões, porque um ano de lucro quase zero explodiria o P/L daquele ano. Calcula-se a **mediana dos valores brutos** (EPS, lucro, patrimônio, dívida, caixa, EBITDA) e depois:
- ROE normalizado = lucro mediano ÷ patrimônio mediano;
- P/L normalizado = cotação atual ÷ EPS mediano (se EPS mediano > 0);
- Dívida líquida/EBITDA normalizado = (dívida mediana − caixa mediano) ÷ EBITDA mediano.

Esses três substituem os valores de hoje no veredito, no Graham e no PEG.

## 5. Vereditos e tolerância

Tolerância = **15%** do limite. Cada critério tem três faixas: passa, "quase" (dentro dos 15%) e reprova (fora dos 15%). Dado ausente não reprova.

Regra de decisão, nesta ordem:
1. qualquer reprovação clara → **Descartada** (com os motivos);
2. dado faltando e nenhum "quase" → **Revisar manualmente (dado insuficiente)**;
3. algum "quase" ou dado faltando junto com "quase" → **Quase Aprovada (revisar)**;
4. passou em tudo e o setor é Financeiro (sem métrica de dívida) → **Aprovada com Ressalva**;
5. passou em tudo → **Aprovada (Pura)**.

### Critérios B3
| Critério | Passa | "Quase" | Reprova |
|---|---|---|---|
| ROE (usado) | ≥ 12% | 10,2% a < 12% | < 10,2% |
| Dív. líq./EBITDA (exceto Financeiro) | < 3,0 | 3,0 a 3,45 | > 3,45 |
| Preço justo: Graham **ou** PEG | Graham ≤ 22,5 **ou** PEG ≤ 1,5 | Graham ≤ 25,875 ou PEG ≤ 1,725 | acima disso nos dois |

Se Graham e PEG estiverem ausentes, o critério vira "dado faltando".
Crescimento (CAGR de receita e lucro, meta 10% a.a.) **não elimina**: só pontua.

### Critérios EUA (ações)
| Critério | Passa | "Quase" | Reprova |
|---|---|---|---|
| ROE **ou** ROIC | ROE ≥ 12% ou ROIC ≥ 10% | ROE ≥ 10,2% ou ROIC ≥ 8,5% | abaixo disso |
| Dív. líq./EBITDA (exceto Financials) | < 3,0 | 3,0 a 3,45 | > 3,45 |
| FCF Yield | ≥ 4% | 3,4% a < 4% | < 3,4% |
| Payout | ≤ 75% | 75% a 86,25% | > 86,25% |
| Shareholder Yield **ou** Dividend Yield | SY ≥ 3,5% ou DY ≥ 2% | SY de 2,975% a < 3,5% | abaixo disso |

**Exceção de alto crescimento (FCF Yield):** se (CAGR do lucro ≥ 15% **ou** CAGR da receita ≥ 15%) **e** (ROE ≥ 20% **ou** ROIC ≥ 20%), o mínimo de FCF Yield cai de 4% para **1,5%** (a faixa "quase" vira 1,275% a < 1,5%). Quando a exceção é usada e o FCF Yield está abaixo de 4%, o motivo registra isso.

## 6. Pontuação final (0 a 100)

Só ordena quem já foi avaliado, não elimina ninguém. Cada ingrediente vira uma nota de 0 a 100 por interpolação linear entre um mínimo e um máximo, com corte nas pontas. Dado ausente = nota neutra **50**.

**B3** (pesos: margem 40%, ROE 25%, crescimento 20%, dívida 15%)
- Margem: 0% → 0; 100% → 100.
- ROE: 0% → 0; 40% → 100.
- Crescimento = média do CAGR de receita e de lucro (0 se nenhum existir): −20% a.a. → 0; +30% a.a. → 100.
- Dívida (invertida): 3x → 0; 0x → 100.

**EUA** (pesos: margem 35%, shareholder yield 25%, qualidade 20%, dívida 20%)
- Margem: 0% → 0; 50% → 100.
- Shareholder yield: 0% → 0; 8% → 100.
- Qualidade = o maior entre ROE e (ROIC × 1,2): 0% → 0; 30% → 100.
- Dívida (invertida): 3x → 0; 0x → 100.

Ordenação: nota decrescente, depois margem decrescente.

## 7. Fórmula Mágica (Greenblatt)

Não elimina nada. Exclui Financeiro/seguros/utilities (B3: Financeiro, Seguros e Previdência, Energia Elétrica, Saneamento e Gás, Saneamento e Serviços; EUA: Financials, Utilities, Real Estate). Mantém só quem tem Earnings Yield > 0 e ROIC > 0. Ranqueia por Earnings Yield (maior = 1) e por ROIC (maior = 1), soma as duas posições e ordena pela menor soma.

## 8. ETFs (só EUA)

Taxa de administração normalizada: valor bruto > 1,0 → descartado (vazio); ≤ 0,01 → lido como fração; entre 0,01 e 1,0 → dividido por 100.

| Resultado | Condição |
|---|---|
| ETF Core Aprovado | taxa ≤ 0,15% **e** patrimônio > US$ 1 bi |
| ETF Aprovado | taxa ≤ 0,20% **e** patrimônio > US$ 1 bi |
| Reprovado | taxa acima de 0,20% ou patrimônio ≤ US$ 1 bi (com o motivo) |
| Revisar manualmente (dado insuficiente) | taxa ausente |

Extra: CAGR dos dividendos dos últimos 5 anos completos.

## 9. Saídas

Ranking completo ordenado por nota, top 20 "Elegível para compra", Fórmula Mágica, diagnóstico das cíclicas (valores ano a ano), diagnóstico de ETFs, histórico acumulativo das rodadas (uma "fotografia" por execução) e, na B3, histórico de indicadores das aprovadas (5 anos) e um backtest simples.

## 10. Pontos de atenção (para o outro chat decidir)

1. O Yahoo muda o formato de alguns campos entre versões. O `dividendYield` às vezes vem em fração (0,05) e às vezes em percentual (5). Conferir antes de usar.
2. Os parâmetros (6%, 8%, 4%, 22,5, 1,5, 15%, 20%, 15% de tolerância, pesos) são julgamento, não resultado de backtest.
3. O preço-teto dos EUA soma recompra ao dividendo, mas a conta de DY não desconta a retenção de 30% do IRS sobre dividendos pagos a brasileiros.
4. O preço-teto por dividendos distorce em empresas com dividendo extraordinário num dos 3 anos (a margem fica exagerada). Considerar mediana ou teto de payout.
5. O yfinance é gratuito e sofre bloqueio por excesso de pedidos: usar pausas, novas tentativas e cache.
6. Limites do Yahoo: poucos anos de balanço (cerca de 4), então CAGR e medianas têm base curta.
7. Não é recomendação de investimento.
