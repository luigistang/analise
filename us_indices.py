"""
Triagem (screening) de ações e ETFs americanos (NYSE/NASDAQ), adaptando
metodologias clássicas de value investing (Graham, Bazin/Barsi, Greenblatt)
para a realidade do mercado dos EUA. Usa yfinance.

ADAPTAÇÕES PRINCIPAIS EM RELAÇÃO AO MODELO BRASILEIRO:
- Dividend Yield sozinho não é o bastante nos EUA, onde empresas devolvem
  capital via RECOMPRA DE AÇÕES tanto quanto via dividendo. Por isso o
  critério de renda usa o Shareholder Yield (dividendo + recompra), não só
  dividendo.
- Preço Teto usa DY Teto de 4% (mais baixo que os 6% do Brasil — ações
  americanas maduras historicamente pagam menos dividendo direto, parte do
  retorno vem de recompra e valorização).
- Entram também: Free Cash Flow Yield (qualidade do caixa gerado) e Payout
  Ratio máximo (sustentabilidade do dividendo).
- ETFs seguem uma trilha de avaliação TOTALMENTE separada das ações (taxa
  de administração, patrimônio sob gestão, histórico de dividendo) — não
  faz sentido pedir ROE ou dívida de um ETF.

AVISOS IMPORTANTES:
- Dividendos pagos por empresas americanas a residentes no Brasil sofrem
  retenção automática de 30% na fonte pelo IRS — isso NÃO está embutido
  nos cálculos de yield abaixo (são todos brutos, antes do imposto).
- O dividendo/recompra projetados são médias históricas, não previsões
  reais do futuro.
- Os parâmetros numéricos (tolerância de 15%, ROE 12%, FCF Yield 4%,
  Payout 75%, Shareholder Yield 3,5%, margem de segurança 15%) vêm de
  literatura clássica adaptada, não foram validados por backtest formal
  nessa lista específica.
- Ferramenta de apoio à decisão, não recomendação de investimento.
"""

import os
import time
from datetime import date

import pandas as pd
import yfinance as yf

# -----------------------------------------------------------------------
# 1) ENTRADA DE DADOS
# -----------------------------------------------------------------------

def normalizar_ticker_yahoo(ticker: str) -> str:
    """Yahoo usa hífen em vez de ponto pra classes de ação (BRK.B -> BRK-B)."""
    return ticker.strip().upper().replace(".", "-")


def normalizar_expense_ratio(valor_bruto):
    """
    O yfinance não é consistente na escala da taxa de administração (expense
    ratio): às vezes vem como fração (0,0003 = 0,03% a.a.) e às vezes como
    percentual direto (0,03 = 0,03% a.a.). Os dois casos são <= 1,0, então
    um único corte não resolve. A regra aqui:
      - valor > 1,0          -> dado inválido, devolve None
      - valor <= 0,01        -> já é fração (0,0003 -> 0,03%), usa direto
      - 0,01 < valor <= 1,0  -> veio como percentual direto (0,03 = 0,03%),
                                divide por 100 e vira fração
    Limite conhecido: um ETF com taxa REAL acima de 1% reportada como fração
    (ex: 0,015) seria lido como percentual. Não é o caso de ETFs core/dividendos.
    Devolve SEMPRE fração (0,0003 = 0,03% a.a.).
    """
    if valor_bruto is None or pd.isna(valor_bruto):
        return None
    if valor_bruto > 1.0:
        return None
    if valor_bruto <= 0.01:
        return float(valor_bruto)
    return float(valor_bruto) / 100


def carregar_lista_ativos(caminho_csv: str):
    """
    Lê o CSV com colunas Nome, Ticker, Setor, Tipo (Ação ou ETF).
    Devolve a lista de tickers (como aparecem no CSV, ex: "BRK.B") e um
    dicionário ticker -> {"nome", "setor", "tipo"}.
    """
    df = pd.read_csv(caminho_csv, encoding="utf-8-sig")
    tickers = []
    info_ativo = {}

    for _, linha in df.iterrows():
        ticker = str(linha["Ticker"]).strip().upper()
        if ticker not in info_ativo:
            tickers.append(ticker)
        info_ativo[ticker] = {
            "nome": linha["Nome"],
            "setor": linha["Setor"],
            "tipo": linha["Tipo"],
        }

    return tickers, info_ativo


# -----------------------------------------------------------------------
# 2) FUNÇÕES AUXILIARES DE CÁLCULO (reaproveitadas pra ações e cíclicas)
# -----------------------------------------------------------------------

def _cagr_de_serie(serie: pd.Series):
    """CAGR entre o valor mais antigo e o mais recente de uma série anual."""
    serie = serie.dropna()
    if len(serie) < 2:
        return None
    valor_recente = serie.iloc[0]
    valor_antigo = serie.iloc[-1]
    anos = len(serie) - 1
    if valor_antigo <= 0 or valor_recente <= 0:
        return None
    return (valor_recente / valor_antigo) ** (1 / anos) - 1


def _primeiro_valor_disponivel(tabela: pd.DataFrame, nomes_possiveis: list, coluna=None):
    """Tenta cada nome de linha possível (nomes de conta variam entre empresas)."""
    for nome in nomes_possiveis:
        if nome in tabela.index:
            serie = tabela.loc[nome]
            if coluna is not None:
                if coluna in tabela.columns and pd.notna(serie.get(coluna)):
                    return serie[coluna]
            else:
                valor = serie.dropna()
                if not valor.empty:
                    return valor.iloc[0]
    return None


def calcular_cagr_e_ultimos_valores(acao_yf: yf.Ticker):
    """
    A partir de .financials (anual), calcula CAGR de receita e lucro, e
    devolve também o EPS e o EBIT do ano mais recente disponível.
    """
    try:
        financeiro = acao_yf.financials
        cagr_receita = _cagr_de_serie(financeiro.loc["Total Revenue"]) if "Total Revenue" in financeiro.index else None
        cagr_lucro = _cagr_de_serie(financeiro.loc["Net Income"]) if "Net Income" in financeiro.index else None
        eps_recente = _primeiro_valor_disponivel(financeiro, ["Diluted EPS", "Basic EPS"], financeiro.columns[0] if not financeiro.empty else None)
        ebit_recente = _primeiro_valor_disponivel(financeiro, ["EBIT", "Operating Income"], financeiro.columns[0] if not financeiro.empty else None)
        return cagr_receita, cagr_lucro, eps_recente, ebit_recente
    except Exception:
        return None, None, None, None


ANOS_MEDIA_DIVIDENDO_BARSI = 3  # anos COMPLETOS usados na média de dividendo


def calcular_dividendo_medio_anos_completos(acao_yf: yf.Ticker, quantidade_anos: int = ANOS_MEDIA_DIVIDENDO_BARSI):
    """
    Média dos dividendos REALMENTE pagos (não estimativa) nos últimos
    `quantidade_anos` anos CALENDÁRIO completos (o ano corrente nunca
    entra, porque ainda não terminou).
    """
    try:
        dividendos = acao_yf.dividends
        if dividendos.empty:
            return None
        dividendos.index = dividendos.index.tz_localize(None)
        ano_atual = date.today().year
        por_ano = dividendos.groupby(dividendos.index.year).sum()
        anos_completos = sorted(ano for ano in por_ano.index if ano < ano_atual)
        ultimos = anos_completos[-quantidade_anos:]
        if not ultimos:
            return None
        media = por_ano.loc[ultimos].mean()
        return round(media, 4) if media > 0 else None
    except Exception:
        return None


def calcular_recompra_media_anos_completos(acao_yf: yf.Ticker, quantidade_anos: int = ANOS_MEDIA_DIVIDENDO_BARSI):
    """
    Média do valor gasto em recompra de ações (buyback) nos últimos anos
    disponíveis no fluxo de caixa anual. Vem negativo no demonstrativo
    (é saída de caixa) — converte pra positivo.
    """
    try:
        fluxo = acao_yf.cashflow
        linha_recompra = None
        for nome in ("Repurchase Of Capital Stock", "Common Stock Repurchased", "Repurchase Of Common Stock"):
            if nome in fluxo.index:
                linha_recompra = fluxo.loc[nome].dropna().abs()
                break
        if linha_recompra is None or linha_recompra.empty:
            return None
        valores = linha_recompra.iloc[:quantidade_anos]
        return round(valores.mean(), 2) if len(valores) > 0 else None
    except Exception:
        return None


# -----------------------------------------------------------------------
# 3) COLETA DE DADOS — AÇÕES
# -----------------------------------------------------------------------

INDICES_DESEJADOS = {
    "pl_preco_lucro": "trailingPE",
    "pl_futuro": "forwardPE",
    "pvp_preco_valor_patrimonial": "priceToBook",
    "roe_retorno_sobre_patrimonio": "returnOnEquity",
    "dividend_yield_atual": "dividendYield",
    "divida_total": "totalDebt",
    "caixa_total": "totalCash",
    "ebitda": "ebitda",
    "valor_empresa_ev": "enterpriseValue",
    "valor_contabil_por_acao": "bookValue",
    "acoes_em_circulacao": "sharesOutstanding",
    "payout_ratio_yahoo": "payoutRatio",
    "fluxo_caixa_livre": "freeCashflow",
    "valor_de_mercado": "marketCap",
}


def coletar_dados_ativo(ticker: str, info_ativo: dict) -> dict:
    """Busca os dados de UM ativo (ação ou ETF) — ramifica o cálculo específico mais adiante."""
    ticker_yahoo = normalizar_ticker_yahoo(ticker)
    dados_cadastro = info_ativo.get(ticker, {})
    tipo = dados_cadastro.get("tipo", "Ação")

    linha = {
        "ticker": ticker,
        "nome": dados_cadastro.get("nome"),
        "setor": dados_cadastro.get("setor"),
        "tipo": tipo,
    }

    try:
        acao_yf = yf.Ticker(ticker_yahoo)
        info = acao_yf.info

        for nome_coluna, campo_yahoo in INDICES_DESEJADOS.items():
            linha[nome_coluna] = info.get(campo_yahoo)

        # Preço atual: currentPrice às vezes vem vazio (comum em ETF) — cai pra regularMarketPrice/previousClose
        linha["cotacao_atual"] = (
            info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
        )

        # Campos específicos de ETF
        taxa_bruta = (
            info.get("annualReportExpenseRatio") or info.get("netExpenseRatio") or info.get("expenseRatio")
        )
        linha["taxa_administracao"] = normalizar_expense_ratio(taxa_bruta)
        linha["patrimonio_sob_gestao"] = info.get("totalAssets")

        if tipo == "ETF":
            # ETF não usa balanço/fluxo de caixa de empresa — só preço, yield e taxa
            linha["status_coleta"] = "ok" if info else "vazio (provável bloqueio/rate-limit do Yahoo)"
            return linha

        # --- Só pra ações a partir daqui ---
        cagr_receita, cagr_lucro, eps_recente, ebit_recente = calcular_cagr_e_ultimos_valores(acao_yf)
        linha["cagr_receita"] = cagr_receita
        linha["cagr_lucro"] = cagr_lucro
        linha["ebit"] = ebit_recente

        linha["dividendo_medio_anos_completos"] = calcular_dividendo_medio_anos_completos(acao_yf)
        linha["recompra_media_anos_completos"] = calcular_recompra_media_anos_completos(acao_yf)

        # P/L verificado pelo balanço (mesma checagem de consistência usada no modelo B3)
        pl_verificado = None
        cotacao = linha.get("cotacao_atual")
        if eps_recente and eps_recente > 0 and cotacao:
            pl_verificado = round(cotacao / eps_recente, 2)
        linha["pl_verificado_pelo_balanco"] = pl_verificado
        pl_do_yahoo = linha.get("pl_preco_lucro")
        linha["pl_inconsistente"] = False
        if pl_verificado and pl_do_yahoo and pl_do_yahoo > 0:
            linha["pl_inconsistente"] = abs(pl_verificado - pl_do_yahoo) / pl_do_yahoo > 0.5

        # ROIC e Earnings Yield (Fórmula Mágica de Greenblatt)
        patrimonio = (
            linha["valor_contabil_por_acao"] * linha["acoes_em_circulacao"]
            if linha.get("valor_contabil_por_acao") and linha.get("acoes_em_circulacao") else None
        )
        linha["earnings_yield"] = None
        linha["roic"] = None
        ev = linha.get("valor_empresa_ev")
        if ebit_recente and ev and ev > 0:
            linha["earnings_yield"] = round(ebit_recente / ev, 4)
        divida, caixa = linha.get("divida_total"), linha.get("caixa_total")
        if ebit_recente and patrimonio and divida is not None and caixa is not None:
            capital_investido = divida + patrimonio - caixa
            if capital_investido > 0:
                linha["roic"] = round(ebit_recente / capital_investido, 4)

        # Free Cash Flow Yield
        linha["fcf_yield"] = None
        if linha.get("fluxo_caixa_livre") and linha.get("valor_de_mercado"):
            linha["fcf_yield"] = round(linha["fluxo_caixa_livre"] / linha["valor_de_mercado"], 4)

        # Buyback Yield e Shareholder Yield
        linha["buyback_yield"] = None
        if linha.get("recompra_media_anos_completos") and linha.get("valor_de_mercado"):
            linha["buyback_yield"] = round(linha["recompra_media_anos_completos"] / linha["valor_de_mercado"], 4)
        dy = linha.get("dividend_yield_atual") or 0
        bb = linha.get("buyback_yield") or 0
        linha["shareholder_yield"] = round(dy + bb, 4) if (linha.get("dividend_yield_atual") is not None or linha.get("buyback_yield") is not None) else None

        # Payout: usa o do Yahoo; se faltar, calcula a partir de DY e EPS
        if linha.get("payout_ratio_yahoo") is None and eps_recente and eps_recente > 0 and linha.get("dividend_yield_atual") and cotacao:
            dividendo_ttm = linha["dividend_yield_atual"] * cotacao
            linha["payout_ratio_yahoo"] = round(dividendo_ttm / eps_recente, 4)

        linha["status_coleta"] = "ok" if info and len(info) >= 5 else "vazio (provável bloqueio/rate-limit do Yahoo)"

    except Exception as erro:
        linha["status_coleta"] = f"erro: {erro}"

    return linha


def coletar_todos(tickers: list, info_ativo: dict, pausa_segundos: float = 2.0, tentativas: int = 3) -> pd.DataFrame:
    resultados = []
    total = len(tickers)
    for i, ticker in enumerate(tickers, start=1):
        for tentativa in range(1, tentativas + 1):
            print(f"[{i}/{total}] Buscando {ticker}... (tentativa {tentativa})")
            linha = coletar_dados_ativo(ticker, info_ativo)
            if linha.get("status_coleta") == "ok":
                break
            time.sleep(pausa_segundos * tentativa)
        resultados.append(linha)
        time.sleep(pausa_segundos)
    return pd.DataFrame(resultados)


# -----------------------------------------------------------------------
# 4) SETORES CÍCLICOS — mesma lógica de mediana usada no modelo B3
# -----------------------------------------------------------------------

SETORES_CICLICOS = ["Energy", "Basic Materials"]


def obter_historico_anual(ticker: str) -> pd.DataFrame:
    """Valores BRUTOS ano a ano (lucro, EPS, patrimônio, dívida, caixa, EBITDA)."""
    ticker_yahoo = normalizar_ticker_yahoo(ticker)
    acao_yf = yf.Ticker(ticker_yahoo)
    financeiro = acao_yf.financials
    balanco = acao_yf.balance_sheet

    linhas = []
    for col in financeiro.columns:
        lucro = _primeiro_valor_disponivel(financeiro, ["Net Income"], col)
        eps = _primeiro_valor_disponivel(financeiro, ["Diluted EPS", "Basic EPS"], col)
        ebitda = _primeiro_valor_disponivel(financeiro, ["EBITDA", "Normalized EBITDA"], col)
        patrimonio = _primeiro_valor_disponivel(balanco, ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"], col)
        divida = _primeiro_valor_disponivel(balanco, ["Total Debt"], col)
        caixa = _primeiro_valor_disponivel(balanco, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"], col)
        linhas.append({
            "ticker": ticker, "ano": col.year, "lucro_bruto": lucro, "eps_bruto": eps,
            "patrimonio_bruto": patrimonio, "divida_bruta": divida, "caixa_bruto": caixa, "ebitda_bruto": ebitda,
        })
    return pd.DataFrame(linhas)


def obter_medias_ciclo(historico: pd.DataFrame, cotacao_atual: float):
    """
    MEDIANA dos valores brutos (nunca a média das razões — um ano de lucro
    quase zero faria o P/L daquele ano explodir e estragar a média).
    """
    if historico.empty:
        return None, None, None
    eps_md = historico["eps_bruto"].median()
    lucro_md = historico["lucro_bruto"].median()
    patrim_md = historico["patrimonio_bruto"].median()
    divida_md = historico["divida_bruta"].median()
    caixa_md = historico["caixa_bruto"].median()
    ebitda_md = historico["ebitda_bruto"].median()

    roe_norm = (lucro_md / patrim_md) if pd.notna(lucro_md) and pd.notna(patrim_md) and patrim_md else None
    divida_ebitda_norm = ((divida_md - caixa_md) / ebitda_md) if pd.notna(divida_md) and pd.notna(caixa_md) and pd.notna(ebitda_md) and ebitda_md else None
    pl_norm = (cotacao_atual / eps_md) if pd.notna(cotacao_atual) and pd.notna(eps_md) and eps_md > 0 else None

    return (
        round(roe_norm, 4) if roe_norm is not None else None,
        round(pl_norm, 2) if pl_norm is not None else None,
        round(divida_ebitda_norm, 2) if divida_ebitda_norm is not None else None,
    )


def enriquecer_ciclicas_com_media(tabela_bruta: pd.DataFrame, pausa_segundos: float = 2.0):
    df = tabela_bruta.copy()
    df["roe_medio_ciclo"] = None
    df["pl_medio_ciclo"] = None
    df["divida_ebitda_medio_ciclo"] = None

    tickers_ciclicos = df[(df["setor"].isin(SETORES_CICLICOS)) & (df["tipo"] == "Ação")]["ticker"].tolist()
    diagnosticos = []

    for i, ticker in enumerate(tickers_ciclicos, start=1):
        print(f"[{i}/{len(tickers_ciclicos)}] Buscando histórico da cíclica {ticker}...")
        cotacao = df.loc[df["ticker"] == ticker, "cotacao_atual"].iloc[0]
        try:
            historico = obter_historico_anual(ticker)
            if not historico.empty:
                diagnosticos.append(historico)
            roe_m, pl_m, divida_m = obter_medias_ciclo(historico, cotacao)
        except Exception as erro:
            print(f"  Não deu pra buscar histórico de {ticker}: {erro}")
            roe_m, pl_m, divida_m = None, None, None
        idx = df[df["ticker"] == ticker].index
        df.loc[idx, "roe_medio_ciclo"] = roe_m
        df.loc[idx, "pl_medio_ciclo"] = pl_m
        df.loc[idx, "divida_ebitda_medio_ciclo"] = divida_m
        time.sleep(pausa_segundos)

    diagnostico = pd.concat(diagnosticos, ignore_index=True) if diagnosticos else pd.DataFrame()
    return df, diagnostico


# -----------------------------------------------------------------------
# 5) CRITÉRIOS DE QUALIDADE E VALUATION — AÇÕES
# -----------------------------------------------------------------------

SETORES_SEM_METRICA_DE_DIVIDA = ["Financials"]
SETORES_EXCLUIDOS_MAGIC_FORMULA = ["Financials", "Utilities", "Real Estate"]
TOLERANCIA_QUASE = 0.15
DY_TETO_BASE = 0.04

FILTRO = {
    "roe_minimo": 0.12,
    "roic_minimo": 0.10,
    "divida_liquida_ebitda_maxima": 3.0,
    "fcf_yield_minimo": 0.04,
    "fcf_yield_minimo_alto_crescimento": 0.015,  # exceção para empresas de alto crescimento
    "crescimento_minimo_excecao": 0.15,          # CAGR lucro ou receita >= 15% a.a.
    "retorno_minimo_excecao": 0.20,              # ROE ou ROIC >= 20%
    "payout_maximo": 0.75,
    "shareholder_yield_minimo": 0.035,
    "dividend_yield_minimo_alternativo": 0.02,  # "Dividend Aristocrat" simplificado
    "margem_de_seguranca_minima": 15.0,  # em %
}


def definir_veredito(linha: pd.Series):
    """
    5 critérios eliminatórios, cada um com 15% de tolerância (vira "Quase
    Aprovada" em vez de descartar de vez) e falta de dado tratada à parte
    (não é reprovação, é "Revisar manualmente"). Mesma filosofia de 4
    categorias usada no modelo B3.
    """
    setor = linha["setor"]
    sem_divida = setor in SETORES_SEM_METRICA_DE_DIVIDA

    roe = linha.get("roe_usado")
    roic = linha.get("roic")
    divida_ebitda = linha.get("divida_liquida_ebitda_usada")
    fcf_yield = linha.get("fcf_yield")
    payout = linha.get("payout_ratio_yahoo")
    shareholder_yield = linha.get("shareholder_yield")
    dividend_yield = linha.get("dividend_yield_atual")

    falhas_graves, quase_falhas, dados_faltando = [], [], []

    # 1) ROE OU ROIC
    roe_ok = pd.notna(roe) and roe >= FILTRO["roe_minimo"]
    roic_ok = pd.notna(roic) and roic >= FILTRO["roic_minimo"]
    roe_quase = pd.notna(roe) and FILTRO["roe_minimo"] * (1 - TOLERANCIA_QUASE) <= roe < FILTRO["roe_minimo"]
    roic_quase = pd.notna(roic) and FILTRO["roic_minimo"] * (1 - TOLERANCIA_QUASE) <= roic < FILTRO["roic_minimo"]
    if not (roe_ok or roic_ok):
        if roe_quase or roic_quase:
            quase_falhas.append(f"ROE={roe}/ROIC={roic} perto do mínimo (12%/10%)")
        elif pd.isna(roe) and pd.isna(roic):
            dados_faltando.append("ROE e ROIC")
        else:
            falhas_graves.append(f"ROE={roe} e ROIC={roic} bem abaixo do mínimo (12%/10%)")

    # 2) Dívida Líquida/EBITDA (não aplica a Financials)
    if not sem_divida:
        limite_quase = FILTRO["divida_liquida_ebitda_maxima"] * (1 + TOLERANCIA_QUASE)
        if pd.isna(divida_ebitda):
            dados_faltando.append("Dívida Líquida/EBITDA")
        elif divida_ebitda > limite_quase:
            falhas_graves.append(f"Dívida Líq./EBITDA={divida_ebitda:.1f}x bem acima de {FILTRO['divida_liquida_ebitda_maxima']}x")
        elif divida_ebitda >= FILTRO["divida_liquida_ebitda_maxima"]:
            quase_falhas.append(f"Dívida Líq./EBITDA={divida_ebitda:.1f}x perto do limite")

    # 3) FCF Yield (com exceção para alto crescimento + alta rentabilidade)
    cagr_l = linha.get("cagr_lucro")
    cagr_r = linha.get("cagr_receita")
    crescimento_ok = (pd.notna(cagr_l) and cagr_l >= FILTRO["crescimento_minimo_excecao"]) or \
                     (pd.notna(cagr_r) and cagr_r >= FILTRO["crescimento_minimo_excecao"])
    retorno_alto = (pd.notna(roe) and roe >= FILTRO["retorno_minimo_excecao"]) or \
                   (pd.notna(roic) and roic >= FILTRO["retorno_minimo_excecao"])
    excecao_fcf = bool(crescimento_ok and retorno_alto)
    fcf_minimo = FILTRO["fcf_yield_minimo_alto_crescimento"] if excecao_fcf else FILTRO["fcf_yield_minimo"]
    nota_excecao = ""
    limite_fcf_quase = fcf_minimo * (1 - TOLERANCIA_QUASE)
    if pd.isna(fcf_yield):
        dados_faltando.append("FCF Yield")
    elif fcf_yield < limite_fcf_quase:
        falhas_graves.append(f"FCF Yield={fcf_yield*100:.1f}% bem abaixo de {fcf_minimo*100:.1f}%")
    elif fcf_yield < fcf_minimo:
        quase_falhas.append(f"FCF Yield={fcf_yield*100:.1f}% perto do mínimo")
    elif excecao_fcf and fcf_yield < FILTRO["fcf_yield_minimo"]:
        nota_excecao = f"Exceção FCF Yield aplicada: {fcf_yield*100:.1f}% aceito (alto crescimento + ROE/ROIC>=20%, mín. 1,5%)"

    # 4) Payout sustentável
    limite_payout_quase = FILTRO["payout_maximo"] * (1 + TOLERANCIA_QUASE)
    if pd.isna(payout):
        dados_faltando.append("Payout Ratio")
    elif payout > limite_payout_quase:
        falhas_graves.append(f"Payout={payout*100:.0f}% bem acima de {FILTRO['payout_maximo']*100:.0f}%")
    elif payout > FILTRO["payout_maximo"]:
        quase_falhas.append(f"Payout={payout*100:.0f}% perto do limite")

    # 5) Shareholder Yield OU Dividend Yield (Aristocrat simplificado)
    sy_ok = pd.notna(shareholder_yield) and shareholder_yield >= FILTRO["shareholder_yield_minimo"]
    dy_ok = pd.notna(dividend_yield) and dividend_yield >= FILTRO["dividend_yield_minimo_alternativo"]
    sy_quase = pd.notna(shareholder_yield) and FILTRO["shareholder_yield_minimo"] * (1 - TOLERANCIA_QUASE) <= shareholder_yield < FILTRO["shareholder_yield_minimo"]
    if not (sy_ok or dy_ok):
        if sy_quase:
            quase_falhas.append(f"Shareholder Yield={shareholder_yield} perto do mínimo de 3,5%")
        elif pd.isna(shareholder_yield) and pd.isna(dividend_yield):
            dados_faltando.append("Shareholder Yield / Dividend Yield")
        else:
            falhas_graves.append(f"Shareholder Yield={shareholder_yield} e Dividend Yield={dividend_yield} abaixo do mínimo")

    if falhas_graves:
        return "Descartada", "; ".join(falhas_graves)
    if dados_faltando and not quase_falhas:
        return "Revisar manualmente (dado insuficiente)", "Faltou dado: " + "; ".join(dados_faltando)
    if dados_faltando or quase_falhas:
        return "Quase Aprovada (revisar)", "; ".join(quase_falhas + [f"dado insuficiente: {d}" for d in dados_faltando] + ([nota_excecao] if nota_excecao else []))
    if sem_divida:
        return "Aprovada com Ressalva", nota_excecao
    return "Aprovada (Pura)", nota_excecao


DY_TETO_POR_VEREDITO = {
    "Aprovada (Pura)": DY_TETO_BASE,
    "Aprovada com Ressalva": DY_TETO_BASE,
    "Quase Aprovada (revisar)": DY_TETO_BASE,
    "Revisar manualmente (dado insuficiente)": DY_TETO_BASE,
}

PESOS_PONTUACAO = {"margem_de_seguranca": 0.35, "shareholder_yield": 0.25, "qualidade": 0.20, "divida": 0.20}


def calcular_pontuacao_final(linha: pd.Series):
    def normalizar(valor, minimo, maximo, neutro=50.0):
        if pd.isna(valor):
            return neutro
        proporcao = (valor - minimo) / (maximo - minimo)
        return round(min(max(proporcao, 0.0), 1.0) * 100, 1)

    nota_margem = normalizar(linha.get("margem_de_seguranca_pct"), 0, 50)
    nota_sy = normalizar(linha.get("shareholder_yield"), 0, 0.08)
    nota_qualidade = normalizar(max(linha.get("roe_usado") or 0, (linha.get("roic") or 0) * 1.2), 0, 0.30)
    nota_divida = normalizar(linha.get("divida_liquida_ebitda_usada"), 3.0, 0.0)

    pontuacao = (
        nota_margem * PESOS_PONTUACAO["margem_de_seguranca"]
        + nota_sy * PESOS_PONTUACAO["shareholder_yield"]
        + nota_qualidade * PESOS_PONTUACAO["qualidade"]
        + nota_divida * PESOS_PONTUACAO["divida"]
    )
    return round(pontuacao, 1)


def calcular_valuation_acoes(tabela: pd.DataFrame) -> pd.DataFrame:
    df = tabela.copy()

    df["divida_liquida_ebitda"] = df.apply(
        lambda l: round((l["divida_total"] - l["caixa_total"]) / l["ebitda"], 2)
        if pd.notna(l.get("divida_total")) and pd.notna(l.get("caixa_total")) and l.get("ebitda")
        else None, axis=1,
    )

    df["usou_media_ciclica"] = df["setor"].isin(SETORES_CICLICOS)

    df["roe_usado"] = df.apply(
        lambda l: l["roe_medio_ciclo"] if l["usou_media_ciclica"] and pd.notna(l.get("roe_medio_ciclo")) else l.get("roe_retorno_sobre_patrimonio"),
        axis=1,
    )
    df["pl_usado"] = df.apply(
        lambda l: l["pl_medio_ciclo"] if l["usou_media_ciclica"] and pd.notna(l.get("pl_medio_ciclo")) else (
            l["pl_verificado_pelo_balanco"] if l.get("pl_inconsistente") and pd.notna(l.get("pl_verificado_pelo_balanco")) else l.get("pl_preco_lucro")
        ), axis=1,
    )
    df["divida_liquida_ebitda_usada"] = df.apply(
        lambda l: l["divida_ebitda_medio_ciclo"] if l["usou_media_ciclica"] and pd.notna(l.get("divida_ebitda_medio_ciclo")) else l.get("divida_liquida_ebitda"),
        axis=1,
    )

    df[["veredito", "motivo_descarte"]] = df.apply(lambda l: pd.Series(definir_veredito(l)), axis=1)

    def preco_teto(l):
        if l["veredito"] == "Descartada":
            return None
        dividendo = l.get("dividendo_medio_anos_completos") or 0
        acoes = l.get("acoes_em_circulacao")
        recompra_total = l.get("recompra_media_anos_completos") or 0
        recompra_por_acao = (recompra_total / acoes) if acoes else 0
        total = dividendo + recompra_por_acao
        if total <= 0:
            return None
        return round(total / DY_TETO_POR_VEREDITO[l["veredito"]], 2)

    df["preco_teto"] = df.apply(preco_teto, axis=1)
    df["margem_de_seguranca_pct"] = df.apply(
        lambda l: round((l["preco_teto"] - l["cotacao_atual"]) / l["cotacao_atual"] * 100, 2)
        if pd.notna(l.get("preco_teto")) and pd.notna(l.get("cotacao_atual")) else None, axis=1,
    )

    def status(l):
        if l["veredito"] == "Descartada":
            return "DESCARTADA"
        if pd.isna(l["margem_de_seguranca_pct"]):
            return "SEM DADOS DE DIVIDENDO/RECOMPRA"
        margem_boa = l["margem_de_seguranca_pct"] >= FILTRO["margem_de_seguranca_minima"]
        if l["veredito"] in ("Quase Aprovada (revisar)", "Revisar manualmente (dado insuficiente)"):
            return "REVISAR MANUALMENTE (margem parece boa)" if margem_boa else "REVISAR MANUALMENTE (margem insuficiente)"
        return "ELEGÍVEL PARA COMPRA" if margem_boa else "AGUARDAR (margem insuficiente)"

    df["status"] = df.apply(status, axis=1)
    df["pontuacao_final"] = df.apply(calcular_pontuacao_final, axis=1)
    df = df.sort_values(["pontuacao_final", "margem_de_seguranca_pct"], ascending=[False, False], na_position="last")
    return df


def calcular_magic_formula(tabela: pd.DataFrame) -> pd.DataFrame:
    """Earnings Yield (EBIT/EV) x ROIC, cruzados — exclui Financials/Utilities/Real Estate."""
    df = tabela[(tabela["tipo"] == "Ação") & (~tabela["setor"].isin(SETORES_EXCLUIDOS_MAGIC_FORMULA))].copy()
    df = df[df["earnings_yield"].notna() & df["roic"].notna() & (df["earnings_yield"] > 0) & (df["roic"] > 0)]
    if df.empty:
        return pd.DataFrame(columns=["ticker", "nome", "setor", "earnings_yield", "roic", "rank_earnings_yield", "rank_roic", "soma_dos_rankings"])
    df["rank_earnings_yield"] = df["earnings_yield"].rank(ascending=False, method="min").astype(int)
    df["rank_roic"] = df["roic"].rank(ascending=False, method="min").astype(int)
    df["soma_dos_rankings"] = df["rank_earnings_yield"] + df["rank_roic"]
    return df.sort_values("soma_dos_rankings")[["ticker", "nome", "setor", "earnings_yield", "roic", "rank_earnings_yield", "rank_roic", "soma_dos_rankings"]]


# -----------------------------------------------------------------------
# 6) TRILHA SEPARADA — ETFs
# -----------------------------------------------------------------------

FILTRO_ETF = {
    "taxa_administracao_maxima": 0.0020,       # aprovação: taxa <= 0,20% a.a.
    "taxa_administracao_maxima_core": 0.0015,  # selo "Core": taxa <= 0,15% a.a.
    "patrimonio_minimo": 1_000_000_000,        # aprovação: patrimônio > US$ 1 bi
}


def avaliar_etf(linha: pd.Series):
    """
    Aprovação de ETF: taxa de administração <= 0,20% a.a. E patrimônio sob
    gestão > US$ 1 bilhão. Dentro dos aprovados, quem tem taxa <= 0,15%
    ganha o selo "Core". A taxa chega aqui já normalizada (fração:
    0,0003 = 0,03% a.a.) — ver normalizar_expense_ratio.
    """
    taxa = linha.get("taxa_administracao")
    aum = linha.get("patrimonio_sob_gestao")
    aum_ok = pd.notna(aum) and aum > FILTRO_ETF["patrimonio_minimo"]

    if pd.isna(taxa):
        return "Revisar manualmente (dado insuficiente)", "Taxa de administração indisponível"

    taxa_ok = taxa <= FILTRO_ETF["taxa_administracao_maxima"]
    if taxa_ok and aum_ok:
        if taxa <= FILTRO_ETF["taxa_administracao_maxima_core"]:
            return "ETF Core Aprovado", ""
        return "ETF Aprovado", ""

    motivo = []
    if not taxa_ok:
        motivo.append(f"taxa {taxa*100:.2f}% a.a. acima do limite de 0,20%")
    if not aum_ok:
        motivo.append("patrimônio sob gestão abaixo de US$ 1 bi (ou indisponível)")
    return "Reprovado", "; ".join(motivo)


def montar_diagnostico_etfs(tabela_bruta: pd.DataFrame, pausa_segundos: float = 2.0) -> pd.DataFrame:
    """Pra cada ETF: avalia taxa/AUM e calcula CAGR de dividendos dos últimos 5 anos."""
    etfs = tabela_bruta[tabela_bruta["tipo"] == "ETF"].copy()
    cagr_5anos = []

    for ticker in etfs["ticker"]:
        try:
            acao_yf = yf.Ticker(normalizar_ticker_yahoo(ticker))
            dividendos = acao_yf.dividends
            if dividendos.empty:
                cagr_5anos.append(None)
                continue
            dividendos.index = dividendos.index.tz_localize(None)
            ano_atual = date.today().year
            por_ano = dividendos.groupby(dividendos.index.year).sum()
            anos_completos = sorted(a for a in por_ano.index if a < ano_atual)
            ultimos5 = anos_completos[-5:]
            cagr_5anos.append(_cagr_de_serie(por_ano.loc[ultimos5][::-1]) if len(ultimos5) >= 2 else None)
        except Exception:
            cagr_5anos.append(None)
        time.sleep(pausa_segundos)

    etfs["cagr_dividendos_5anos"] = cagr_5anos
    etfs[["veredito_etf", "motivo_etf"]] = etfs.apply(lambda l: pd.Series(avaliar_etf(l)), axis=1)
    colunas = ["ticker", "nome", "setor", "cotacao_atual", "dividend_yield_atual", "taxa_administracao",
               "patrimonio_sob_gestao", "cagr_dividendos_5anos", "veredito_etf", "motivo_etf"]
    return etfs[colunas].sort_values("veredito_etf")


# -----------------------------------------------------------------------
# 7) HISTÓRICO DE EXECUÇÕES (snapshot acumulativo, nunca sobrescreve)
# -----------------------------------------------------------------------

def salvar_snapshot_historico(tabela_final: pd.DataFrame, caminho_arquivo: str = "historico_execucoes_usa.csv"):
    colunas = ["data_execucao", "ticker", "nome", "setor", "veredito", "status",
               "cotacao_atual", "shareholder_yield", "roe_usado", "preco_teto", "margem_de_seguranca_pct"]
    snapshot = tabela_final.copy()
    snapshot["data_execucao"] = date.today().isoformat()
    snapshot = snapshot[[c for c in colunas if c in snapshot.columns]]
    existe = os.path.isfile(caminho_arquivo)
    snapshot.to_csv(caminho_arquivo, mode="a", header=not existe, index=False, encoding="utf-8-sig")
    return caminho_arquivo


# -----------------------------------------------------------------------
# 8) EXECUÇÃO PRINCIPAL
# -----------------------------------------------------------------------

if __name__ == "__main__":
    CAMINHO_CSV = "tickers_usa.csv"
    QUANTIDADE_TOP = 20

    tickers, info_ativo = carregar_lista_ativos(CAMINHO_CSV)
    print(f"{len(tickers)} ativos carregados ({sum(1 for v in info_ativo.values() if v['tipo']=='Ação')} ações, "
          f"{sum(1 for v in info_ativo.values() if v['tipo']=='ETF')} ETFs).\n")

    tabela_bruta = coletar_todos(tickers, info_ativo)

    print("\nBuscando histórico das ações de setor cíclico (Energy, Basic Materials)...")
    tabela_bruta, diagnostico_ciclicas = enriquecer_ciclicas_com_media(tabela_bruta)
    if not diagnostico_ciclicas.empty:
        diagnostico_ciclicas.to_csv("diagnostico_ciclicas_usa.csv", index=False, encoding="utf-8-sig")
        print("Diagnóstico das cíclicas salvo em: diagnostico_ciclicas_usa.csv")

    # --- Trilha das ações ---
    acoes_bruta = tabela_bruta[tabela_bruta["tipo"] == "Ação"].reset_index(drop=True)
    tabela_final = calcular_valuation_acoes(acoes_bruta)

    colunas_prioritarias = ["ticker", "nome", "setor", "veredito", "status", "pontuacao_final",
                             "preco_teto", "cotacao_atual", "margem_de_seguranca_pct", "motivo_descarte"]
    demais = [c for c in tabela_final.columns if c not in colunas_prioritarias]
    tabela_final = tabela_final[colunas_prioritarias + demais]
    tabela_final.to_csv("ranking_valuation_usa.csv", index=False, encoding="utf-8-sig")

    colunas_resumo = ["ticker", "nome", "setor", "veredito", "status", "pontuacao_final", "preco_teto",
                       "cotacao_atual", "margem_de_seguranca_pct", "shareholder_yield", "roe_usado",
                       "roic", "fcf_yield", "payout_ratio_yahoo"]
    print("\n=== RANKING COMPLETO DE AÇÕES (maior para menor pontuação final) ===")
    print(tabela_final[colunas_resumo].to_string(index=False))

    top20 = tabela_final[tabela_final["status"] == "ELEGÍVEL PARA COMPRA"].head(QUANTIDADE_TOP)
    top20.to_csv("top20_us_recommended.csv", index=False, encoding="utf-8-sig")
    print(f"\n=== TOP {len(top20)} RECOMENDADAS ===")
    print(top20[colunas_resumo].to_string(index=False))

    magic = calcular_magic_formula(tabela_final)
    magic.to_csv("magic_formula_wallstreet.csv", index=False, encoding="utf-8-sig")
    print("\n=== TOP 20 FÓRMULA MÁGICA DE GREENBLATT (EUA) ===")
    print(magic.head(20).to_string(index=False))

    descartadas = tabela_final[tabela_final["veredito"] == "Descartada"]
    print(f"\n=== POR QUE {len(descartadas)} AÇÕES FORAM DESCARTADAS ===")
    print(descartadas[["ticker", "motivo_descarte"]].to_string(index=False))

    # --- Trilha dos ETFs ---
    etfs_bruta = tabela_bruta[tabela_bruta["tipo"] == "ETF"].reset_index(drop=True)
    diagnostico_etfs = montar_diagnostico_etfs(etfs_bruta)
    diagnostico_etfs.to_csv("diagnostico_etfs_usa.csv", index=False, encoding="utf-8-sig")
    print("\n=== DIAGNÓSTICO DE ETFs ===")
    print(diagnostico_etfs.to_string(index=False))

    # --- Histórico acumulativo ---
    arq_snapshot = salvar_snapshot_historico(tabela_final)
    print(f"\nFotografia de hoje acrescentada a: {arq_snapshot}")

    print("\n" + "=" * 70)
    print("AVISO: dividendos de empresas americanas pagos a residentes no Brasil")
    print("sofrem retenção de 30% na fonte pelo IRS — os yields acima são BRUTOS,")
    print("antes desse imposto. Ferramenta de apoio à decisão, não recomendação.")
    print("=" * 70)
