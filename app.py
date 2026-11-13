"""
Screener de Valuation ao vivo (B3 e EUA).
Digite um ticker (ou suba um CSV com uma coluna "ticker") e o app busca os
dados no Yahoo Finance na hora e aplica o mesmo método dos scripts
b3_indices.py e us_indices.py.

Rodar no seu computador:  streamlit run app.py
"""
import io
import time

import pandas as pd
import streamlit as st
import yfinance as yf

import b3_indices as b3
import us_indices as us

st.set_page_config(page_title="Screener de Valuation", page_icon="📈", layout="wide")

# ----------------------------------------------------------------------
# Tradução do setor do Yahoo para os nomes usados nos scripts
# ----------------------------------------------------------------------
SETOR_EUA = {
    "Financial Services": "Financials",
    "Consumer Defensive": "Consumer Staples",
    "Consumer Cyclical": "Consumer Discretionary",
}


def setor_b3(setor_yahoo, industria):
    s, i = (setor_yahoo or ""), (industria or "").lower()
    if s == "Financial Services":
        return "Financeiro"
    if s == "Energy" and ("oil" in i or "gas" in i):
        return "Petróleo, Gás e Combustíveis"
    if s == "Basic Materials":
        if "paper" in i or "lumber" in i:
            return "Papel e Celulose"
        if "steel" in i or "mining" in i or "metal" in i or "coal" in i:
            return "Mineração e Siderurgia"
    if s == "Utilities":
        return "Energia Elétrica"
    return s or "Outros"


# ----------------------------------------------------------------------
# Análise de UM ativo (cada ativo é guardado em cache por 1 hora)
# ----------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def analisar_eua(ticker: str) -> dict:
    t = us.normalizar_ticker_yahoo(ticker)
    try:
        info = yf.Ticker(t).info or {}
    except Exception as e:
        return {"ticker": ticker, "erro": f"Yahoo não respondeu ({e})"}
    if not (info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")):
        return {"ticker": ticker, "erro": "Ticker não encontrado no Yahoo."}
    tipo = "ETF" if info.get("quoteType") == "ETF" else "Ação"
    setor = SETOR_EUA.get(info.get("sector"), info.get("sector")) or ("ETF" if tipo == "ETF" else "Outros")
    cadastro = {ticker: {"nome": info.get("longName") or info.get("shortName") or ticker, "setor": setor, "tipo": tipo}}

    bruta = us.coletar_todos([ticker], cadastro, pausa_segundos=0.5, tentativas=2)
    if tipo == "ETF":
        d = us.montar_diagnostico_etfs(bruta, pausa_segundos=0)
        return {"tipo": "ETF", **d.iloc[0].to_dict()}
    bruta, _ = us.enriquecer_ciclicas_com_media(bruta, pausa_segundos=0.5)
    r = us.calcular_valuation_acoes(bruta)
    return {"tipo": "Ação", **r.iloc[0].to_dict()}


@st.cache_data(ttl=3600, show_spinner=False)
def analisar_b3(ticker: str) -> dict:
    base = ticker.upper().replace(".SA", "")
    try:
        info = yf.Ticker(f"{base}.SA").info or {}
    except Exception as e:
        return {"ticker": base, "erro": f"Yahoo não respondeu ({e})"}
    if not (info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")):
        return {"ticker": base, "erro": "Ticker não encontrado no Yahoo."}
    cadastro = {base: {
        "nome": info.get("longName") or info.get("shortName") or base,
        "setor": setor_b3(info.get("sector"), info.get("industry")),
        "subsetor": info.get("industry"),
    }}
    bruta = b3.coletar_todas([base], cadastro, pausa_segundos=0.5, tentativas=2)
    bruta, _ = b3.enriquecer_ciclicas_com_media(bruta, {}, pausa_segundos=0.5)
    r = b3.calcular_valuation(bruta)
    return {"tipo": "Ação", **r.iloc[0].to_dict()}


# ----------------------------------------------------------------------
# Tela
# ----------------------------------------------------------------------
def n(v, casas=2, pct=False):
    try:
        if v is None or pd.isna(v):
            return "—"
        v = float(v) * (100 if pct else 1)
        return f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".") + ("%" if pct else "")
    except Exception:
        return "—"


COR = {"Aprovada": "green", "ETF": "green", "Quase": "orange", "Revisar": "orange", "Descartada": "red", "Reprovado": "red"}


def cor(veredito):
    for k, c in COR.items():
        if str(veredito).startswith(k):
            return c
    return "gray"


def mostrar(r: dict, mercado: str):
    if r.get("erro"):
        st.error(f"**{r['ticker']}**: {r['erro']}")
        return
    moeda = "R$" if mercado == "B3" else "US$"
    with st.container(border=True):
        if r["tipo"] == "ETF":
            v = r.get("veredito_etf")
            st.subheader(f"{r['ticker']} · :{cor(v)}[{v}]")
            st.caption(r.get("nome", ""))
            c = st.columns(4)
            c[0].metric("Cotação", f"{moeda} {n(r.get('cotacao_atual'))}")
            c[1].metric("Taxa de administração", n(r.get("taxa_administracao"), 2, pct=True))
            pat = r.get("patrimonio_sob_gestao")
            c[2].metric("Patrimônio", "—" if pat is None or pd.isna(pat) else f"US$ {pat/1e9:,.1f} bi")
            c[3].metric("Crescimento dos dividendos (5 anos)", n(r.get("cagr_dividendos_5anos"), 1, pct=True))
            if r.get("motivo_etf"):
                st.write(f"**Motivo:** {r['motivo_etf']}")
            return
        v = r.get("veredito")
        st.subheader(f"{r['ticker']} · :{cor(v)}[{v}]")
        st.caption(f"{r.get('nome','')} · {r.get('setor','')}")
        c = st.columns(4)
        c[0].metric("Preço-teto", f"{moeda} {n(r.get('preco_teto'))}")
        c[1].metric("Cotação", f"{moeda} {n(r.get('cotacao_atual'))}")
        c[2].metric("Margem de segurança", n(r.get("margem_de_seguranca_pct"), 1) + "%" if pd.notna(r.get("margem_de_seguranca_pct")) else "—")
        c[3].metric("Nota final (0 a 100)", n(r.get("pontuacao_final"), 1))
        st.write(f"**Status:** {r.get('status','—')}")
        if r.get("motivo_descarte"):
            st.write(f"**Motivo:** {r['motivo_descarte']}")
        with st.expander("Indicadores usados"):
            if mercado == "B3":
                itens = [("ROE", n(r.get("roe_usado"), 1, pct=True)), ("P/L", n(r.get("pl_usado"), 1)),
                         ("Dív. líq./EBITDA", n(r.get("divida_liquida_ebitda_usada"), 1)),
                         ("Número de Graham", n(r.get("numero_graham"), 1)), ("PEG", n(r.get("peg_ratio"), 2)),
                         ("Crescimento do lucro (CAGR)", n(r.get("cagr_lucro"), 1, pct=True))]
            else:
                itens = [("ROE", n(r.get("roe_usado"), 1, pct=True)), ("ROIC", n(r.get("roic"), 1, pct=True)),
                         ("Dív. líq./EBITDA", n(r.get("divida_liquida_ebitda_usada"), 1)),
                         ("FCF Yield", n(r.get("fcf_yield"), 1, pct=True)), ("Payout", n(r.get("payout_ratio_yahoo"), 0, pct=True)),
                         ("Shareholder Yield", n(r.get("shareholder_yield"), 1, pct=True))]
            cols = st.columns(3)
            for i, (k, val) in enumerate(itens):
                cols[i % 3].metric(k, val)


aba_ao_vivo, aba_acomp = st.tabs(["Consultar ao vivo", "Acompanhar mudanças"])

with aba_ao_vivo:
    st.title("📈 Screener de Valuation")
    st.write("Digite o ticker e veja o resultado na hora. Os dados vêm do Yahoo Finance.")

    mercado = st.radio("Mercado", ["B3", "EUA"], horizontal=True)
    exemplo = "BBSE3, KLBN4, VALE3" if mercado == "B3" else "AAPL, KO, SCHD"
    texto = st.text_input("Ticker (pode colocar vários, separados por vírgula)", placeholder=f"Ex.: {exemplo}")
    arquivo = st.file_uploader("Ou suba um CSV com uma coluna chamada ticker", type=["csv"])

    tickers = [t.strip().upper() for t in texto.replace(";", ",").split(",") if t.strip()]
    if arquivo is not None:
        try:
            df = pd.read_csv(arquivo, encoding="utf-8-sig", sep=None, engine="python")
            col = next((c for c in df.columns if str(c).strip().lower() in ("ticker", "símbolo", "simbolo", "symbol")), df.columns[0])
            for cel in df[col].dropna().astype(str):
                tickers += [t.strip().upper() for t in cel.split(",") if t.strip()]
        except Exception:
            st.error("Não consegui ler o CSV. Confira se ele tem uma coluna chamada ticker.")
    tickers = list(dict.fromkeys(tickers))

    LIMITE = 40
    if len(tickers) > LIMITE:
        st.warning(f"Para não sobrecarregar o Yahoo, analisei só os primeiros {LIMITE} tickers.")
        tickers = tickers[:LIMITE]

    if st.button("Analisar", type="primary", disabled=not tickers):
        resultados = []
        barra = st.progress(0.0, text="Buscando dados...")
        for i, t in enumerate(tickers, start=1):
            barra.progress((i - 1) / len(tickers), text=f"Buscando {t} ({i} de {len(tickers)})...")
            resultados.append(analisar_b3(t) if mercado == "B3" else analisar_eua(t))
            time.sleep(0.3)
        barra.empty()
        st.session_state["resultados"] = (mercado, resultados)

    if "resultados" in st.session_state:
        mercado_res, resultados = st.session_state["resultados"]
        ok = [r for r in resultados if not r.get("erro")]
        for r in resultados:
            mostrar(r, mercado_res)
        if len(ok) > 1:
            tabela = pd.DataFrame(ok)
            st.download_button("Baixar resultado em CSV", tabela.to_csv(index=False).encode("utf-8-sig"),
                               file_name="resultado_screener.csv", mime="text/csv")

    st.divider()
    st.caption("Não é recomendação de investimento. Dados gratuitos do Yahoo Finance podem estar errados ou atrasados. "
               "Dividendos de ações americanas pagos a brasileiros têm retenção de 30% nos EUA, não descontada aqui.")


# ----------------------------------------------------------------------
# Aba "Acompanhar mudanças": compara as duas últimas atualizações diárias
# ----------------------------------------------------------------------
ARQUIVOS_HISTORICO = {"B3": "historico_execucoes.csv", "EUA": "historico_execucoes_usa.csv"}


@st.cache_data(ttl=900, show_spinner=False)
def ler_historico(caminho: str):
    try:
        h = pd.read_csv(caminho, encoding="utf-8-sig")
    except Exception:
        return None
    h["data_execucao"] = pd.to_datetime(h["data_execucao"], errors="coerce")
    h = h.dropna(subset=["data_execucao"]).drop_duplicates(["data_execucao", "ticker"], keep="last")
    return h.sort_values(["ticker", "data_execucao"])


def comparar(h: pd.DataFrame):
    datas = sorted(h["data_execucao"].unique())
    atual = h[h["data_execucao"] == datas[-1]].set_index("ticker")
    ant = h[h["data_execucao"] == datas[-2]].set_index("ticker")
    c = atual.join(ant, how="left", lsuffix="", rsuffix="_ant")
    c["variacao_pct"] = (c["cotacao_atual"] / c["cotacao_atual_ant"] - 1) * 100
    c["margem_delta"] = c["margem_de_seguranca_pct"] - c["margem_de_seguranca_pct_ant"]
    c["mudou_veredito"] = c["veredito"].ne(c["veredito_ant"]) & c["veredito_ant"].notna()
    return pd.Timestamp(datas[-2]), pd.Timestamp(datas[-1]), c


with aba_acomp:
    st.write("Acompanhe o que mudou entre uma atualização e outra. Os dados são atualizados uma vez por dia útil, depois do fechamento, e **não são em tempo real**.")
    mer = st.radio("Mercado", ["B3", "EUA"], horizontal=True, key="mer_acomp")
    h = ler_historico(ARQUIVOS_HISTORICO[mer])
    if h is None or h["data_execucao"].nunique() < 1:
        st.info("Ainda não há atualizações salvas para esse mercado.")
    else:
        ultima = h["data_execucao"].max()
        st.caption(f"Última atualização: {ultima:%d/%m/%Y}. Atualizações guardadas: {h['data_execucao'].nunique()}.")
        if h["data_execucao"].nunique() < 2:
            st.info("Só existe uma atualização até agora. As comparações aparecem a partir da segunda.")
        else:
            d0, d1, c = comparar(h)
            st.write(f"Comparando **{d0:%d/%m}** com **{d1:%d/%m}**.")
            ent = c[(c["status"] == "ELEGÍVEL PARA COMPRA") & (c["status_ant"] != "ELEGÍVEL PARA COMPRA")]
            sai = c[(c["status"] != "ELEGÍVEL PARA COMPRA") & (c["status_ant"] == "ELEGÍVEL PARA COMPRA")]
            m = st.columns(3)
            m[0].metric("Entraram em Elegível para compra", len(ent))
            m[1].metric("Saíram de Elegível para compra", len(sai))
            m[2].metric("Mudaram de veredito", int(c["mudou_veredito"].sum()))
            cols = ["nome", "veredito", "status", "cotacao_atual", "variacao_pct", "margem_de_seguranca_pct", "margem_delta"]
            fmt = {"cotacao_atual": "{:.2f}", "variacao_pct": "{:+.1f}%", "margem_de_seguranca_pct": "{:.1f}%", "margem_delta": "{:+.1f}"}
            nomes = {"nome": "Empresa", "veredito": "Veredito", "status": "Status", "cotacao_atual": "Cotação",
                     "variacao_pct": "Variação", "margem_de_seguranca_pct": "Margem", "margem_delta": "Mudança na margem"}
            def tabela(df):
                st.dataframe(df[cols].rename(columns=nomes).style.format({nomes[k]: v for k, v in fmt.items()}, na_rep="—"),
                             width="stretch")
            if len(ent):
                st.subheader("Entraram em Elegível para compra"); tabela(ent)
            if len(sai):
                st.subheader("Saíram de Elegível para compra"); tabela(sai)
            mud = c[c["mudou_veredito"]]
            if len(mud):
                st.subheader("Mudaram de veredito")
                st.dataframe(mud[["nome", "veredito_ant", "veredito"]].rename(columns={"nome": "Empresa", "veredito_ant": "Antes", "veredito": "Agora"}), width="stretch")
            st.subheader("Maiores altas e quedas de preço")
            mv = c.dropna(subset=["variacao_pct"]).sort_values("variacao_pct")
            col_a, col_b = st.columns(2)
            with col_a:
                st.caption("Maiores altas"); tabela(mv.tail(5).iloc[::-1])
            with col_b:
                st.caption("Maiores quedas"); tabela(mv.head(5))

        st.subheader("Histórico de um ativo")
        t = st.selectbox("Ativo", sorted(h["ticker"].unique()), key="ativo_acomp")
        serie = h[h["ticker"] == t].set_index("data_execucao")
        st.line_chart(serie[["cotacao_atual", "preco_teto"]].rename(columns={"cotacao_atual": "Cotação", "preco_teto": "Preço-teto"}))
        st.caption("Dados do Yahoo Finance, com defasagem. Não é recomendação de investimento.")
