"""
Coleta indicadores fundamentalistas de uma lista PRÉ-APROVADA de ações da B3
(setores perenes + bens industriais, escolhidos manualmente por você),
aplica filtros de qualidade e calcula o Preço Teto / Margem de Segurança
pelo método de Barsi/Gordon (dividendo projetado / dividend yield exigido).

COMO FUNCIONA, PASSO A PASSO:
  1) Lê o CSV com a sua lista de empresas aprovadas (Nome, Ticker, Setor,
     Subsetor) — o mesmo formato do arquivo que você me mandou.
  2) Para cada ticker, busca no Yahoo Finance: cotação, P/L, ROE, dívida,
     EBITDA, dividendo e o histórico de receita/lucro dos últimos anos.
  3) Aplica os filtros de qualidade: ROE, Número de Graham (P/L x P/VP,
     método Benjamin Graham), PEG (P/L / crescimento do lucro, método
     Peter Lynch) e Dívida Líquida/EBITDA.
  4) Calcula o Preço Teto e a Margem de Segurança de cada ação aprovada.
  5) Salva tudo num CSV, já ordenado da melhor pra pior oportunidade.

IMPORTANTE (avisos sobre os números):
- O "dividendo projetado" usado aqui é o dividendo dos últimos 12 meses que
  o próprio Yahoo Finance informa (campo dividendRate). Não é uma previsão
  de verdade pro futuro — é uma aproximação. Se você tiver uma estimativa
  melhor (ex: baseada em resultados trimestrais recentes ou guidance da
  empresa), pode editar a coluna "dividendo_projetado" no CSV final antes
  de recalcular, ou ajustar direto no código.
- Isso não é recomendação de investimento. É uma ferramenta de cálculo
  para ajudar você a organizar a sua própria análise — sempre confira os
  números nos relatórios oficiais (RI da empresa, CVM) antes de decidir.
"""

import os
import time
from datetime import date

import pandas as pd
import yfinance as yf

# -----------------------------------------------------------------------
# 1) LISTA PRÉ-APROVADA DE EMPRESAS (vem do seu CSV)
# -----------------------------------------------------------------------

def carregar_lista_aprovada(caminho_csv: str):
    """
    Lê o CSV com colunas: Nome, Ticker, Setor, Subsetor.
    Uma mesma empresa pode ter mais de um ticker na mesma célula
    (ex: "ITUB3, ITUB4") — a função separa isso em tickers individuais.

    Devolve:
      - lista de tickers (um por ação)
      - dicionário ticker -> {"nome", "setor", "subsetor"}
    """
    df = pd.read_csv(caminho_csv, encoding="utf-8-sig")
    tickers = []
    info_empresa = {}

    for _, linha in df.iterrows():
        tickers_da_empresa = [t.strip() for t in str(linha["Ticker"]).split(",")]
        for ticker in tickers_da_empresa:
            # Algumas ações aparecem em mais de uma linha do CSV (ex: uma
            # holding que também é listada por outro motivo). Guardamos só
            # uma vez cada ticker, pra não processar a mesma ação 2x.
            if ticker not in info_empresa:
                tickers.append(ticker)
            info_empresa[ticker] = {
                "nome": linha["Nome"],
                "setor": linha["Setor"],
                "subsetor": linha["Subsetor"],
            }

    return tickers, info_empresa


# -----------------------------------------------------------------------
# 2) COLETA DOS DADOS NO YAHOO FINANCE
# -----------------------------------------------------------------------

# Campos simples que vêm direto do "info" de cada ação
INDICES_DESEJADOS = {
    "cotacao_atual": "currentPrice",
    "pl_preco_lucro": "trailingPE",
    "pvp_preco_valor_patrimonial": "priceToBook",
    "roe_retorno_sobre_patrimonio": "returnOnEquity",
    "dividendo_projetado": "dividendRate",   # ver aviso no topo do arquivo
    "dividend_yield_atual": "dividendYield",
    "divida_total": "totalDebt",
    "caixa_total": "totalCash",
    "ebitda": "ebitda",
}


def _cagr_de_serie(serie: pd.Series):
    """
    Calcula o CAGR (crescimento anual composto) de uma série de valores
    anuais. Precisa de pelo menos 2 anos e valores positivos nas pontas
    (não dá pra calcular crescimento % de um prejuízo).
    """
    serie = serie.dropna()
    if len(serie) < 2:
        return None

    valor_recente = serie.iloc[0]     # yfinance devolve do ano mais recente pro mais antigo
    valor_antigo = serie.iloc[-1]
    anos = len(serie) - 1

    if valor_antigo <= 0 or valor_recente <= 0:
        return None

    return (valor_recente / valor_antigo) ** (1 / anos) - 1


def calcular_cagr_receita_e_lucro(acao_yf: yf.Ticker):
    """
    Usa o histórico de resultados anuais (geralmente os últimos 4 anos
    disponíveis no Yahoo Finance) para calcular o CAGR de receita e de
    lucro líquido. Também devolve o lucro por ação (EPS) do ano mais
    recente disponível no balanço — usado só pra conferir se o P/L que o
    Yahoo reporta em `.info` bate com os próprios balanços da empresa (ver
    coletar_dados_acao).
    """
    try:
        financeiro = acao_yf.financials
        cagr_receita = _cagr_de_serie(financeiro.loc["Total Revenue"])
        cagr_lucro = _cagr_de_serie(financeiro.loc["Net Income"])

        eps_mais_recente = None
        if not financeiro.empty:
            for nome in ("Diluted EPS", "Basic EPS"):
                if nome in financeiro.index:
                    valor = financeiro.loc[nome].dropna()
                    if not valor.empty:
                        eps_mais_recente = valor.iloc[0]
                        break

        return cagr_receita, cagr_lucro, eps_mais_recente
    except Exception:
        return None, None, None


def _primeiro_valor_disponivel(tabela: pd.DataFrame, nomes_possiveis: list, coluna):
    """
    Alguns dados mudam de nome de linha dependendo da empresa (ex: bancos
    chamam patrimônio de um jeito, indústrias de outro). Essa função tenta
    cada nome possível até achar um que exista na tabela, pra aquele ano.
    """
    for nome in nomes_possiveis:
        if nome in tabela.index and coluna in tabela.columns:
            valor = tabela.loc[nome, coluna]
            if pd.notna(valor):
                return valor
    return None


def obter_historico_indicadores(ticker: str) -> pd.DataFrame:
    """
    Monta a evolução ANO A ANO de P/L, ROE, Dívida Líquida/EBITDA, dividendo
    pago por ação e payout ratio (dividendo ÷ lucro por ação) — os mesmos
    "indicadores do presente" que o resto do script só calcula pra hoje.
    Usa os balanços anuais (financials/balance_sheet) mais o histórico de
    preço e de dividendos, pra montar o P/L de cada ano com o preço daquela
    época (não o preço de hoje).

    AVISO: o Yahoo Finance (gratuito) só disponibiliza os balanços anuais
    dos últimos 4 anos pra praticamente todas as ações — não é uma
    limitação deste código, é o que a fonte de dados entrega de graça.
    Então mesmo pedindo 5 anos, o normal é vir só 4 linhas por ação. Se
    quiser garantir 5 anos completos de verdade, seria necessário uma
    fonte paga (ex: Status Invest, Fundamentus com histórico maior, ou
    uma API paga como a da própria B3/CVM).
    """
    ticker_yahoo = f"{ticker}.SA"
    acao_yf = yf.Ticker(ticker_yahoo)

    financeiro = acao_yf.financials
    balanco = acao_yf.balance_sheet
    precos = acao_yf.history(period="6y")

    dividendos = acao_yf.dividends
    if not dividendos.empty:
        dividendos.index = dividendos.index.tz_localize(None)

    linhas = []
    for data_coluna in financeiro.columns:
        lucro = _primeiro_valor_disponivel(financeiro, ["Net Income"], data_coluna)
        eps = _primeiro_valor_disponivel(financeiro, ["Diluted EPS", "Basic EPS"], data_coluna)
        ebitda = _primeiro_valor_disponivel(financeiro, ["EBITDA", "Normalized EBITDA"], data_coluna)

        patrimonio = _primeiro_valor_disponivel(
            balanco, ["Stockholders Equity", "Common Stock Equity",
                      "Total Equity Gross Minority Interest"], data_coluna
        )
        divida = _primeiro_valor_disponivel(balanco, ["Total Debt"], data_coluna)
        caixa = _primeiro_valor_disponivel(
            balanco, ["Cash And Cash Equivalents",
                      "Cash Cash Equivalents And Short Term Investments"], data_coluna
        )

        roe_ano = (lucro / patrimonio) if lucro is not None and patrimonio else None
        divida_ebitda_ano = ((divida - caixa) / ebitda) if (divida is not None and caixa is not None and ebitda) else None

        # Preço de fechamento mais próximo daquela data (não o preço de hoje)
        preco_no_periodo = None
        if not precos.empty:
            precos_ate_data = precos[precos.index.tz_localize(None) <= data_coluna]
            if not precos_ate_data.empty:
                preco_no_periodo = precos_ate_data["Close"].iloc[-1]

        pl_ano = (preco_no_periodo / eps) if preco_no_periodo is not None and eps else None

        # Dividendo pago naquele ano (soma de todos os pagamentos do ano) e
        # payout ratio (quanto do lucro por ação virou dividendo)
        dividendo_do_ano = 0.0
        if not dividendos.empty:
            dividendo_do_ano = dividendos[dividendos.index.year == data_coluna.year].sum()
        payout_ratio_ano = (dividendo_do_ano / eps) if eps and eps > 0 else None

        linhas.append({
            "ticker": ticker,
            "ano": data_coluna.year,
            "roe": round(roe_ano, 4) if roe_ano is not None else None,
            "divida_liquida_ebitda": round(divida_ebitda_ano, 2) if divida_ebitda_ano is not None else None,
            "pl": round(pl_ano, 2) if pl_ano is not None else None,
            "dividendo_por_acao_ano": round(dividendo_do_ano, 2),
            "payout_ratio": round(payout_ratio_ano, 2) if payout_ratio_ano is not None else None,
            # Valores brutos (não a razão já calculada) — usados pra tirar a
            # MEDIANA correta em setor cíclico (ver obter_medias_ciclo).
            # Importante: nunca tire a média de uma razão ano a ano — um
            # ano de lucro quase zero faz o P/L daquele ano explodir (ex:
            # 200x) e estraga a média inteira. O jeito certo é tirar a
            # mediana dos valores brutos primeiro, e só then calcular a razão.
            "lucro_bruto": lucro,
            "eps_bruto": eps,
            "patrimonio_bruto": patrimonio,
            "divida_bruta": divida,
            "caixa_bruto": caixa,
            "ebitda_bruto": ebitda,
        })

    return pd.DataFrame(linhas).sort_values("ano", ascending=False)


def obter_medias_ciclo(historico: pd.DataFrame, cotacao_atual: float):
    """
    Pra setores cíclicos (commodity), calcula um ROE, P/L e Dívida
    Líquida/EBITDA "normalizados" (representativos do ciclo todo), em vez
    de usar só o número de hoje — que pode estar distorcido por estar no
    topo ou no fundo do ciclo de preço da commodity.

    IMPORTANTE — como isso é calculado certo: a gente NUNCA tira a média
    das razões ano a ano (ex: média dos P/L de cada ano). Se um ano teve
    lucro quase zero, o P/L daquele ano explode (pode virar 100x, 200x) e
    estraga a média inteira pra cima — foi exatamente isso que distorceu
    a Petrobras na rodada anterior (P/L "médio" de 24, quando o P/L de
    hoje dela é 5). O jeito certo (e o que o próprio Graham recomendava
    pra empresa cíclica) é tirar a MEDIANA dos valores brutos (lucro,
    patrimônio, dívida, EBITDA) primeiro, e só DEPOIS calcular a razão.

    Recebe o histórico já buscado (ver enriquecer_ciclicas_com_media) e a
    cotação atual (pro P/L normalizado = preço de hoje ÷ lucro por ação
    típico do ciclo).
    """
    if historico.empty:
        return None, None, None

    eps_mediano = historico["eps_bruto"].median()
    lucro_mediano = historico["lucro_bruto"].median()
    patrimonio_mediano = historico["patrimonio_bruto"].median()
    divida_mediana = historico["divida_bruta"].median()
    caixa_mediano = historico["caixa_bruto"].median()
    ebitda_mediano = historico["ebitda_bruto"].median()

    roe_normalizado = (
        lucro_mediano / patrimonio_mediano
        if pd.notna(lucro_mediano) and pd.notna(patrimonio_mediano) and patrimonio_mediano
        else None
    )
    divida_ebitda_normalizada = (
        (divida_mediana - caixa_mediano) / ebitda_mediano
        if pd.notna(divida_mediana) and pd.notna(caixa_mediano) and pd.notna(ebitda_mediano) and ebitda_mediano
        else None
    )
    pl_normalizado = (
        cotacao_atual / eps_mediano
        if pd.notna(cotacao_atual) and pd.notna(eps_mediano) and eps_mediano > 0
        else None
    )

    return (
        round(roe_normalizado, 4) if roe_normalizado is not None else None,
        round(pl_normalizado, 2) if pl_normalizado is not None else None,
        round(divida_ebitda_normalizada, 2) if divida_ebitda_normalizada is not None else None,
    )


def enriquecer_ciclicas_com_media(tabela_bruta: pd.DataFrame, cache_historico: dict,
                                   pausa_segundos: float = 2.0):
    """
    Pra cada ação de setor cíclico (SETORES_CICLICOS), busca o histórico
    UMA VEZ, guarda no cache_historico (pra não buscar de novo depois, se
    essa mesma ação for aprovada e precisar do histórico outra vez lá em
    montar_historico_das_aprovadas) e calcula os valores normalizados —
    guardados em colunas separadas (roe_medio_ciclo, pl_medio_ciclo,
    divida_ebitda_medio_ciclo). O julgamento final (calcular_valuation)
    usa esses valores pras cíclicas e o valor de hoje pras demais.

    Devolve (tabela_enriquecida, diagnostico) — diagnostico é o histórico
    ano a ano (com os valores BRUTOS: lucro, EPS, patrimônio, dívida,
    EBITDA) de TODAS as cíclicas, aprovadas ou não. Serve pra você
    conferir com os próprios olhos os números que geraram a mediana, em
    vez de confiar cegamente no cálculo — principalmente quando o
    resultado parecer estranho (ex: P/L normalizado bem diferente do P/L
    de hoje).
    """
    df = tabela_bruta.copy()
    df["roe_medio_ciclo"] = None
    df["pl_medio_ciclo"] = None
    df["divida_ebitda_medio_ciclo"] = None

    tickers_ciclicos = df[df["setor"].isin(SETORES_CICLICOS)]["ticker"].tolist()
    diagnosticos = []

    for i, ticker in enumerate(tickers_ciclicos, start=1):
        print(f"[{i}/{len(tickers_ciclicos)}] Buscando histórico da cíclica {ticker}...")
        cotacao_atual = df.loc[df["ticker"] == ticker, "cotacao_atual"].iloc[0]
        try:
            historico = obter_historico_indicadores(ticker)
            cache_historico[ticker] = historico  # guarda pra reaproveitar depois
            roe_medio, pl_medio, divida_ebitda_medio = obter_medias_ciclo(historico, cotacao_atual)
            if not historico.empty:
                historico_com_cotacao = historico.copy()
                historico_com_cotacao["cotacao_atual_referencia"] = cotacao_atual
                diagnosticos.append(historico_com_cotacao)
        except Exception as erro:
            print(f"  Não deu pra buscar o histórico de {ticker}: {erro}")
            roe_medio, pl_medio, divida_ebitda_medio = None, None, None

        indice = df[df["ticker"] == ticker].index
        df.loc[indice, "roe_medio_ciclo"] = roe_medio
        df.loc[indice, "pl_medio_ciclo"] = pl_medio
        df.loc[indice, "divida_ebitda_medio_ciclo"] = divida_ebitda_medio
        time.sleep(pausa_segundos)

    diagnostico = pd.concat(diagnosticos, ignore_index=True) if diagnosticos else pd.DataFrame()
    return df, diagnostico


def montar_historico_das_aprovadas(tabela_final: pd.DataFrame, cache_historico: dict = None,
                                    pausa_segundos: float = 2.0) -> pd.DataFrame:
    """
    Roda obter_historico_indicadores só pras ações que passaram no filtro
    (Aprovada Pura ou com Ressalva) — não vale a pena gastar mais pedidos
    no Yahoo com quem já foi descartado. Depois junta setor/veredito/status
    de cada ação na tabela de histórico, pra você não precisar cruzar os
    dois arquivos na mão.

    cache_historico: se uma ação já teve o histórico buscado antes (ex: já
    era cíclica e teve a média calculada em enriquecer_ciclicas_com_media),
    reaproveita em vez de pedir tudo de novo ao Yahoo.
    """
    cache_historico = cache_historico or {}
    aprovadas = tabela_final[tabela_final["veredito"] != "Descartada"]
    tickers_aprovados = aprovadas["ticker"].tolist()
    tabelas = []

    for i, ticker in enumerate(tickers_aprovados, start=1):
        if ticker in cache_historico:
            print(f"[{i}/{len(tickers_aprovados)}] {ticker} já tinha histórico buscado (reaproveitando, sem pedir de novo ao Yahoo)")
            tabelas.append(cache_historico[ticker])
            continue
        print(f"[{i}/{len(tickers_aprovados)}] Buscando histórico de {ticker}...")
        try:
            tabelas.append(obter_historico_indicadores(ticker))
        except Exception as erro:
            print(f"  Não deu pra buscar o histórico de {ticker}: {erro}")
        time.sleep(pausa_segundos)

    if not tabelas:
        historico = pd.DataFrame(columns=["ticker", "ano", "roe", "divida_liquida_ebitda",
                                           "pl", "dividendo_por_acao_ano", "payout_ratio"])
    else:
        historico = pd.concat(tabelas, ignore_index=True)

    historico = historico.merge(
        aprovadas[["ticker", "nome", "setor", "veredito", "status"]], on="ticker", how="left"
    )

    historico = historico.merge(
        _consistencia_de_dividendos(historico), on="ticker", how="left"
    )

    # Setor e resultado da aprovação logo depois do ticker, ano por último
    colunas_ordem = [
        "ticker", "nome", "setor", "veredito", "status", "ano", "roe",
        "divida_liquida_ebitda", "pl", "dividendo_por_acao_ano", "payout_ratio",
        "anos_pagando_dividendo", "teve_queda_de_dividendo",
    ]
    return historico[colunas_ordem]


def _consistencia_de_dividendos(historico: pd.DataFrame) -> pd.DataFrame:
    """
    Pra cada ticker, olha a série de dividendo_por_acao_ano (já calculada)
    e resume: em quantos dos anos disponíveis ela pagou dividendo (>0), e
    se o dividendo caiu em algum ano em relação ao anterior. Isso ajuda a
    pegar empresas com dividendo "de um ano só" (evento pontual) em vez de
    pagamento consistente, que é a base do método Barsi.
    """
    linhas = []
    for ticker, grupo in historico.groupby("ticker"):
        serie = grupo.sort_values("ano")["dividendo_por_acao_ano"]
        anos_pagando = int((serie > 0).sum())
        teve_queda = (
            any(serie.iloc[i] < serie.iloc[i - 1] for i in range(1, len(serie)))
            if len(serie) > 1 else None
        )
        linhas.append({
            "ticker": ticker,
            "anos_pagando_dividendo": anos_pagando,
            "teve_queda_de_dividendo": teve_queda,
        })
    return pd.DataFrame(linhas)


def coletar_dados_acao(ticker: str, info_empresa: dict) -> dict:
    """
    Busca no Yahoo Finance todos os dados de UMA ação: indicadores do
    "info" + CAGR de receita/lucro calculado a partir dos balanços.
    """
    ticker_yahoo = f"{ticker}.SA"
    dados_cadastro = info_empresa.get(ticker, {})

    linha = {
        "ticker": ticker,
        "nome": dados_cadastro.get("nome"),
        "setor": dados_cadastro.get("setor"),
        "subsetor": dados_cadastro.get("subsetor"),
    }

    try:
        acao_yf = yf.Ticker(ticker_yahoo)
        info = acao_yf.info

        for nome_coluna, campo_yahoo in INDICES_DESEJADOS.items():
            linha[nome_coluna] = info.get(campo_yahoo)

        cagr_receita, cagr_lucro, eps_mais_recente = calcular_cagr_receita_e_lucro(acao_yf)
        linha["cagr_receita"] = cagr_receita
        linha["cagr_lucro"] = cagr_lucro

        # P/L "de verificação": preço de hoje ÷ lucro por ação do balanço
        # mais recente — tudo vindo da MESMA fonte (os balanços), pra
        # checar se o P/L que o Yahoo reporta em .info (trailingPE) bate.
        # Foi assim que a gente descobriu que o P/L "de hoje" da Petrobras
        # (5,16) não batia com o lucro por ação real dos balanços dela —
        # o Yahoo tem um problema conhecido de inconsistência de dado em
        # algumas ações brasileiras (possível mistura com base em dólar).
        pl_verificado = None
        cotacao = linha.get("cotacao_atual")
        if eps_mais_recente and eps_mais_recente > 0 and cotacao:
            pl_verificado = round(cotacao / eps_mais_recente, 2)
        linha["pl_verificado_pelo_balanco"] = pl_verificado

        pl_do_yahoo = linha.get("pl_preco_lucro")
        linha["pl_inconsistente"] = False
        if pl_verificado and pl_do_yahoo and pl_do_yahoo > 0:
            divergencia = abs(pl_verificado - pl_do_yahoo) / pl_do_yahoo
            linha["pl_inconsistente"] = divergencia > 0.5  # mais de 50% de diferença

        # info vazio ou quase vazio costuma ser o Yahoo bloqueando/limitando
        # os pedidos (rate limit), não a ação não ter dados de verdade.
        if not info or len(info) < 5:
            linha["status_coleta"] = "vazio (provável bloqueio/rate-limit do Yahoo)"
        else:
            linha["status_coleta"] = "ok"

    except Exception as erro:
        for nome_coluna in list(INDICES_DESEJADOS) + ["cagr_receita", "cagr_lucro",
                                                        "pl_verificado_pelo_balanco", "pl_inconsistente"]:
            linha[nome_coluna] = None
        linha["status_coleta"] = f"erro: {erro}"

    return linha


def coletar_todas(tickers: list, info_empresa: dict, pausa_segundos: float = 2.5,
                   tentativas: int = 3) -> pd.DataFrame:
    resultados = []
    total = len(tickers)

    for i, ticker in enumerate(tickers, start=1):
        for tentativa in range(1, tentativas + 1):
            print(f"[{i}/{total}] Buscando {ticker}... (tentativa {tentativa})")
            linha = coletar_dados_acao(ticker, info_empresa)
            if linha["status_coleta"] == "ok":
                break
            time.sleep(pausa_segundos * tentativa)  # espera mais a cada nova tentativa
        resultados.append(linha)
        time.sleep(pausa_segundos)

    return pd.DataFrame(resultados)


# -----------------------------------------------------------------------
# 3) FILTROS DE QUALIDADE (regras que você definiu)
# -----------------------------------------------------------------------

FILTRO = {
    "roe_minimo": 0.12,
    "divida_liquida_ebitda_maxima": 3.0,   # não se aplica a bancos/seguradoras
    "numero_graham_maximo": 22.5,          # P/L x P/VP — método Benjamin Graham
    "peg_maximo": 1.5,                     # P/L / crescimento do lucro — método Peter Lynch
    "cagr_receita_minimo": 0.10,           # usado só na pontuação de crescimento (não elimina)
    "cagr_lucro_minimo": 0.10,             # usado só na pontuação de crescimento (não elimina)
    "margem_de_seguranca_minima": 20.0,    # em %
}

# Setores onde "Dívida Líquida/EBITDA" não faz sentido (bancos e seguradoras
# funcionam com alavancagem por natureza do negócio — não é comparável ao
# de uma indústria ou varejista).
SETORES_SEM_METRICA_DE_DIVIDA = ["Financeiro"]

# Setores cíclicos de commodity: o lucro (e por tabela, ROE, P/L e
# Dívida/EBITDA) varia muito mais do que numa "perene" de verdade, porque
# segue o preço da commodity no mercado internacional, não uma demanda
# estável. Pra essas, o julgamento usa a MÉDIA dos últimos anos em vez do
# número de hoje (ver enriquecer_ciclicas_com_media).
SETORES_CICLICOS = ["Petróleo, Gás e Combustíveis", "Mineração e Siderurgia", "Papel e Celulose"]

# Margem de tolerância: se a ação ficar perto do limite (dentro dessa %),
# em vez de descartar direto ela cai numa "zona cinzenta" pra você revisar
# manualmente — assim uma empresa boa não é descartada só por um triz.
TOLERANCIA_QUASE = 0.15  # 15% de folga

# Setores onde o dividendo reportado pelo Yahoo (dividendRate) costuma vir
# espremido ou incompleto — pra esses, o dividendo projetado é estimado
# via EPS x payout do setor, em vez de confiar no dado bruto do Yahoo (ver
# estimar_dividendo_projetado).
SETORES_PAYOUT_ESTIMADO = ["Financeiro", "Holding"]
PAYOUT_ESTIMADO_FINANCEIRO_HOLDING = 0.40  # premissa conservadora de payout

# Dividend Yield exigido no Preço Teto, conforme o veredito da ação
DY_TETO_POR_VEREDITO = {
    "Aprovada (Pura)": 0.06,
    "Aprovada com Ressalva": 0.08,
    "Quase Aprovada (revisar)": 0.08,
    "Revisar manualmente (dado insuficiente)": 0.08,
}


def estimar_dividendo_projetado(linha: pd.Series):
    """
    Pra Financeiro e Holding (ex: Itaúsa, Banco do Brasil), o campo
    dividendRate que o Yahoo Finance reporta costuma vir muito espremido
    ou incompleto (ex: R$ 0,10 quando o dividendo real é bem maior) — isso
    jogava o Preço Teto pro chão e gerava falso alarme de "margem
    insuficiente". Em vez de confiar nesse dado bruto, estima o dividendo
    a partir do lucro por ação (EPS = cotação ÷ P/L usado) multiplicado
    por um payout conservador de 40%, típico do setor bancário/holding na
    B3. Pros demais setores, usa o dividendo do Yahoo normalmente.
    """
    setor = linha["setor"]
    if setor not in SETORES_PAYOUT_ESTIMADO:
        return linha["dividendo_projetado"]

    pl_usado = linha.get("pl_usado")
    cotacao = linha.get("cotacao_atual")
    if pd.isna(pl_usado) or pl_usado is None or pl_usado <= 0 or pd.isna(cotacao):
        return linha["dividendo_projetado"]  # sem dado suficiente pra estimar, cai no valor original

    eps_estimado = cotacao / pl_usado
    return round(eps_estimado * PAYOUT_ESTIMADO_FINANCEIRO_HOLDING, 2)


def calcular_divida_liquida_sobre_ebitda(divida_total, caixa_total, ebitda):
    if pd.isna(divida_total) or pd.isna(caixa_total) or pd.isna(ebitda) or ebitda == 0:
        return None
    return (divida_total - caixa_total) / ebitda


def calcular_numero_graham(pl, pvp):
    """
    Método Benjamin Graham: combina P/L e P/VP num único número, em vez de
    travar cada um numa faixa fixa separada. Uma empresa com P/L um pouco
    mais alto ainda passa se o P/VP for baixo o suficiente (e vice-versa).
    Só faz sentido com P/L e P/VP positivos (empresa com lucro e patrimônio
    positivos) — Graham nunca aplicava isso a empresa no prejuízo.
    """
    if pd.isna(pl) or pd.isna(pvp) or pl <= 0 or pvp <= 0:
        return None
    return round(pl * pvp, 2)


def calcular_peg(pl, cagr_lucro):
    """
    Método Peter Lynch: relaciona o P/L com o crescimento do lucro (CAGR),
    em vez de julgar o P/L sozinho. PEG ≤ 1 é considerado barato, até 1,5
    ainda é aceitável. Só faz sentido com lucro crescendo (CAGR positivo) —
    empresa com lucro caindo não tem "PEG bom", tem problema.
    """
    if pd.isna(pl) or pd.isna(cagr_lucro) or pl <= 0 or cagr_lucro <= 0:
        return None
    return round(pl / (cagr_lucro * 100), 2)


def definir_veredito(linha: pd.Series):
    """
    Aplica os critérios de qualidade e devolve uma dupla:
    (veredito, motivo)

    Em vez de só "aprovada" ou "descartada", existem 4 resultados
    possíveis — pra garantir que nenhuma empresa boa se perca só por causa
    de uma margem mínima ou de um dado que faltou:

      - "Descartada": reprovou de verdade em algo, e não por pouco.
      - "Revisar manualmente (dado insuficiente)": não dá pra saber, porque
        faltou um dado (ex: Yahoo não trouxe o P/VP daquela ação) — NÃO é
        reprovação, é "não sei, olha você".
      - "Quase Aprovada (revisar)": ficou perto do limite de algum critério
        (dentro da margem de TOLERANCIA_QUASE) — vale a pena olhar antes
        de descartar de vez.
      - "Aprovada com Ressalva": passou em tudo, mas é banco/seguradora —
        setor onde a métrica de dívida não se aplica, então exigimos um
        desconto maior (DY Teto de 8%) por segurança.
      - "Aprovada (Pura)": passou em tudo, com folga, sem ressalvas.

    Preço justo é confirmado se Número de Graham OU PEG aprovar (não
    precisa dos dois) — são duas filosofias de investimento diferentes,
    exigir as duas ao mesmo tempo seria mais restritivo que só usar P/L.

    Pra setores cíclicos (SETORES_CICLICOS), ROE, P/L (usado no Graham/PEG)
    e Dívida/EBITDA usam a MÉDIA dos últimos anos em vez do número de hoje
    — pra não confundir "empresa ruim" com "empresa boa que está num
    momento fraco do ciclo de preço da commodity".
    """
    setor = linha["setor"]
    sem_metrica_de_divida = setor in SETORES_SEM_METRICA_DE_DIVIDA
    usou_media_ciclica = linha.get("usou_media_ciclica", False)

    roe = linha["roe_usado"]
    divida_ebitda = linha["divida_liquida_ebitda_usada"]
    numero_graham = linha["numero_graham"]
    peg = linha["peg_ratio"]

    falhas_graves = []    # reprovação clara, fora da margem de tolerância
    quase_falhas = []     # perto do limite, dentro da margem de tolerância
    dados_faltando = []   # não dá pra avaliar por falta de dado

    sufixo_ciclica = " (média dos últimos anos, setor cíclico)" if usou_media_ciclica else ""

    # --- ROE ---
    limite_roe_quase = FILTRO["roe_minimo"] * (1 - TOLERANCIA_QUASE)
    if pd.isna(roe):
        dados_faltando.append("ROE")
    elif roe < limite_roe_quase:
        falhas_graves.append(f"ROE={roe*100:.1f}%{sufixo_ciclica} bem abaixo do mínimo de {FILTRO['roe_minimo']*100:.0f}%")
    elif roe < FILTRO["roe_minimo"]:
        quase_falhas.append(f"ROE={roe*100:.1f}%{sufixo_ciclica} perto do mínimo de {FILTRO['roe_minimo']*100:.0f}%")

    # --- Dívida Líquida/EBITDA (só quando o setor usa essa métrica) ---
    if not sem_metrica_de_divida:
        limite_divida_quase = FILTRO["divida_liquida_ebitda_maxima"] * (1 + TOLERANCIA_QUASE)
        if pd.isna(divida_ebitda):
            dados_faltando.append("Dívida Líquida/EBITDA")
        elif divida_ebitda > limite_divida_quase:
            falhas_graves.append(f"Dívida Líq./EBITDA={divida_ebitda:.1f}x{sufixo_ciclica} bem acima do limite de {FILTRO['divida_liquida_ebitda_maxima']}x")
        elif divida_ebitda >= FILTRO["divida_liquida_ebitda_maxima"]:
            quase_falhas.append(f"Dívida Líq./EBITDA={divida_ebitda:.1f}x{sufixo_ciclica} perto do limite de {FILTRO['divida_liquida_ebitda_maxima']}x")

    # --- Preço justo: Graham OU PEG ---
    limite_graham_quase = FILTRO["numero_graham_maximo"] * (1 + TOLERANCIA_QUASE)
    limite_peg_quase = FILTRO["peg_maximo"] * (1 + TOLERANCIA_QUASE)

    graham_ok = (not pd.isna(numero_graham)) and numero_graham <= FILTRO["numero_graham_maximo"]
    peg_ok = (not pd.isna(peg)) and peg <= FILTRO["peg_maximo"]
    graham_quase = (not pd.isna(numero_graham)) and FILTRO["numero_graham_maximo"] < numero_graham <= limite_graham_quase
    peg_quase = (not pd.isna(peg)) and FILTRO["peg_maximo"] < peg <= limite_peg_quase

    graham_txt = f"Graham={numero_graham:.1f}" if not pd.isna(numero_graham) else "Graham indisponível"
    peg_txt = f"PEG={peg:.2f}" if not pd.isna(peg) else "PEG indisponível"

    if not (graham_ok or peg_ok):
        if graham_quase or peg_quase:
            quase_falhas.append(f"Preço perto do limite ({graham_txt}, {peg_txt})")
        elif pd.isna(numero_graham) and pd.isna(peg):
            dados_faltando.append("Graham e PEG (faltou P/L, P/VP ou CAGR de lucro)")
        else:
            falhas_graves.append(f"Preço caro nos dois métodos ({graham_txt}, {peg_txt})")

    # --- Decide o veredito final ---
    if falhas_graves:
        return "Descartada", "; ".join(falhas_graves)

    if dados_faltando and not quase_falhas:
        return "Revisar manualmente (dado insuficiente)", "Faltou dado pra avaliar: " + "; ".join(dados_faltando)

    if dados_faltando or quase_falhas:
        motivo = "; ".join(quase_falhas + [f"dado insuficiente: {d}" for d in dados_faltando])
        return "Quase Aprovada (revisar)", motivo

    if sem_metrica_de_divida:
        return "Aprovada com Ressalva", ""
    return "Aprovada (Pura)", ""


def calcular_pontuacao_crescimento(cagr_receita, cagr_lucro):
    """
    Não elimina ninguém — só classifica o crescimento de quem já passou
    nos critérios obrigatórios, pra ajudar a desempatar/comparar.
    Pontuação = média do CAGR de receita e lucro (0 quando não tem dado).
    """
    valores = [v for v in (cagr_receita, cagr_lucro) if not pd.isna(v)]
    pontuacao = sum(valores) / len(valores) if valores else 0.0

    if not valores:
        classificacao = "Sem dado de crescimento"
    elif pontuacao >= FILTRO["cagr_receita_minimo"]:
        classificacao = "Crescimento forte (≥10% a.a.)"
    elif pontuacao >= 0:
        classificacao = "Crescimento moderado (0-10% a.a.)"
    else:
        classificacao = "Receita/lucro em queda"

    return round(pontuacao, 4), classificacao


# Peso de cada critério na pontuação final (soma 100%) — usado só pra
# ORDENAR quem já foi aprovado, não pra decidir quem é aprovado ou não.
PESOS_PONTUACAO = {
    "margem_de_seguranca": 0.40,
    "roe": 0.25,
    "crescimento": 0.20,
    "divida": 0.15,
}


def calcular_pontuacao_final(linha: pd.Series):
    """
    Nota de 0 a 100 pra ORDENAR as aprovadas (não elimina ninguém — isso já
    foi decidido em definir_veredito). Serve pra você não precisar olhar
    30 empresas aprovadas uma por uma: só olha o topo do ranking.

    Combina 4 ingredientes, cada um transformado numa nota de 0 a 100 antes
    de juntar (senão margem de segurança em "35" dominaria ROE em "0,18"
    só por ter uma escala numérica maior):
      - Margem de segurança (peso 40%): 0% margem = nota 0, 100%+ = nota 100
      - ROE (peso 25%): 0% ROE = nota 0, 40%+ = nota 100
      - Crescimento (peso 20%, usa a mesma pontuação de CAGR já calculada):
        -20% a.a. = nota 0, +30% a.a. = nota 100
      - Dívida Líquida/EBITDA (peso 15%, invertido — menos dívida é melhor):
        0x = nota 100, 3x (o limite) = nota 0

    Quando um valor não está disponível, usa nota neutra (50) pra não
    punir nem premiar a empresa só por falta de dado.
    """
    def normalizar(valor, minimo, maximo, neutro=50.0):
        if pd.isna(valor):
            return neutro
        proporcao = (valor - minimo) / (maximo - minimo)
        return round(min(max(proporcao, 0.0), 1.0) * 100, 1)

    nota_margem = normalizar(linha.get("margem_de_seguranca_pct"), 0, 100)
    nota_roe = normalizar(linha.get("roe_usado"), 0, 0.40)
    nota_crescimento = normalizar(linha.get("pontuacao_crescimento"), -0.20, 0.30)
    nota_divida = normalizar(linha.get("divida_liquida_ebitda_usada"), 3.0, 0.0)  # invertido

    pontuacao = (
        nota_margem * PESOS_PONTUACAO["margem_de_seguranca"]
        + nota_roe * PESOS_PONTUACAO["roe"]
        + nota_crescimento * PESOS_PONTUACAO["crescimento"]
        + nota_divida * PESOS_PONTUACAO["divida"]
    )
    return round(pontuacao, 1)


def calcular_valuation(tabela: pd.DataFrame) -> pd.DataFrame:
    """
    Aplica os filtros, calcula Preço Teto e Margem de Segurança, e devolve
    a tabela final já pronta pro ranking.
    """
    df = tabela.copy()

    df["divida_liquida_ebitda"] = df.apply(
        lambda linha: calcular_divida_liquida_sobre_ebitda(
            linha["divida_total"], linha["caixa_total"], linha["ebitda"]
        ),
        axis=1,
    )

    # Pra setores cíclicos (commodity), o julgamento usa a MÉDIA dos
    # últimos anos em vez do número de hoje — ver SETORES_CICLICOS e
    # enriquecer_ciclicas_com_media. Pra todo o resto, usa o valor normal.
    df["usou_media_ciclica"] = df["setor"].isin(SETORES_CICLICOS)

    def roe_usado(linha):
        if linha["usou_media_ciclica"] and pd.notna(linha.get("roe_medio_ciclo")):
            return linha["roe_medio_ciclo"]
        return linha["roe_retorno_sobre_patrimonio"]

    def pl_usado(linha):
        if linha["usou_media_ciclica"] and pd.notna(linha.get("pl_medio_ciclo")):
            return linha["pl_medio_ciclo"]
        # Mesmo fora das cíclicas: se o P/L que o Yahoo reporta em .info não
        # bate com o lucro por ação dos próprios balanços da empresa (caso
        # descoberto na Petrobras), usa o P/L verificado pelo balanço em
        # vez do inconsistente.
        if linha.get("pl_inconsistente") and pd.notna(linha.get("pl_verificado_pelo_balanco")):
            return linha["pl_verificado_pelo_balanco"]
        return linha["pl_preco_lucro"]

    def divida_usada(linha):
        if linha["usou_media_ciclica"] and pd.notna(linha.get("divida_ebitda_medio_ciclo")):
            return linha["divida_ebitda_medio_ciclo"]
        return linha["divida_liquida_ebitda"]

    df["roe_usado"] = df.apply(roe_usado, axis=1)
    df["pl_usado"] = df.apply(pl_usado, axis=1)
    df["divida_liquida_ebitda_usada"] = df.apply(divida_usada, axis=1)

    df["dividendo_projetado_usado"] = df.apply(estimar_dividendo_projetado, axis=1)
    df["dividendo_ajustado_setor_financeiro"] = df["setor"].isin(SETORES_PAYOUT_ESTIMADO)

    df["numero_graham"] = df.apply(
        lambda linha: calcular_numero_graham(
            linha["pl_usado"], linha["pvp_preco_valor_patrimonial"]
        ),
        axis=1,
    )

    df["peg_ratio"] = df.apply(
        lambda linha: calcular_peg(linha["pl_usado"], linha["cagr_lucro"]),
        axis=1,
    )

    df[["veredito", "motivo_descarte"]] = df.apply(
        lambda linha: pd.Series(definir_veredito(linha)), axis=1
    )

    df[["pontuacao_crescimento", "classificacao_crescimento"]] = df.apply(
        lambda linha: pd.Series(
            calcular_pontuacao_crescimento(linha["cagr_receita"], linha["cagr_lucro"])
        ),
        axis=1,
    )

    def preco_teto(linha):
        if linha["veredito"] == "Descartada":
            return None
        dy_teto = DY_TETO_POR_VEREDITO[linha["veredito"]]
        dividendo = linha["dividendo_projetado_usado"]
        if pd.isna(dividendo) or dividendo <= 0:
            return None
        return round(dividendo / dy_teto, 2)

    df["preco_teto"] = df.apply(preco_teto, axis=1)

    def margem_de_seguranca(linha):
        if pd.isna(linha["preco_teto"]) or pd.isna(linha["cotacao_atual"]):
            return None
        return round(
            (linha["preco_teto"] - linha["cotacao_atual"]) / linha["cotacao_atual"] * 100, 2
        )

    df["margem_de_seguranca_pct"] = df.apply(margem_de_seguranca, axis=1)

    def status_final(linha):
        if linha["veredito"] == "Descartada":
            return "DESCARTADA"
        if pd.isna(linha["margem_de_seguranca_pct"]):
            return "SEM DADOS DE DIVIDENDO"
        margem_boa = linha["margem_de_seguranca_pct"] >= FILTRO["margem_de_seguranca_minima"]
        if linha["veredito"] in ("Quase Aprovada (revisar)", "Revisar manualmente (dado insuficiente)"):
            return "REVISAR MANUALMENTE (margem parece boa)" if margem_boa else "REVISAR MANUALMENTE (margem insuficiente)"
        return "ELEGÍVEL PARA COMPRA" if margem_boa else "AGUARDAR (margem insuficiente)"

    df["status"] = df.apply(status_final, axis=1)

    df["pontuacao_final"] = df.apply(calcular_pontuacao_final, axis=1)

    df = df.sort_values(
        ["pontuacao_final", "margem_de_seguranca_pct"],
        ascending=[False, False],
        na_position="last",
    )
    return df


def destaques_diversificados(df: pd.DataFrame, quantidade: int = 5) -> pd.DataFrame:
    """
    Pega a melhor ação elegível de cada setor, até completar 'quantidade'
    empresas — regra de diversificação (não concentrar tudo num setor só).
    """
    elegiveis = df[df["status"] == "ELEGÍVEL PARA COMPRA"]
    return elegiveis.drop_duplicates(subset="setor", keep="first").head(quantidade)


# -----------------------------------------------------------------------
# 4) HISTÓRICO DAS SUAS PRÓPRIAS EXECUÇÕES (não depende do Yahoo ter
#    4 anos de balanço — é você construindo sua própria série ao longo
#    do tempo, uma "fotografia" por rodada)
# -----------------------------------------------------------------------

def salvar_snapshot_historico(tabela_final: pd.DataFrame, caminho_arquivo: str = "historico_execucoes.csv"):
    """
    Cada vez que você roda o script, ele ACRESCENTA (não sobrescreve) uma
    fotografia do dia nesse arquivo: data, ticker, cotação, P/L, ROE, preço
    teto e margem de segurança daquele dia. Depois de algumas semanas/meses
    rodando, você tem uma série histórica sua — dá pra ver, por exemplo, se
    a margem de segurança de uma ação está aumentando (ficando mais barata)
    ou diminuindo (ficando mais cara) ao longo do tempo.
    """
    colunas = [
        "data_execucao", "ticker", "nome", "setor", "veredito", "status",
        "cotacao_atual", "pl_preco_lucro", "roe_retorno_sobre_patrimonio",
        "preco_teto", "margem_de_seguranca_pct",
    ]

    snapshot = tabela_final.copy()
    snapshot["data_execucao"] = date.today().isoformat()
    snapshot = snapshot[colunas]

    arquivo_ja_existe = os.path.isfile(caminho_arquivo)
    snapshot.to_csv(
        caminho_arquivo, mode="a", header=not arquivo_ja_existe,
        index=False, encoding="utf-8-sig",
    )
    return caminho_arquivo


# -----------------------------------------------------------------------
# 5) BACKTEST SIMPLES (aprovadas de hoje vs Ibovespa, nos últimos anos)
# -----------------------------------------------------------------------

def rodar_backtest_simples(tickers_aprovados: list, anos: int = 3) -> pd.DataFrame:
    """
    Pergunta simples: "se eu tivesse comprado peso igual em cada ação
    aprovada HOJE, há [anos] anos atrás, qual teria sido o retorno até
    agora — comparado ao Ibovespa no mesmo período?"

    AVISO — isso é uma validação simples, não uma prova de nada:
    - Usa só a variação de preço (não soma os dividendos recebidos no
      caminho, que no método Barsi são boa parte do retorno).
    - Não rebalanceia a carteira nem considera imposto/corretagem.
    - As aprovadas de HOJE não são necessariamente as mesmas que passariam
      no filtro se você tivesse rodado isso há [anos] anos (viés de
      "sabendo o resultado final, escolho quem já deu certo").
    Serve pra ter uma ideia de direção, não um número final de retorno.
    """
    if not tickers_aprovados:
        return pd.DataFrame(columns=["ticker", "retorno_pct"])

    tickers_yahoo = [f"{t}.SA" for t in tickers_aprovados]

    try:
        dados = yf.download(
            tickers_yahoo + ["^BVSP"], period=f"{anos}y",
            progress=False, auto_adjust=True,
        )
        precos_fechamento = dados["Close"]
    except Exception as erro:
        print(f"Não foi possível rodar o backtest: {erro}")
        return pd.DataFrame(columns=["ticker", "retorno_pct"])

    linhas = []
    for ticker, ticker_yahoo in zip(tickers_aprovados, tickers_yahoo):
        if ticker_yahoo not in precos_fechamento.columns:
            continue
        serie = precos_fechamento[ticker_yahoo].dropna()
        if len(serie) < 2:
            continue
        retorno = (serie.iloc[-1] / serie.iloc[0] - 1) * 100
        linhas.append({"ticker": ticker, "retorno_pct": round(retorno, 2)})

    tabela = pd.DataFrame(linhas)

    if "^BVSP" in precos_fechamento.columns:
        serie_ibov = precos_fechamento["^BVSP"].dropna()
        if len(serie_ibov) >= 2:
            retorno_ibov = round((serie_ibov.iloc[-1] / serie_ibov.iloc[0] - 1) * 100, 2)
            tabela = pd.concat([
                tabela,
                pd.DataFrame([{"ticker": "IBOVESPA (referência)", "retorno_pct": retorno_ibov}]),
            ], ignore_index=True)

    return tabela.sort_values("retorno_pct", ascending=False)


# -----------------------------------------------------------------------
# 6) EXECUÇÃO PRINCIPAL
# -----------------------------------------------------------------------

if __name__ == "__main__":
    CAMINHO_CSV_LISTA_APROVADA = "empresas_perenes_holdings_commodities_b3-v2.csv"

    tickers, info_empresa = carregar_lista_aprovada(CAMINHO_CSV_LISTA_APROVADA)
    print(f"{len(tickers)} tickers carregados a partir da lista aprovada.\n")

    tabela_bruta = coletar_todas(tickers, info_empresa)

    print("\nBuscando o histórico das ações de setor cíclico (mineração/siderurgia, petróleo/gás, papel/celulose)...")
    cache_historico = {}
    tabela_bruta, diagnostico_ciclicas = enriquecer_ciclicas_com_media(tabela_bruta, cache_historico)

    if not diagnostico_ciclicas.empty:
        nome_arquivo_diagnostico = "diagnostico_ciclicas.csv"
        diagnostico_ciclicas.to_csv(nome_arquivo_diagnostico, index=False, encoding="utf-8-sig")
        print(f"Diagnóstico ano a ano das cíclicas (valores brutos: lucro, EPS, patrimônio, dívida, EBITDA) salvo como: {nome_arquivo_diagnostico}")

    tabela_final = calcular_valuation(tabela_bruta)

    # Reordena as colunas: ticker primeiro, resultado da aprovação e a
    # pontuação final logo em seguida — o resto dos dados vem depois.
    colunas_prioritarias = [
        "ticker", "nome", "setor", "veredito", "status", "pontuacao_final",
        "preco_teto", "cotacao_atual", "margem_de_seguranca_pct", "motivo_descarte",
    ]
    demais_colunas = [c for c in tabela_final.columns if c not in colunas_prioritarias]
    tabela_final = tabela_final[colunas_prioritarias + demais_colunas]

    nome_arquivo = "ranking_valuation_b3.csv"
    tabela_final.to_csv(nome_arquivo, index=False, encoding="utf-8-sig")

    colunas_resumo = [
        "ticker", "nome", "setor", "veredito", "status", "pontuacao_final",
        "preco_teto", "cotacao_atual", "margem_de_seguranca_pct", "dividendo_ajustado_setor_financeiro",
        "dividendo_projetado_usado", "usou_media_ciclica",
        "pl_usado", "pl_inconsistente", "pvp_preco_valor_patrimonial", "numero_graham", "peg_ratio",
        "classificacao_crescimento",
    ]
    print("\n=== RANKING COMPLETO (maior para menor pontuação final) ===")
    print(tabela_final[colunas_resumo].to_string(index=False))

    QUANTIDADE_TOP = 20
    top10 = tabela_final[tabela_final["status"] == "ELEGÍVEL PARA COMPRA"].head(QUANTIDADE_TOP)
    nome_arquivo_top10 = "top20_recomendadas.csv"
    top10.to_csv(nome_arquivo_top10, index=False, encoding="utf-8-sig")
    print(f"\n=== TOP {len(top10)} RECOMENDADAS (nota final combina margem de segurança 40%, ROE 25%, crescimento 20%, dívida 15%) ===")
    print(top10[colunas_resumo].to_string(index=False))
    print(f"\nTop {QUANTIDADE_TOP} salvo como: {nome_arquivo_top10}")

    print(f"\n=== DESTAQUES DIVERSIFICADOS (melhor de cada setor, dentro do Top {QUANTIDADE_TOP}) ===")
    print(destaques_diversificados(top10)[colunas_resumo].to_string(index=False))

    descartadas = tabela_final[tabela_final["veredito"] == "Descartada"]
    print(f"\n=== POR QUE {len(descartadas)} AÇÕES FORAM DESCARTADAS DE VERDADE ===")
    print(descartadas[["ticker", "motivo_descarte"]].to_string(index=False))

    zona_cinzenta = tabela_final[tabela_final["veredito"].isin(
        ["Quase Aprovada (revisar)", "Revisar manualmente (dado insuficiente)"]
    )]
    if len(zona_cinzenta) > 0:
        print(f"\n=== {len(zona_cinzenta)} AÇÕES NA ZONA CINZENTA (perto do limite ou com dado faltando — vale revisar antes de descartar) ===")
        print(zona_cinzenta[["ticker", "nome", "veredito", "motivo_descarte", "margem_de_seguranca_pct"]].to_string(index=False))

    sem_dados = tabela_final[tabela_final["status_coleta"] != "ok"]
    if len(sem_dados) > 0:
        print(f"\n=== ATENÇÃO: {len(sem_dados)} AÇÕES SEM DADOS DO YAHOO (não é reprovação, é falha na busca) ===")
        print(sem_dados[["ticker", "status_coleta"]].to_string(index=False))

    print("\nBuscando o histórico (P/L, ROE, Dívida Líq./EBITDA e dividendos) das aprovadas...")
    historico = montar_historico_das_aprovadas(tabela_final, cache_historico)
    nome_arquivo_historico = "historico_indicadores_aprovadas.csv"
    historico.to_csv(nome_arquivo_historico, index=False, encoding="utf-8-sig")

    print("\n=== EVOLUÇÃO ANO A ANO DAS APROVADAS (indicadores + dividendos/payout) ===")
    print(historico.to_string(index=False))
    print(f"\nHistórico salvo como: {nome_arquivo_historico}")

    arquivo_snapshot = salvar_snapshot_historico(tabela_final)
    print(f"\nFotografia de hoje acrescentada a: {arquivo_snapshot} (a cada rodada, isso cresce e vira uma série histórica sua)")

    tickers_aprovados = tabela_final[tabela_final["veredito"] != "Descartada"]["ticker"].tolist()
    print("\nRodando backtest simples (últimos 3 anos) das aprovadas vs Ibovespa...")
    resultado_backtest = rodar_backtest_simples(tickers_aprovados, anos=3)
    if not resultado_backtest.empty:
        nome_arquivo_backtest = "backtest_simples.csv"
        resultado_backtest.to_csv(nome_arquivo_backtest, index=False, encoding="utf-8-sig")
        print("\n=== BACKTEST SIMPLES (retorno acumulado nos últimos 3 anos, só variação de preço) ===")
        print(resultado_backtest.to_string(index=False))
        print(f"\nBacktest salvo como: {nome_arquivo_backtest}")

    print(f"\nArquivo completo salvo como: {nome_arquivo}")
    ##