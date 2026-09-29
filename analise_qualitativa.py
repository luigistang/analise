"""
Análise qualitativa simples das ações aprovadas: busca as notícias mais
recentes de cada uma no Yahoo Finance e sinaliza, por palavra-chave, se o
tom das manchetes parece positivo, negativo ou neutro.

IMPORTANTE — o que isso NÃO é:
- Isso NÃO é uma IA lendo e interpretando a notícia de verdade. É uma
  contagem simples de palavras-chave dentro dos TÍTULOS das notícias.
  "Empresa evita prejuízo" seria marcada como negativa (por causa da
  palavra "prejuízo"), mesmo sendo uma notícia boa — o script não entende
  o contexto da frase, só procura a palavra.
- NÃO substitui você lendo a notícia. Serve só pra apontar rapidamente
  quais ações têm manchete recente que merece uma olhada mais de perto
  antes de decidir — o julgamento final continua sendo seu.

COMO USAR:
  1) Rode primeiro o b3_indices.py (ele gera o top20_recomendadas.csv).
  2) Rode este arquivo: python analise_qualitativa.py
  3) Confira noticias_aprovadas.csv (detalhe) e
     resumo_qualitativo_aprovadas.csv (uma linha por ação).
"""

import time
import pandas as pd
import yfinance as yf

PALAVRAS_NEGATIVAS = [
    "prejuízo", "queda", "investigação", "processo", "multa", "corte",
    "demissão", "fraude", "recall", "greve", "rebaixamento", "colapso",
    "crise", "dívida elevada", "calote",
    "loss", "lawsuit", "investigation", "fraud", "downgrade", "strike",
    "decline", "layoff", "plunge", "probe", "recall", "scandal",
]

PALAVRAS_POSITIVAS = [
    "recorde", "aprovação", "aumento de lucro", "lucro recorde", "alta",
    "expansão", "acordo", "dividendo extra", "crescimento", "avanço",
    "recomendação de compra", "valorização",
    "record", "profit", "growth", "approval", "expansion", "deal",
    "upgrade", "surge", "rally", "buyback","Evita prejuízo",
]


def classificar_titulo(titulo: str) -> str:
    """
    Não entende a frase — só procura se alguma palavra da lista aparece
    no título. Ver aviso no topo do arquivo sobre a limitação disso.
    """
    if not titulo:
        return "Sem título"
    titulo_lower = titulo.lower()
    tem_negativa = any(p in titulo_lower for p in PALAVRAS_NEGATIVAS)
    tem_positiva = any(p in titulo_lower for p in PALAVRAS_POSITIVAS)
    if tem_negativa and tem_positiva:
        return "Misto"
    if tem_negativa:
        return "Possível sinal negativo"
    if tem_positiva:
        return "Possível sinal positivo"
    return "Neutro"


def extrair_campos_noticia(item: dict) -> dict:
    """
    O formato que o yfinance devolve em `.news` mudou entre versões da
    biblioteca — essa função tenta os dois formatos conhecidos (o antigo,
    plano, e o novo, aninhado dentro de "content"), pra funcionar nos dois.
    """
    conteudo = item.get("content", item)

    titulo = conteudo.get("title")
    link = (
        conteudo.get("link")
        or (conteudo.get("canonicalUrl") or {}).get("url")
        or (conteudo.get("clickThroughUrl") or {}).get("url")
    )
    publicador = (
        conteudo.get("publisher")
        or (conteudo.get("provider") or {}).get("displayName")
    )
    data_publicacao = conteudo.get("pubDate") or item.get("providerPublishTime")

    return {"titulo": titulo, "link": link, "publicador": publicador, "data": data_publicacao}


def buscar_noticias(ticker: str, quantidade: int = 5) -> pd.DataFrame:
    """Busca até 'quantidade' notícias recentes de uma ação no Yahoo Finance."""
    ticker_yahoo = f"{ticker}.SA"
    try:
        noticias = yf.Ticker(ticker_yahoo).news or []
    except Exception as erro:
        print(f"  Não deu pra buscar notícias de {ticker}: {erro}")
        noticias = []

    linhas = []
    for item in noticias[:quantidade]:
        campos = extrair_campos_noticia(item)
        campos["ticker"] = ticker
        campos["classificacao_titulo"] = classificar_titulo(campos.get("titulo"))
        linhas.append(campos)

    if not linhas:
        return pd.DataFrame(columns=["ticker", "titulo", "classificacao_titulo", "publicador", "data", "link"])

    return pd.DataFrame(linhas)[["ticker", "titulo", "classificacao_titulo", "publicador", "data", "link"]]


def montar_analise_qualitativa(caminho_csv_aprovadas: str, quantidade_noticias: int = 5,
                                pausa_segundos: float = 2.0) -> pd.DataFrame:
    """Roda buscar_noticias pra cada ação aprovada, com pausa entre uma e outra."""
    aprovadas = pd.read_csv(caminho_csv_aprovadas)
    tickers = aprovadas["ticker"].tolist()

    todas_noticias = []
    for i, ticker in enumerate(tickers, start=1):
        print(f"[{i}/{len(tickers)}] Buscando notícias de {ticker}...")
        todas_noticias.append(buscar_noticias(ticker, quantidade_noticias))
        time.sleep(pausa_segundos)

    resultado = pd.concat(todas_noticias, ignore_index=True) if todas_noticias else pd.DataFrame()
    return resultado


def resumo_por_acao(noticias: pd.DataFrame) -> pd.DataFrame:
    """
    Uma linha por ação, contando quantas manchetes vieram com sinal
    positivo/negativo — visão rápida antes de entrar no detalhe.
    """
    colunas_finais = ["ticker", "total_noticias", "sinais_positivos", "sinais_negativos", "sinais_mistos"]
    if noticias.empty:
        return pd.DataFrame(columns=colunas_finais)

    contagem = noticias.groupby("ticker")["classificacao_titulo"].value_counts().unstack(fill_value=0)
    contagem["total_noticias"] = contagem.sum(axis=1)

    for coluna in ["Possível sinal positivo", "Possível sinal negativo", "Misto"]:
        if coluna not in contagem.columns:
            contagem[coluna] = 0

    contagem = contagem.rename(columns={
        "Possível sinal positivo": "sinais_positivos",
        "Possível sinal negativo": "sinais_negativos",
        "Misto": "sinais_mistos",
    }).reset_index()

    return contagem[colunas_finais]


if __name__ == "__main__":
    CAMINHO_APROVADAS = "top20_recomendadas.csv"

    noticias = montar_analise_qualitativa(CAMINHO_APROVADAS)
    nome_arquivo_noticias = "noticias_aprovadas.csv"
    noticias.to_csv(nome_arquivo_noticias, index=False, encoding="utf-8-sig")

    resumo = resumo_por_acao(noticias)
    nome_arquivo_resumo = "resumo_qualitativo_aprovadas.csv"
    resumo.to_csv(nome_arquivo_resumo, index=False, encoding="utf-8-sig")

    print("\n=== RESUMO QUALITATIVO (por palavra-chave nas manchetes) ===")
    print(resumo.to_string(index=False))
    print(f"\nDetalhe de cada notícia salvo em: {nome_arquivo_noticias}")
    print(f"Resumo por ação salvo em: {nome_arquivo_resumo}")
