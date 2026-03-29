import os
import csv
import math
import time
import json
import threading
from datetime import datetime
from collections import deque

import requests
import matplotlib.pyplot as plt
from binance.client import Client
from binance.exceptions import BinanceAPIException
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================================================
# CONFIG
# =========================================================

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
TOKEN = os.getenv("TOKEN")
CHAT_ID = str(os.getenv("CHAT_ID", ""))

SYMBOL = "BTCBRL"
STATE_FILE = "estado_bot.json"
TRADES_FILE = "historico_trades.csv"
PRICE_GRAPH_FILE = "grafico_preco.png"
PNL_GRAPH_FILE = "grafico_pnl.png"

POLL_SECONDS = 5

client = Client(API_KEY, API_SECRET)
lock = threading.RLock()

# =========================================================
# ESTADO
# =========================================================

estado = {
    "modo_execucao": "SIMULADO",       # SIMULADO | REAL
    "auto": False,
    "estrategia": "REVERSAO",          # SCALPING | REVERSAO | TENDENCIA | CUSTOM
    "queda": 1.0,                      # gatilho de queda (%)
    "reversao": 0.4,                   # confirmação de reversão (%)
    "take": 1.3,                       # take fixo (%)
    "stop": 1.0,                       # stop fixo (%)
    "take_auto": True,                 # take dinâmico ligado?
    "usar_filtro_tendencia": True,
    "valor_auto": 200.0,               # valor fixo por ordem no auto
    "max_posicoes": 3,                 # quantas posições simultâneas
    "cooldown": 60,                    # segundos entre compras
    "referencia": None,
    "ultima_minima": None,
    "aguardando_reversao": False,
    "ultimo_trade_ts": 0.0,
    "lucro_total_realizado": 0.0,
    "vendas_realizadas": 0,
    "ultimo_evento": "Iniciado",
    "posicoes": [],                    # lista de posições abertas
}

recent_prices = deque(maxlen=200)      # [(timestamp, price), ...]
price_samples = deque(maxlen=60)       # só preços, pra volatilidade
short_ma = deque(maxlen=9)
long_ma = deque(maxlen=21)

# =========================================================
# PERSISTÊNCIA
# =========================================================

def salvar_estado():
    with lock:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)


def carregar_estado():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        with lock:
            estado.update(data)
    except Exception as e:
        print("Erro ao carregar estado:", e)


def garantir_csv():
    if os.path.exists(TRADES_FILE):
        return
    with open(TRADES_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "data",
            "tipo",
            "modo",
            "estrategia",
            "entrada",
            "saida",
            "quantidade",
            "investido",
            "valor_final",
            "lucro_reais",
            "lucro_pct",
            "motivo",
        ])


def registrar_trade(
    tipo: str,
    modo: str,
    estrategia: str,
    entrada: float,
    saida: float,
    quantidade: float,
    investido: float,
    valor_final: float,
    lucro_reais: float,
    lucro_pct: float,
    motivo: str,
):
    garantir_csv()
    with open(TRADES_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            tipo,
            modo,
            estrategia,
            entrada,
            saida,
            quantidade,
            investido,
            valor_final,
            lucro_reais,
            lucro_pct,
            motivo,
        ])

# =========================================================
# UTILS
# =========================================================

def autorizado(update):
    return str(update.effective_chat.id) == CHAT_ID

def send_text(msg: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg},
            timeout=20,
        )
    except Exception as e:
        print("Erro Telegram:", e)


def now_str() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")


def preco() -> float:
    ticker = client.get_symbol_ticker(symbol=SYMBOL)
    return float(ticker["price"])


def fmt_brl(v: float) -> str:
    return f"R${v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_pct(v: float) -> str:
    return f"{v:.2f}%"


def fmt_qty(v: float) -> str:
    s = f"{v:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


def update_price_buffers(p: float):
    ts = time.time()
    recent_prices.append((ts, p))
    price_samples.append(p)
    short_ma.append(p)
    long_ma.append(p)


def current_short_ma():
    if not short_ma:
        return None
    return sum(short_ma) / len(short_ma)


def current_long_ma():
    if not long_ma:
        return None
    return sum(long_ma) / len(long_ma)


def tendencia_ok() -> bool:
    if not estado["usar_filtro_tendencia"]:
        return True
    m_short = current_short_ma()
    m_long = current_long_ma()
    if m_short is None or m_long is None or len(long_ma) < 10:
        return True
    return m_short >= m_long


def volatilidade_pct() -> float:
    if len(price_samples) < 5:
        return 1.0
    hi = max(price_samples)
    lo = min(price_samples)
    if lo <= 0:
        return 1.0
    return ((hi - lo) / lo) * 100.0


def calc_take_dinamico() -> float:
    if not estado["take_auto"]:
        return float(estado["take"])

    vol = volatilidade_pct()
    # simples e controlado
    take = 1.0 + vol
    if take < 1.0:
        take = 1.0
    if take > 2.4:
        take = 2.4
    return round(take, 2)


# =========================================================
# FILTROS BINANCE
# =========================================================

_symbol_info_cache = None

def get_symbol_info():
    global _symbol_info_cache
    if _symbol_info_cache is None:
        info = client.get_symbol_info(SYMBOL)
        if not info:
            raise ValueError(f"Símbolo inválido: {SYMBOL}")
        _symbol_info_cache = info
    return _symbol_info_cache


def get_filters():
    info = get_symbol_info()
    lot_size = None
    market_lot_size = None
    min_notional = None
    notional = None

    for f in info["filters"]:
        ft = f["filterType"]
        if ft == "LOT_SIZE":
            lot_size = f
        elif ft == "MARKET_LOT_SIZE":
            market_lot_size = f
        elif ft == "MIN_NOTIONAL":
            min_notional = f
        elif ft == "NOTIONAL":
            notional = f

    lot = market_lot_size or lot_size
    if not lot:
        raise ValueError("Filtro LOT_SIZE não encontrado")

    step_size = float(lot["stepSize"])
    min_qty = float(lot["minQty"])
    max_qty = float(lot["maxQty"])

    min_notional_value = None
    if notional and "minNotional" in notional:
        min_notional_value = float(notional["minNotional"])
    elif min_notional and "minNotional" in min_notional:
        min_notional_value = float(min_notional["minNotional"])

    return {
        "step_size": step_size,
        "min_qty": min_qty,
        "max_qty": max_qty,
        "min_notional": min_notional_value,
    }


def adjust_qty(qty: float, step_size: float) -> float:
    adjusted = math.floor(qty / step_size) * step_size
    if step_size >= 1:
        return float(int(adjusted))
    decimals = max(0, int(round(-math.log10(step_size), 0)))
    return float(f"{adjusted:.{decimals}f}")


def calc_qtd(valor_brl: float, p: float) -> float:
    filters = get_filters()
    qty_raw = valor_brl / p
    qty = adjust_qty(qty_raw, filters["step_size"])

    if qty < filters["min_qty"]:
        raise ValueError(f"Qtd abaixo do mínimo do par ({filters['min_qty']})")
    if qty > filters["max_qty"]:
        raise ValueError(f"Qtd acima do máximo do par ({filters['max_qty']})")

    notional = qty * p
    if filters["min_notional"] is not None and notional < filters["min_notional"]:
        raise ValueError(
            f"Valor da ordem {fmt_brl(notional)} abaixo do mínimo {fmt_brl(filters['min_notional'])}"
        )

    return qty

# =========================================================
# ORDENS
# =========================================================

def ordem_buy_market(qty: float):
    if estado["modo_execucao"] == "REAL":
        return client.create_order(symbol=SYMBOL, side="BUY", type="MARKET", quantity=qty)
    return client.create_test_order(symbol=SYMBOL, side="BUY", type="MARKET", quantity=qty)


def ordem_sell_market(qty: float):
    if estado["modo_execucao"] == "REAL":
        return client.create_order(symbol=SYMBOL, side="SELL", type="MARKET", quantity=qty)
    return client.create_test_order(symbol=SYMBOL, side="SELL", type="MARKET", quantity=qty)

# =========================================================
# ESTRATÉGIAS
# =========================================================

def estrategia_params():
    base = dict(
        queda=float(estado["queda"]),
        reversao=float(estado["reversao"]),
        exigir_tendencia=bool(estado["usar_filtro_tendencia"]),
    )

    est = estado["estrategia"]

    if est == "SCALPING":
        base["queda"] = 0.9
        base["reversao"] = 0.3
        base["exigir_tendencia"] = False
    elif est == "REVERSAO":
        base["queda"] = 1.2
        base["reversao"] = 0.4
        base["exigir_tendencia"] = True
    elif est == "TENDENCIA":
        base["queda"] = 1.0
        base["reversao"] = 0.6
        base["exigir_tendencia"] = True
    # CUSTOM usa o que está no estado

    return base


def should_open_position(p: float) -> tuple[bool, str]:
    params = estrategia_params()

    with lock:
        ref = estado["referencia"]
        ultima_minima = estado["ultima_minima"]
        aguardando = estado["aguardando_reversao"]

    if ref is None:
        with lock:
            estado["referencia"] = p
            estado["ultimo_evento"] = f"Referência iniciada em {fmt_brl(p)}"
            salvar_estado()
        return False, "Sem referência"

    if p > ref and not aguardando:
        with lock:
            estado["referencia"] = p
            estado["ultimo_evento"] = f"Nova referência em {fmt_brl(p)}"
            salvar_estado()
        return False, "Atualizando topo"

    queda_atual = ((ref - p) / ref) * 100

    if queda_atual >= params["queda"]:
        with lock:
            estado["aguardando_reversao"] = True
            if estado["ultima_minima"] is None or p < estado["ultima_minima"]:
                estado["ultima_minima"] = p
            salvar_estado()

    with lock:
        ultima_minima = estado["ultima_minima"]
        aguardando = estado["aguardando_reversao"]

    if not aguardando or ultima_minima is None:
        return False, "Ainda sem reversão"

    subida = ((p - ultima_minima) / ultima_minima) * 100

    if subida < params["reversao"]:
        return False, f"Subida insuficiente ({subida:.2f}%)"

    if params["exigir_tendencia"] and not tendencia_ok():
        return False, "Filtro de tendência bloqueou"

    return True, "Entrada confirmada"


# =========================================================
# COMPRAS / VENDAS
# =========================================================

def abrir_posicao_auto():
    p = preco()
    ok, motivo = should_open_position(p)
    if not ok:
        return

    with lock:
        if len(estado["posicoes"]) >= int(estado["max_posicoes"]):
            return

        if time.time() - float(estado["ultimo_trade_ts"]) < int(estado["cooldown"]):
            return

        valor = float(estado["valor_auto"])

    qty = calc_qtd(valor, p)
    ordem_buy_market(qty)

    take_usado = calc_take_dinamico()

    pos = {
        "id": int(time.time() * 1000),
        "tipo": "AUTO",
        "entrada": p,
        "qtd": qty,
        "investido": valor,
        "alvo": p * (1 + take_usado / 100),
        "stop_preco": p * (1 - float(estado["stop"]) / 100),
        "aberta_em": now_str(),
        "take_usado": take_usado,
        "estrategia": estado["estrategia"],
    }

    with lock:
        estado["posicoes"].append(pos)
        estado["ultimo_trade_ts"] = time.time()
        estado["referencia"] = p
        estado["ultima_minima"] = None
        estado["aguardando_reversao"] = False
        estado["ultimo_evento"] = f"Compra AUTO em {fmt_brl(p)}"
        salvar_estado()

    send_text(
        "🟢 COMPRA AUTO\n"
        f"Estratégia: {estado['estrategia']}\n"
        f"Preço: {fmt_brl(p)}\n"
        f"Qtd: {fmt_qty(qty)}\n"
        f"Investido: {fmt_brl(valor)}\n"
        f"Take usado: {fmt_pct(take_usado)}\n"
        f"Alvo: {fmt_brl(pos['alvo'])}\n"
        f"Stop: {fmt_brl(pos['stop_preco'])}"
    )


def abrir_posicao_manual(valor: float):
    p = preco()
    qty = calc_qtd(valor, p)
    ordem_buy_market(qty)

    take_usado = calc_take_dinamico()

    pos = {
        "id": int(time.time() * 1000),
        "tipo": "MANUAL",
        "entrada": p,
        "qtd": qty,
        "investido": valor,
        "alvo": p * (1 + take_usado / 100),
        "stop_preco": p * (1 - float(estado["stop"]) / 100),
        "aberta_em": now_str(),
        "take_usado": take_usado,
        "estrategia": estado["estrategia"],
    }

    with lock:
        estado["posicoes"].append(pos)
        estado["ultimo_trade_ts"] = time.time()
        estado["referencia"] = p
        estado["ultima_minima"] = None
        estado["aguardando_reversao"] = False
        estado["ultimo_evento"] = f"Compra MANUAL em {fmt_brl(p)}"
        salvar_estado()

    send_text(
        "💰 COMPRA MANUAL\n"
        f"Preço: {fmt_brl(p)}\n"
        f"Qtd: {fmt_qty(qty)}\n"
        f"Investido: {fmt_brl(valor)}\n"
        f"Take usado: {fmt_pct(take_usado)}\n"
        f"Alvo: {fmt_brl(pos['alvo'])}\n"
        f"Stop: {fmt_brl(pos['stop_preco'])}"
    )


def fechar_posicao(pos: dict, motivo: str, p_saida: float):
    ordem_sell_market(float(pos["qtd"]))

    valor_final = float(pos["qtd"]) * p_saida
    lucro = valor_final - float(pos["investido"])
    lucro_pct = (lucro / float(pos["investido"])) * 100 if float(pos["investido"]) else 0.0

    registrar_trade(
        tipo="VENDA",
        modo=estado["modo_execucao"],
        estrategia=pos.get("estrategia", estado["estrategia"]),
        entrada=float(pos["entrada"]),
        saida=p_saida,
        quantidade=float(pos["qtd"]),
        investido=float(pos["investido"]),
        valor_final=valor_final,
        lucro_reais=lucro,
        lucro_pct=lucro_pct,
        motivo=motivo,
    )

    with lock:
        estado["lucro_total_realizado"] += lucro
        estado["vendas_realizadas"] += 1
        estado["ultimo_evento"] = f"Venda {motivo} em {fmt_brl(p_saida)}"
        salvar_estado()

    send_text(
        f"💸 VENDA ({motivo})\n"
        f"Entrada: {fmt_brl(float(pos['entrada']))}\n"
        f"Saída: {fmt_brl(p_saida)}\n"
        f"Investido: {fmt_brl(float(pos['investido']))}\n"
        f"Valor final: {fmt_brl(valor_final)}\n"
        f"Lucro: {fmt_brl(lucro)}\n"
        f"Resultado: {fmt_pct(lucro_pct)}"
    )


def verificar_vendas():
    if not estado["posicoes"]:
        return

    p = preco()
    keep = []

    for pos in list(estado["posicoes"]):
        if p >= float(pos["alvo"]):
            fechar_posicao(pos, "TAKE PROFIT", p)
        elif p <= float(pos["stop_preco"]):
            fechar_posicao(pos, "STOP LOSS", p)
        else:
            keep.append(pos)

    with lock:
        estado["posicoes"] = keep
        salvar_estado()


def zerar_tudo_sync():
    if not estado["posicoes"]:
        return "Não há posições abertas."

    p = preco()
    total_resultado = 0.0
    abertas = list(estado["posicoes"])

    for pos in abertas:
        ordem_sell_market(float(pos["qtd"]))
        valor_final = float(pos["qtd"]) * p
        lucro = valor_final - float(pos["investido"])
        lucro_pct = (lucro / float(pos["investido"])) * 100 if float(pos["investido"]) else 0.0
        total_resultado += lucro

        registrar_trade(
            tipo="VENDA",
            modo=estado["modo_execucao"],
            estrategia=pos.get("estrategia", estado["estrategia"]),
            entrada=float(pos["entrada"]),
            saida=p,
            quantidade=float(pos["qtd"]),
            investido=float(pos["investido"]),
            valor_final=valor_final,
            lucro_reais=lucro,
            lucro_pct=lucro_pct,
            motivo="ZERAR TUDO",
        )

    with lock:
        estado["lucro_total_realizado"] += total_resultado
        estado["vendas_realizadas"] += len(abertas)
        estado["posicoes"] = []
        estado["referencia"] = p
        estado["ultima_minima"] = None
        estado["aguardando_reversao"] = False
        estado["ultimo_evento"] = f"Zeragem manual em {fmt_brl(p)}"
        salvar_estado()

    return (
        f"💰 Tudo vendido em {fmt_brl(p)}\n"
        f"Resultado total: {fmt_brl(total_resultado)}"
    )

# =========================================================
# PAINEL / HISTÓRICO / GRÁFICOS
# =========================================================

def painel_texto() -> str:
    p = preco()

    total_investido = sum(float(x["investido"]) for x in estado["posicoes"])
    total_aberto = sum(float(x["qtd"]) * p for x in estado["posicoes"])
    resultado_aberto = total_aberto - total_investido

    return (
        "📊 PAINEL\n\n"
        f"Modo: {estado['modo_execucao']}\n"
        f"Auto: {'ON' if estado['auto'] else 'OFF'}\n"
        f"Par: {SYMBOL}\n"
        f"Preço atual: {fmt_brl(p)}\n"
        f"Referência: {fmt_brl(float(estado['referencia'])) if estado['referencia'] else 'None'}\n\n"
        f"Estratégia: {estado['estrategia']}\n"
        f"Queda compra: {fmt_pct(float(estado['queda']))}\n"
        f"Reversão: {fmt_pct(float(estado['reversao']))}\n"
        f"Take atual: {fmt_pct(calc_take_dinamico())}\n"
        f"Stop individual: {fmt_pct(float(estado['stop']))}\n"
        f"Valor auto: {fmt_brl(float(estado['valor_auto']))}\n"
        f"Máx posições: {int(estado['max_posicoes'])}\n"
        f"Cooldown: {int(estado['cooldown'])}s\n\n"
        f"Posições abertas: {len(estado['posicoes'])}\n"
        f"Investido aberto: {fmt_brl(total_investido)}\n"
        f"Valor aberto atual: {fmt_brl(total_aberto)}\n"
        f"Resultado aberto: {fmt_brl(resultado_aberto)}\n"
        f"Lucro total realizado: {fmt_brl(float(estado['lucro_total_realizado']))}\n"
        f"Vendas realizadas: {int(estado['vendas_realizadas'])}\n"
        f"Último evento: {estado['ultimo_evento']}"
    )


def ler_historico():
    garantir_csv()
    rows = []
    with open(TRADES_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def historico_texto(limit=10) -> str:
    rows = ler_historico()
    if not rows:
        return "📜 HISTÓRICO\n\nNenhum trade ainda."

    ultimos = rows[-limit:]
    parts = ["📜 HISTÓRICO\n"]
    for row in ultimos:
        parts.append(
            f"{row['data']} | {row['motivo']} | "
            f"E {fmt_brl(float(row['entrada']))} | "
            f"S {fmt_brl(float(row['saida']))} | "
            f"L {fmt_brl(float(row['lucro_reais']))}"
        )
    return "\n".join(parts)


def gerar_grafico_pnl():
    rows = ler_historico()
    if not rows:
        return None

    xs = []
    ys = []
    acumulado = 0.0

    for i, row in enumerate(rows, start=1):
        acumulado += float(row["lucro_reais"])
        xs.append(i)
        ys.append(acumulado)

    plt.figure(figsize=(8, 5))
    plt.plot(xs, ys, marker="o")
    plt.title("Lucro acumulado por trade")
    plt.xlabel("Trade")
    plt.ylabel("Lucro acumulado (R$)")
    plt.tight_layout()
    plt.savefig(PNL_GRAPH_FILE)
    plt.close()
    return PNL_GRAPH_FILE


def gerar_grafico_preco():
    if len(recent_prices) < 5:
        return None

    xs = [datetime.fromtimestamp(t) for t, _ in recent_prices]
    ys = [p for _, p in recent_prices]

    plt.figure(figsize=(8, 5))
    plt.plot(xs, ys)
    plt.title(f"Preço recente - {SYMBOL}")
    plt.xlabel("Hora")
    plt.ylabel("Preço (BRL)")
    plt.xticks(rotation=30)
    plt.tight_layout()
    plt.savefig(PRICE_GRAPH_FILE)
    plt.close()
    return PRICE_GRAPH_FILE


# =========================================================
# MENUS
# =========================================================

def menu_principal():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Painel", callback_data="painel")],
        [
            InlineKeyboardButton("📉 Comprar", callback_data="comprar_manual"),
            InlineKeyboardButton("💸 Vender tudo", callback_data="zerar_tudo"),
        ],
        [
            InlineKeyboardButton("🤖 Auto ON/OFF", callback_data="toggle_auto"),
            InlineKeyboardButton("📜 Histórico", callback_data="historico"),
        ],
        [
            InlineKeyboardButton("📈 Gráfico preço", callback_data="grafico_preco"),
            InlineKeyboardButton("📊 Gráfico PnL", callback_data="grafico_pnl"),
        ],
        [
            InlineKeyboardButton("⚙️ Config", callback_data="config"),
            InlineKeyboardButton("🧠 Estratégia", callback_data="estrategia"),
        ],
        [
            InlineKeyboardButton("🧪 Simulado/Real", callback_data="modo_execucao"),
        ],
    ])


def menu_config():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📉 Queda", callback_data="cfg_queda"),
            InlineKeyboardButton("🔄 Reversão", callback_data="cfg_reversao"),
        ],
        [
            InlineKeyboardButton("🎯 Take", callback_data="cfg_take"),
            InlineKeyboardButton("🛑 Stop", callback_data="cfg_stop"),
        ],
        [
            InlineKeyboardButton("💵 Valor auto", callback_data="cfg_valor"),
            InlineKeyboardButton("📦 Máx posições", callback_data="cfg_max"),
        ],
        [
            InlineKeyboardButton("⏱ Cooldown", callback_data="cfg_cooldown"),
            InlineKeyboardButton("🎚 Take auto ON/OFF", callback_data="cfg_take_auto"),
        ],
        [
            InlineKeyboardButton("📈 Tendência ON/OFF", callback_data="cfg_tendencia"),
            InlineKeyboardButton("⬅️ Voltar", callback_data="voltar_menu"),
        ],
    ])


def menu_estrategias():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚡ Scalping", callback_data="est_SCALPING"),
            InlineKeyboardButton("🔄 Reversão", callback_data="est_REVERSAO"),
        ],
        [
            InlineKeyboardButton("📈 Tendência", callback_data="est_TENDENCIA"),
            InlineKeyboardButton("🛠 Custom", callback_data="est_CUSTOM"),
        ],
        [
            InlineKeyboardButton("⬅️ Voltar", callback_data="voltar_menu"),
        ],
    ])


def menu_modo_execucao():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🧪 SIMULADO", callback_data="modo_SIMULADO"),
            InlineKeyboardButton("💰 REAL", callback_data="modo_REAL"),
        ],
        [
            InlineKeyboardButton("⬅️ Voltar", callback_data="voltar_menu"),
        ],
    ])

# =========================================================
# BOTÕES
# =========================================================

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    print("CLICK:", data)

    try:
        if data == "painel":
            await query.message.reply_text(painel_texto())

        elif data == "toggle_auto":
            with lock:
                estado["auto"] = not estado["auto"]
                estado["ultimo_evento"] = f"Auto {'ligado' if estado['auto'] else 'desligado'}"
                salvar_estado()
            await query.message.reply_text(f"🤖 Auto: {'ON' if estado['auto'] else 'OFF'}")

        elif data == "comprar_manual":
            context.user_data["awaiting"] = "manual_buy"
            await query.message.reply_text("Digite o valor em R$ da compra manual:")

        elif data == "zerar_tudo":
            msg = zerar_tudo_sync()
            await query.message.reply_text(msg)

        elif data == "historico":
            await query.message.reply_text(historico_texto())

        elif data == "grafico_preco":
            path = gerar_grafico_preco()
            if not path:
                await query.message.reply_text("Ainda não há dados suficientes de preço.")
            else:
                with open(path, "rb") as f:
                    await query.message.reply_photo(f, caption="📈 Gráfico de preço recente")

        elif data == "grafico_pnl":
            path = gerar_grafico_pnl()
            if not path:
                await query.message.reply_text("Ainda não há trades para gerar o gráfico.")
            else:
                with open(path, "rb") as f:
                    await query.message.reply_photo(f, caption="📊 Gráfico de lucro acumulado")

        elif data == "config":
            await query.message.reply_text("⚙️ CONFIGURAÇÕES", reply_markup=menu_config())

        elif data == "estrategia":
            await query.message.reply_text("🧠 ESCOLHA A ESTRATÉGIA", reply_markup=menu_estrategias())

        elif data == "modo_execucao":
            await query.message.reply_text("🧪 / 💰 MODO DE EXECUÇÃO", reply_markup=menu_modo_execucao())

        elif data == "voltar_menu":
            await query.message.reply_text("🚀 MENU DO BOT", reply_markup=menu_principal())

        # ========= CONFIG =========
        elif data == "cfg_queda":
            context.user_data["awaiting"] = "cfg_queda"
            await query.message.reply_text("Digite a nova queda (%). Ex: 1.2")

        elif data == "cfg_reversao":
            context.user_data["awaiting"] = "cfg_reversao"
            await query.message.reply_text("Digite a nova reversão (%). Ex: 0.4")

        elif data == "cfg_take":
            context.user_data["awaiting"] = "cfg_take"
            await query.message.reply_text("Digite o take fixo (%). Ex: 1.3")

        elif data == "cfg_stop":
            context.user_data["awaiting"] = "cfg_stop"
            await query.message.reply_text("Digite o stop (%). Ex: 1.0")

        elif data == "cfg_valor":
            context.user_data["awaiting"] = "cfg_valor"
            await query.message.reply_text("Digite o valor fixo das operações auto em R$")

        elif data == "cfg_max":
            context.user_data["awaiting"] = "cfg_max"
            await query.message.reply_text("Digite o máximo de posições abertas. Ex: 3")

        elif data == "cfg_cooldown":
            context.user_data["awaiting"] = "cfg_cooldown"
            await query.message.reply_text("Digite o cooldown em segundos. Ex: 60")

        elif data == "cfg_take_auto":
            with lock:
                estado["take_auto"] = not estado["take_auto"]
                salvar_estado()
            await query.message.reply_text(f"Take automático: {'ON' if estado['take_auto'] else 'OFF'}")

        elif data == "cfg_tendencia":
            with lock:
                estado["usar_filtro_tendencia"] = not estado["usar_filtro_tendencia"]
                salvar_estado()
            await query.message.reply_text(
                f"Filtro de tendência: {'ON' if estado['usar_filtro_tendencia'] else 'OFF'}"
            )

        # ========= ESTRATÉGIAS =========
        elif data.startswith("est_"):
            est = data.replace("est_", "")
            with lock:
                estado["estrategia"] = est
                estado["ultimo_evento"] = f"Estratégia alterada para {est}"
                salvar_estado()
            await query.message.reply_text(f"🧠 Estratégia: {est}")

        # ========= MODO EXECUÇÃO =========
        elif data.startswith("modo_"):
            modo = data.replace("modo_", "")
            with lock:
                estado["modo_execucao"] = modo
                estado["ultimo_evento"] = f"Modo alterado para {modo}"
                salvar_estado()
            await query.message.reply_text(f"Modo de execução: {modo}")

        else:
            await query.message.reply_text(f"Botão não tratado: {data}")

    except Exception as e:
        print("ERRO NO BOTÃO:", e)
        await query.message.reply_text(f"Erro no botão: {e}")

# =========================================================
# TEXTO LIVRE DE INPUT
# =========================================================

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return

    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        return

    raw = update.message.text.replace(",", ".").strip()

    try:
        if awaiting == "manual_buy":
            valor = float(raw)
            abrir_posicao_manual(valor)
            await update.message.reply_text("✅ Compra manual registrada.", reply_markup=menu_principal())

        elif awaiting == "cfg_queda":
            estado["queda"] = float(raw)
            salvar_estado()
            await update.message.reply_text(f"Queda atualizada para {estado['queda']}%", reply_markup=menu_principal())

        elif awaiting == "cfg_reversao":
            estado["reversao"] = float(raw)
            salvar_estado()
            await update.message.reply_text(f"Reversão atualizada para {estado['reversao']}%", reply_markup=menu_principal())

        elif awaiting == "cfg_take":
            estado["take"] = float(raw)
            salvar_estado()
            await update.message.reply_text(f"Take fixo atualizado para {estado['take']}%", reply_markup=menu_principal())

        elif awaiting == "cfg_stop":
            estado["stop"] = float(raw)
            # atualiza stops das posições já abertas
            for pos in estado["posicoes"]:
                pos["stop_preco"] = float(pos["entrada"]) * (1 - estado["stop"] / 100)
            salvar_estado()
            await update.message.reply_text(f"Stop atualizado para {estado['stop']}%", reply_markup=menu_principal())

        elif awaiting == "cfg_valor":
            estado["valor_auto"] = float(raw)
            salvar_estado()
            await update.message.reply_text(f"Valor auto atualizado para {fmt_brl(estado['valor_auto'])}", reply_markup=menu_principal())

        elif awaiting == "cfg_max":
            estado["max_posicoes"] = int(float(raw))
            salvar_estado()
            await update.message.reply_text(f"Máx posições atualizado para {estado['max_posicoes']}", reply_markup=menu_principal())

        elif awaiting == "cfg_cooldown":
            estado["cooldown"] = int(float(raw))
            salvar_estado()
            await update.message.reply_text(f"Cooldown atualizado para {estado['cooldown']}s", reply_markup=menu_principal())

        else:
            await update.message.reply_text("Ação não reconhecida.", reply_markup=menu_principal())

    except Exception as e:
        await update.message.reply_text(f"Valor inválido. Erro: {e}")

    context.user_data.pop("awaiting", None)

# =========================================================
# COMANDOS
# =========================================================

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return
    await update.message.reply_text("🚀 MENU DO BOT", reply_markup=menu_principal())


async def cmd_painel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return
    await update.message.reply_text(painel_texto())


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return
    await update.message.reply_text(str({
        "modo_execucao": estado["modo_execucao"],
        "auto": estado["auto"],
        "estrategia": estado["estrategia"],
        "queda": estado["queda"],
        "reversao": estado["reversao"],
        "take": estado["take"],
        "stop": estado["stop"],
        "valor_auto": estado["valor_auto"],
        "max_posicoes": estado["max_posicoes"],
        "cooldown": estado["cooldown"],
        "posicoes": len(estado["posicoes"]),
    }))

# =========================================================
# LOOP PRINCIPAL
# =========================================================

def monitor():
    while True:
        try:
            p = preco()
            update_price_buffers(p)

            if estado["auto"]:
                abrir_posicao_auto()
                verificar_vendas()

            time.sleep(POLL_SECONDS)

        except BinanceAPIException as e:
            print("Binance API error:", e)
            time.sleep(8)
        except Exception as e:
            print("ERRO:", e)
            time.sleep(8)

# =========================================================
# MAIN
# =========================================================

def validar_env():
    missing = []
    if not API_KEY:
        missing.append("API_KEY")
    if not API_SECRET:
        missing.append("API_SECRET")
    if not TOKEN:
        missing.append("TOKEN")
    if not CHAT_ID:
        missing.append("CHAT_ID")

    if missing:
        print("Faltando variáveis:", ", ".join(missing))
        return False
    return True


def main():
    if not validar_env():
        return

    garantir_csv()
    carregar_estado()

    threading.Thread(target=monitor, daemon=True).start()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("menu", cmd_help))
    app.add_handler(CommandHandler("painel", cmd_painel))
    app.add_handler(CommandHandler("status", cmd_status))

    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    print("BOT ONLINE 🚀")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
