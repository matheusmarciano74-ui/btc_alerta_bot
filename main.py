import json
import logging
import math
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import requests
from binance.client import Client
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# =========================================================
# CONFIG
# =========================================================

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()

STATE_FILE = "state.json"
BTC_GRAPH_FILE = "btc_brl.png"
LUCRO_GRAPH_FILE = "lucro.png"
SYMBOL = "BTCBRL"
BASE_ASSET = "BTC"
QUOTE_ASSET = "BRL"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("btc_bot")


# =========================================================
# STATE
# =========================================================

def default_state() -> Dict[str, Any]:
    return {
        "modo": "SIMULADO",   # SIMULADO / REAL
        "rodando": False,
        "config": {
            "queda": 2.0,
            "lucro": 3.0,
            "valor": 100.0,
            "max_dca": 5,
            "cooldown": 600
        },
        "auto_estrategia": True,
        "regime_mercado": "INDEFINIDO",
        "config_ativa": {
            "queda": 2.0,
            "lucro": 3.0,
            "valor": 100.0,
            "max_dca": 5,
            "cooldown": 600
        },
        "ultimo_snapshot_estrategia": "",
        "historico_regime": [],
        "posicoes": [],
        "lucro_total": 0.0,
        "ultima_compra_ts": 0,
        "ultimo_preco": None,
        "preco_referencia": None,
        "ultimo_check": None,
        "logs": [],
        "historico_lucro": [],
    }


def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        state = default_state()
        save_state(state)
        return state

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)

        state = default_state()
        state.update(raw)

        if not isinstance(state.get("config"), dict):
            state["config"] = default_state()["config"]

        if not isinstance(state.get("config_ativa"), dict):
            state["config_ativa"] = dict(state["config"])

        if not isinstance(state.get("posicoes"), list):
            state["posicoes"] = []

        if not isinstance(state.get("logs"), list):
            state["logs"] = []

        if not isinstance(state.get("historico_lucro"), list):
            state["historico_lucro"] = []

        if not isinstance(state.get("historico_regime"), list):
            state["historico_regime"] = []

        return state

    except Exception as e:
        logger.exception("Erro carregando state.json: %s", e)
        state = default_state()
        save_state(state)
        return state


def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


state = load_state()


def add_log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%d/%m %H:%M:%S')}] {msg}"
    logger.info(msg)
    state["logs"].append(line)
    state["logs"] = state["logs"][-50:]
    save_state(state)


# =========================================================
# HELPERS
# =========================================================

def format_brl(v: float) -> str:
    s = f"{v:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def now_ts() -> int:
    return int(datetime.now().timestamp())


def get_public_price() -> float:
    r = requests.get(
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": SYMBOL},
        timeout=15,
    )
    r.raise_for_status()
    return float(r.json()["price"])


def get_public_klines(limit: int = 120, interval: str = "5m") -> List[List[Any]]:
    r = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": SYMBOL, "interval": interval, "limit": limit},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def get_binance_client() -> Client:
    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        raise RuntimeError("BINANCE_API_KEY / BINANCE_API_SECRET não configuradas.")
    return Client(BINANCE_API_KEY, BINANCE_API_SECRET)


def get_symbol_info() -> Dict[str, Any]:
    client = get_binance_client()
    info = client.get_symbol_info(SYMBOL)
    if not info:
        raise RuntimeError(f"Não foi possível obter symbol info de {SYMBOL}.")
    return info


def get_lot_size_filter() -> Tuple[float, float]:
    info = get_symbol_info()

    step_size = None
    min_qty = None

    for f in info["filters"]:
        if f["filterType"] == "LOT_SIZE":
            step_size = float(f["stepSize"])
            min_qty = float(f["minQty"])
            break

    if step_size is None or min_qty is None:
        raise RuntimeError("Não foi possível obter LOT_SIZE da Binance.")

    return step_size, min_qty


def adjust_quantity_to_step(quantity: float) -> float:
    step_size, min_qty = get_lot_size_filter()

    adjusted = math.floor(quantity / step_size) * step_size

    step_str = f"{step_size:.18f}".rstrip("0")
    decimals = 0
    if "." in step_str:
        decimals = len(step_str.split(".")[1])

    adjusted = round(adjusted, decimals)

    if adjusted < min_qty:
        raise RuntimeError(
            f"Quantidade ajustada ficou abaixo do mínimo da Binance. "
            f"Qtd ajustada: {adjusted} | Mínimo: {min_qty}"
        )

    return adjusted


def get_free_asset_balance(asset: str) -> float:
    client = get_binance_client()
    balance = client.get_asset_balance(asset=asset)
    if not balance:
        return 0.0
    return float(balance.get("free", 0) or 0)


def get_real_balances() -> Tuple[float, float]:
    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        return 0.0, 0.0
    try:
        brl = get_free_asset_balance(QUOTE_ASSET)
        btc = get_free_asset_balance(BASE_ASSET)
        return brl, btc
    except Exception:
        return 0.0, 0.0


def open_positions() -> List[Dict[str, Any]]:
    return [p for p in state["posicoes"] if p.get("status") == "OPEN"]


def total_investido_aberto() -> float:
    return sum(float(p["valor"]) for p in open_positions())


def total_pnl_aberto(preco_atual: Optional[float]) -> float:
    if preco_atual is None:
        return 0.0

    total = 0.0
    for p in open_positions():
        qtd = float(p["q"])
        valor_atual = qtd * preco_atual
        total += valor_atual - float(p["valor"])
    return total


def can_buy() -> bool:
    if len(open_positions()) >= int(get_active_config()["max_dca"]):
        return False

    cooldown = int(get_active_config()["cooldown"])
    return (now_ts() - int(state.get("ultima_compra_ts", 0))) >= cooldown


# =========================================================
# INDICADORES / ESTRATÉGIA
# =========================================================

def ema(values: List[float], period: int) -> float:
    if len(values) < period:
        return sum(values) / len(values)

    k = 2 / (period + 1)
    ema_value = sum(values[:period]) / period
    for price in values[period:]:
        ema_value = price * k + ema_value * (1 - k)
    return ema_value


def calc_rsi(values: List[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def analyze_market() -> Dict[str, Any]:
    klines = get_public_klines(limit=120, interval="5m")
    closes = [float(k[4]) for k in klines]

    if len(closes) < 60:
        return {
            "regime": "INDEFINIDO",
            "ema9": 0.0,
            "ema21": 0.0,
            "ema50": 0.0,
            "rsi": 50.0,
            "slope_pct": 0.0,
            "pullback_pct": 0.0,
            "last_price": closes[-1] if closes else 0.0,
        }

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    ema50 = ema(closes, 50)
    rsi = calc_rsi(closes, 14)

    last_price = closes[-1]
    old_price = closes[-13] if len(closes) >= 13 else closes[0]
    slope_pct = ((last_price - old_price) / old_price) * 100 if old_price else 0.0

    recent_high = max(closes[-18:])
    pullback_pct = ((recent_high - last_price) / recent_high) * 100 if recent_high else 0.0

    regime = "LATERAL"

    if ema9 > ema21 > ema50 and slope_pct > 0.35:
        regime = "ALTA"
    elif ema9 < ema21 < ema50 and slope_pct < -0.35:
        regime = "QUEDA"
    else:
        regime = "LATERAL"

    return {
        "regime": regime,
        "ema9": ema9,
        "ema21": ema21,
        "ema50": ema50,
        "rsi": rsi,
        "slope_pct": slope_pct,
        "pullback_pct": pullback_pct,
        "last_price": last_price,
    }


def strategy_for_regime(regime: str) -> Dict[str, Any]:
    base_valor = float(state["config"]["valor"])

    if regime == "ALTA":
        return {
            "queda": 0.8,
            "lucro": 2.0,
            "valor": base_valor,
            "max_dca": 3,
            "cooldown": 180,
        }

    if regime == "QUEDA":
        return {
            "queda": 2.0,
            "lucro": 3.2,
            "valor": base_valor,
            "max_dca": max(5, int(state["config"]["max_dca"])),
            "cooldown": 600,
        }

    return {
        "queda": 1.1,
        "lucro": 1.7,
        "valor": base_valor,
        "max_dca": 3,
        "cooldown": 300,
    }


def get_active_config() -> Dict[str, Any]:
    if state.get("auto_estrategia", True):
        return state.get("config_ativa", dict(state["config"]))
    return state["config"]


def snapshot_strategy_text(analysis: Dict[str, Any], active_cfg: Dict[str, Any]) -> str:
    return (
        f"regime={analysis['regime']} | "
        f"rsi={analysis['rsi']:.2f} | "
        f"ema9={analysis['ema9']:.2f} | "
        f"ema21={analysis['ema21']:.2f} | "
        f"ema50={analysis['ema50']:.2f} | "
        f"slope={analysis['slope_pct']:.2f}% | "
        f"pullback={analysis['pullback_pct']:.2f}% | "
        f"queda={active_cfg['queda']} | "
        f"lucro={active_cfg['lucro']} | "
        f"dca={active_cfg['max_dca']} | "
        f"cooldown={active_cfg['cooldown']}"
    )


def should_buy_professional(analysis: Dict[str, Any], preco: float) -> Tuple[bool, str]:
    regime = analysis["regime"]
    rsi = analysis["rsi"]
    pullback = analysis["pullback_pct"]

    ref = state.get("preco_referencia")
    active_cfg = get_active_config()

    if ref is None:
        return False, "sem referencia"

    ref = float(ref)
    queda_cfg = float(active_cfg["queda"])
    preco_disparo = ref * (1 - queda_cfg / 100)

    if preco <= preco_disparo and can_buy():
        return True, f"queda clássica {queda_cfg}%"

    if regime == "ALTA":
        if pullback >= 0.35 and pullback <= 1.20 and 48 <= rsi <= 68 and can_buy():
            return True, "continuação em tendência de alta"

    if regime == "QUEDA":
        if rsi <= 28 and pullback >= 1.0 and can_buy():
            return True, "reversão em sobrevenda"

    if regime == "LATERAL":
        if rsi <= 38 and pullback >= 0.45 and can_buy():
            return True, "scalp em lateral"

    return False, "sem gatilho"


# =========================================================
# TELEGRAM UI
# =========================================================

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Status", callback_data="status"),
            InlineKeyboardButton("📦 Posições", callback_data="positions"),
        ],
        [
            InlineKeyboardButton("📈 BTC", callback_data="grafico_btc"),
            InlineKeyboardButton("💰 Lucro", callback_data="grafico_lucro"),
        ],
        [
            InlineKeyboardButton("⚙️ Config", callback_data="config"),
            InlineKeyboardButton("🧾 Logs", callback_data="logs"),
        ],
        [
            InlineKeyboardButton(
                f"🤖 Estratégia: {'AUTO' if state.get('auto_estrategia', True) else 'MANUAL'}",
                callback_data="toggle_auto_estrategia",
            ),
        ],
        [
            InlineKeyboardButton(f"🧠 Modo: {state['modo']}", callback_data="modo"),
        ],
        [
            InlineKeyboardButton("▶️ Start", callback_data="start"),
            InlineKeyboardButton("⛔ Stop", callback_data="stop"),
        ],
        [
            InlineKeyboardButton("🟢 Comprar", callback_data="buy"),
            InlineKeyboardButton("🔴 Vender tudo", callback_data="sell_all"),
        ],
    ])


def config_menu() -> InlineKeyboardMarkup:
    c = state["config"]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📉 Queda: {c['queda']}%", callback_data="cfg_queda_menu")],
        [InlineKeyboardButton(f"📈 Lucro: {c['lucro']}%", callback_data="cfg_lucro_menu")],
        [InlineKeyboardButton(f"💰 Valor: {format_brl(float(c['valor']))}", callback_data="cfg_valor_menu")],
        [InlineKeyboardButton(f"📚 Max DCA: {c['max_dca']}", callback_data="cfg_dca_menu")],
        [InlineKeyboardButton(f"⏱ Cooldown: {c['cooldown']}s", callback_data="cfg_cooldown_menu")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="menu")],
    ])


def queda_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("0.5%", callback_data="set_queda_0.5"),
            InlineKeyboardButton("1%", callback_data="set_queda_1"),
            InlineKeyboardButton("1.5%", callback_data="set_queda_1.5"),
        ],
        [
            InlineKeyboardButton("2%", callback_data="set_queda_2"),
            InlineKeyboardButton("3%", callback_data="set_queda_3"),
            InlineKeyboardButton("5%", callback_data="set_queda_5"),
        ],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="config")],
    ])


def lucro_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1%", callback_data="set_lucro_1"),
            InlineKeyboardButton("1.5%", callback_data="set_lucro_1.5"),
            InlineKeyboardButton("2%", callback_data="set_lucro_2"),
        ],
        [
            InlineKeyboardButton("3%", callback_data="set_lucro_3"),
            InlineKeyboardButton("4%", callback_data="set_lucro_4"),
            InlineKeyboardButton("5%", callback_data="set_lucro_5"),
        ],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="config")],
    ])


def valor_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("20", callback_data="set_valor_20"),
            InlineKeyboardButton("30", callback_data="set_valor_30"),
            InlineKeyboardButton("40", callback_data="set_valor_40"),
        ],
        [
            InlineKeyboardButton("50", callback_data="set_valor_50"),
            InlineKeyboardButton("100", callback_data="set_valor_100"),
        ],
        [
            InlineKeyboardButton("200", callback_data="set_valor_200"),
            InlineKeyboardButton("300", callback_data="set_valor_300"),
            InlineKeyboardButton("500", callback_data="set_valor_500"),
        ],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="config")],
    ])


def dca_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1", callback_data="set_dca_1"),
            InlineKeyboardButton("2", callback_data="set_dca_2"),
            InlineKeyboardButton("3", callback_data="set_dca_3"),
        ],
        [
            InlineKeyboardButton("4", callback_data="set_dca_4"),
            InlineKeyboardButton("5", callback_data="set_dca_5"),
            InlineKeyboardButton("7", callback_data="set_dca_7"),
        ],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="config")],
    ])


def cooldown_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("60s", callback_data="set_cooldown_60"),
            InlineKeyboardButton("120s", callback_data="set_cooldown_120"),
            InlineKeyboardButton("300s", callback_data="set_cooldown_300"),
        ],
        [
            InlineKeyboardButton("600s", callback_data="set_cooldown_600"),
            InlineKeyboardButton("900s", callback_data="set_cooldown_900"),
            InlineKeyboardButton("1800s", callback_data="set_cooldown_1800"),
        ],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="config")],
    ])


# =========================================================
# TEXT BUILDERS
# =========================================================

def status_text() -> str:
    preco = state.get("ultimo_preco")
    active_cfg = get_active_config()
    brl_real, btc_real = get_real_balances()

    txt = [
        "📊 STATUS DO BOT",
        "",
        f"Modo: {state['modo']}",
        f"Rodando: {'✅ SIM' if state['rodando'] else '⛔ NÃO'}",
        f"Estratégia: {'AUTO' if state.get('auto_estrategia', True) else 'MANUAL'}",
        f"Regime mercado: {state.get('regime_mercado', '---')}",
        f"Preço atual: {format_brl(float(preco)) if preco else '---'}",
        f"Referência: {format_brl(float(state['preco_referencia'])) if state.get('preco_referencia') else '---'}",
        "",
        "💼 SALDO REAL BINANCE",
        f"BRL livre: {format_brl(brl_real)}",
        f"BTC livre: {btc_real:.8f}",
        "",
        "⚙️ CONFIG ATIVA",
        f"Queda: {active_cfg['queda']}%",
        f"Lucro: {active_cfg['lucro']}%",
        f"Valor: {format_brl(float(active_cfg['valor']))}",
        f"Max DCA: {active_cfg['max_dca']}",
        f"Cooldown: {active_cfg['cooldown']}s",
        "",
        f"Posições abertas: {len(open_positions())} / {int(active_cfg['max_dca'])}",
        f"Total investido aberto: {format_brl(total_investido_aberto())}",
        f"P/L aberto: {format_brl(total_pnl_aberto(preco))}",
        f"Lucro total realizado: {format_brl(float(state['lucro_total']))}",
        f"Último check: {state.get('ultimo_check') or '---'}",
    ]
    return "\n".join(txt)



def positions_text() -> str:
    pos = open_positions()
    if not pos:
        return "📦 Nenhuma posição aberta."

    preco_atual = state.get("ultimo_preco") or get_public_price()
    lines = ["📦 POSIÇÕES ABERTAS", ""]
    for i, p in enumerate(pos, start=1):
        valor_atual = float(p["q"]) * float(preco_atual)
        lucro_atual = valor_atual - float(p["valor"])
        lines.extend([
            f"{i}) Entrada: {format_brl(float(p['preco']))}",
            f"   Valor investido: {format_brl(float(p['valor']))}",
            f"   Valor atual: {format_brl(valor_atual)}",
            f"   Lucro atual: {format_brl(lucro_atual)}",
            f"   Qtd BTC: {float(p['q']):.8f}",
            f"   TP: {format_brl(float(p['tp']))}",
            f"   Modo da compra: {p.get('modo', '---')}",
            f"   Motivo: {p.get('motivo', '---')}",
            "",
        ])
    return "\n".join(lines).strip()



def positions_menu() -> InlineKeyboardMarkup:
    pos = open_positions()
    rows = []
    for i, _p in enumerate(pos, start=1):
        rows.append([InlineKeyboardButton(f"🔴 Vender posição #{i}", callback_data=f"sell_pos_{i-1}")])
    rows.append([InlineKeyboardButton("⬅️ Voltar", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


# =========================================================
# GRÁFICOS
# =========================================================

def generate_btc_chart() -> str:
    klines = get_public_klines(limit=100, interval="5m")
    closes = [float(k[4]) for k in klines]

    plt.figure(figsize=(10, 5))
    plt.plot(closes)
    plt.title("BTC/BRL - últimos 100 candles de 5m")
    plt.xlabel("Candles")
    plt.ylabel("Preço")
    plt.tight_layout()
    plt.savefig(BTC_GRAPH_FILE)
    plt.close()

    return BTC_GRAPH_FILE


def generate_lucro_chart() -> Optional[str]:
    hist = state.get("historico_lucro", [])
    if not hist:
        return None

    plt.figure(figsize=(10, 5))
    plt.plot(hist)
    plt.title("Lucro acumulado")
    plt.xlabel("Operações fechadas")
    plt.ylabel("Lucro (BRL)")
    plt.tight_layout()
    plt.savefig(LUCRO_GRAPH_FILE)
    plt.close()

    return LUCRO_GRAPH_FILE


# =========================================================
# TRADING
# =========================================================

def buy_position(preco: float, motivo: str) -> None:
    active_cfg = get_active_config()
    valor = float(active_cfg["valor"])
    lucro_pct = float(active_cfg["lucro"])

    qty = valor / preco
    executed_price = preco
    invested_value = valor

    if state["modo"] == "REAL":
        client = get_binance_client()
        order = client.order_market_buy(symbol=SYMBOL, quoteOrderQty=round(valor, 2))

        executed_qty = float(order.get("executedQty", 0) or 0)
        cummulative_quote_qty = float(order.get("cummulativeQuoteQty", 0) or 0)

        if executed_qty <= 0:
            raise RuntimeError("Compra REAL retornou quantidade executada zero.")

        qty = executed_qty
        invested_value = cummulative_quote_qty if cummulative_quote_qty > 0 else valor
        executed_price = invested_value / qty

    tp = executed_price * (1 + lucro_pct / 100)

    state["posicoes"].append({
        "status": "OPEN",
        "preco": executed_price,
        "q": qty,
        "valor": invested_value,
        "tp": tp,
        "opened_at": datetime.now().isoformat(timespec="seconds"),
        "motivo": motivo,
        "modo": state["modo"],
        "regime_na_entrada": state.get("regime_mercado", "INDEFINIDO"),
    })
    state["ultima_compra_ts"] = now_ts()
    save_state(state)

    add_log(
        f"Compra {state['modo']} em {format_brl(executed_price)} | "
        f"valor {format_brl(invested_value)} | qtd {qty:.8f} | motivo: {motivo}"
    )


def close_position_simulada(p: Dict[str, Any], preco_saida: float, motivo: str) -> float:
    qty = float(p["q"])
    valor_entrada = float(p["valor"])
    executed_exit_price = preco_saida

    lucro = (executed_exit_price * qty) - valor_entrada

    p["status"] = "CLOSED"
    p["closed_at"] = datetime.now().isoformat(timespec="seconds")
    p["preco_saida"] = executed_exit_price
    p["lucro"] = lucro
    p["sell_reason"] = motivo

    state["lucro_total"] = float(state["lucro_total"]) + lucro
    state["historico_lucro"].append(float(state["lucro_total"]))

    save_state(state)
    add_log(
        f"Venda SIMULADO em {format_brl(executed_exit_price)} | "
        f"lucro {format_brl(lucro)} | motivo: {motivo}"
    )
    return lucro


def close_position_real_single(p: Dict[str, Any], preco_ref: float, motivo: str) -> float:
    qty = float(p["q"])
    valor_entrada = float(p["valor"])

    client = get_binance_client()

    free_btc = get_free_asset_balance(BASE_ASSET)
    qty_base = min(qty, free_btc)
    if qty_base <= 0:
        raise RuntimeError("Sem BTC suficiente na conta para vender esta posição.")

    try:
        sell_qty = adjust_quantity_to_step(qty_base * 0.999)
    except Exception:
        sell_qty = round(qty_base * 0.999, 6)

    if sell_qty <= 0:
        raise RuntimeError("Quantidade inválida após ajuste para venda da posição.")

    last_error = None
    order = None
    for _tentativa in range(3):
        try:
            order = client.order_market_sell(symbol=SYMBOL, quantity=sell_qty)
            break
        except Exception as e:
            last_error = e
    if order is None:
        raise RuntimeError(f"Falha ao vender posição na Binance: {last_error}")

    executed_qty = float(order.get("executedQty", 0) or 0)
    cummulative_quote_qty = float(order.get("cummulativeQuoteQty", 0) or 0)

    if executed_qty <= 0:
        raise RuntimeError("Venda REAL retornou quantidade executada zero.")

    executed_exit_price = cummulative_quote_qty / executed_qty
    lucro = cummulative_quote_qty - valor_entrada

    p["status"] = "CLOSED"
    p["closed_at"] = datetime.now().isoformat(timespec="seconds")
    p["preco_saida"] = executed_exit_price
    p["lucro"] = lucro
    p["sell_reason"] = motivo

    state["lucro_total"] = float(state["lucro_total"]) + lucro
    state["historico_lucro"].append(float(state["lucro_total"]))

    save_state(state)
    add_log(
        f"Venda REAL posição em {format_brl(executed_exit_price)} | "
        f"lucro {format_brl(lucro)} | motivo: {motivo}"
    )
    return lucro


def close_all_real_positions(preco_ref: float, motivo: str) -> Dict[str, Any]:
    abertas = list(open_positions())
    if not abertas:
        return {
            "ok": False,
            "message": "Não há posições abertas.",
            "lucro_total": 0.0,
            "remaining_btc": 0.0,
        }

    client = get_binance_client()

    free_btc = get_free_asset_balance(BASE_ASSET)
    if free_btc <= 0:
        raise RuntimeError("Saldo BTC livre zerado na Binance.")

    try:
        sell_qty = adjust_quantity_to_step(free_btc * 0.999)
    except Exception:
        sell_qty = round(free_btc * 0.999, 6)

    if sell_qty <= 0:
        raise RuntimeError("Quantidade inválida após ajuste para venda total.")

    last_error = None
    order = None
    for _tentativa in range(3):
        try:
            order = client.order_market_sell(symbol=SYMBOL, quantity=sell_qty)
            break
        except Exception as e:
            last_error = e
    if order is None:
        raise RuntimeError(f"Falha ao vender tudo na Binance: {last_error}")

    executed_qty = float(order.get("executedQty", 0) or 0)
    cummulative_quote_qty = float(order.get("cummulativeQuoteQty", 0) or 0)

    if executed_qty <= 0:
        raise RuntimeError("Venda REAL geral retornou quantidade executada zero.")

    executed_exit_price = cummulative_quote_qty / executed_qty

    invested_total = sum(float(p["valor"]) for p in abertas)
    lucro_total = cummulative_quote_qty - invested_total

    lucro_restante = lucro_total

    for i, p in enumerate(abertas):
        valor_entrada = float(p["valor"])

        if i < len(abertas) - 1:
            proporcao = valor_entrada / invested_total if invested_total > 0 else 0
            lucro_pos = lucro_total * proporcao
            lucro_restante -= lucro_pos
        else:
            lucro_pos = lucro_restante

        p["status"] = "CLOSED"
        p["closed_at"] = datetime.now().isoformat(timespec="seconds")
        p["preco_saida"] = executed_exit_price
        p["lucro"] = lucro_pos
        p["sell_reason"] = motivo

    state["lucro_total"] = float(state["lucro_total"]) + lucro_total
    state["historico_lucro"].append(float(state["lucro_total"]))

    save_state(state)

    remaining_btc = get_free_asset_balance(BASE_ASSET)
    ok = remaining_btc <= 0.000001
    if ok:
        add_log(
            f"Venda REAL geral em {format_brl(executed_exit_price)} | "
            f"lucro total {format_brl(lucro_total)} | motivo: {motivo}"
        )
        message = f"🔴 Todas as posições foram vendidas.\nLucro desta saída: {format_brl(lucro_total)}"

    else:
        add_log(
            f"ALERTA: venda total executada, mas ainda sobrou BTC na conta: {remaining_btc:.8f}"
        )
        message = (
            f"⚠️ A ordem de venda foi enviada, mas ainda sobrou BTC na conta: {remaining_btc:.8f}\n"

            f"Lucro desta saída: {format_brl(lucro_total)}"
        )

    return {
        "ok": ok,
        "message": message,
        "lucro_total": lucro_total,
        "remaining_btc": remaining_btc,
    }


# =========================================================
# LOOP PRINCIPAL
# =========================================================

async def bot_loop(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        preco = get_public_price()
        state["ultimo_preco"] = preco
        state["ultimo_check"] = datetime.now().strftime("%d/%m %H:%M:%S")

        analysis = analyze_market()
        state["regime_mercado"] = analysis["regime"]

        if state.get("auto_estrategia", True):
            active_cfg = strategy_for_regime(analysis["regime"])
            state["config_ativa"] = active_cfg
        else:
            active_cfg = dict(state["config"])
            state["config_ativa"] = active_cfg

        snapshot = snapshot_strategy_text(analysis, active_cfg)
        if snapshot != state.get("ultimo_snapshot_estrategia", ""):
            state["ultimo_snapshot_estrategia"] = snapshot
            state["historico_regime"].append({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "regime": analysis["regime"],
                "rsi": round(analysis["rsi"], 2),
                "slope_pct": round(analysis["slope_pct"], 2),
            })
            state["historico_regime"] = state["historico_regime"][-100:]
            add_log(f"Estratégia ativa: {snapshot}")

        if state["preco_referencia"] is None:
            state["preco_referencia"] = preco

        if state["rodando"]:
            if len(open_positions()) == 0 and preco > float(state["preco_referencia"]):
                state["preco_referencia"] = preco

            should_buy, reason = should_buy_professional(analysis, preco)

            if should_buy:
                try:
                    buy_position(preco, reason)
                    state["preco_referencia"] = preco

                    if CHAT_ID:
                        last = state["posicoes"][-1]
                        await context.bot.send_message(
                            chat_id=CHAT_ID,
                            text=(
                                "🟢 COMPRA AUTOMÁTICA\n"
                                f"Modo: {state['modo']}\n"
                                f"Regime: {state.get('regime_mercado', '---')}\n"
                                f"Motivo: {reason}\n"
                                f"Preço: {format_brl(float(last['preco']))}\n"
                                f"Valor: {format_brl(float(last['valor']))}\n"
                                f"Qtd BTC: {float(last['q']):.8f}"
                            ),
                        )
                except Exception as e:
                    add_log(f"Erro ao comprar automaticamente: {e}")

            for p in list(open_positions()):
                if preco >= float(p["tp"]):
                    try:
                        if p.get("modo") == "REAL":
                            lucro = close_position_real_single(p, preco, "take profit")
                        else:
                            lucro = close_position_simulada(p, preco, "take profit")

                        if CHAT_ID:
                            await context.bot.send_message(
                                chat_id=CHAT_ID,
                                text=(
                                    "🔴 VENDA AUTOMÁTICA\n"
                                    f"Modo: {p.get('modo', 'SIMULADO')}\n"
                                    f"Regime na entrada: {p.get('regime_na_entrada', '---')}\n"
                                    f"Entrada: {format_brl(float(p['preco']))}\n"
                                    f"Saída: {format_brl(float(p['preco_saida']))}\n"
                                    f"Lucro: {format_brl(lucro)}"
                                ),
                            )
                    except Exception as e:
                        add_log(f"Erro ao vender automaticamente: {e}")

            if len(open_positions()) == 0:
                state["preco_referencia"] = preco

        save_state(state)

    except Exception as e:
        add_log(f"Erro no loop: {e}")


# =========================================================
# COMMAND
# =========================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 BOT ONLINE\n\nEscolha uma opção:",
        reply_markup=main_menu(),
    )


# =========================================================
# CALLBACKS
# =========================================================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    logger.info("Callback recebido: %s", data)

    try:
        if data == "menu":
            await query.edit_message_text(
                "🤖 BOT ONLINE\n\nEscolha uma opção:",
                reply_markup=main_menu(),
            )
            return

        if data == "status":
            await query.edit_message_text(
                status_text(),
                reply_markup=main_menu(),
            )
            return

        if data == "positions":
            await query.edit_message_text(
                positions_text(),
                reply_markup=positions_menu(),
            )
            return

        if data.startswith("sell_pos_"):
            idx = int(data.split("_")[-1])
            abertas = open_positions()
            if idx < 0 or idx >= len(abertas):
                await query.edit_message_text(
                    "❌ Posição inválida.",
                    reply_markup=positions_menu(),
                )
                return

            pos = abertas[idx]
            preco_ref = get_public_price()
            try:
                if pos.get("modo") == "REAL":
                    lucro = close_position_real_single(pos, preco_ref, "venda manual individual")
                else:
                    lucro = close_position_simulada(pos, preco_ref, "venda manual individual")

                remaining_btc = get_free_asset_balance(BASE_ASSET) if BINANCE_API_KEY and BINANCE_API_SECRET else 0.0
                msg = (
                    f"🔴 Posição #{idx + 1} vendida.\n"
                    f"Lucro realizado: {format_brl(lucro)}"
                )
                if pos.get("modo") == "REAL" and remaining_btc > 0.000001:
                    msg += f"\nBTC livre restante na conta: {remaining_btc:.8f}"

                await query.edit_message_text(
                    msg,
                    reply_markup=main_menu(),
                )
            except Exception as e:
                add_log(f"Erro em vender posição individual: {e}")
                await query.edit_message_text(
                    f"❌ Erro ao vender posição individual:\n{e}",

                    reply_markup=positions_menu(),
                )
            return

        if data == "logs":
            await query.edit_message_text(
                logs_text(),
                reply_markup=main_menu(),
            )
            return

        if data == "config":
            await query.edit_message_text(
                "⚙️ CONFIGURAÇÃO MANUAL\n\nQuando a estratégia AUTO estiver ligada, o bot usa a config automática do regime.\nA config abaixo continua salva como base manual.",
                reply_markup=config_menu(),
            )
            return

        if data == "grafico_btc":
            path = generate_btc_chart()
            with open(path, "rb") as f:
                await context.bot.send_photo(chat_id=query.message.chat_id, photo=f)
            return

        if data == "grafico_lucro":
            path = generate_lucro_chart()
            if path is None:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="Ainda não há histórico de lucro para gerar gráfico."
                )
            else:
                with open(path, "rb") as f:
                    await context.bot.send_photo(chat_id=query.message.chat_id, photo=f)
            return

        if data == "toggle_auto_estrategia":
            state["auto_estrategia"] = not state.get("auto_estrategia", True)
            save_state(state)
            add_log(
                f"Estratégia alterada para {'AUTO' if state['auto_estrategia'] else 'MANUAL'}"
            )
            await query.edit_message_text(
                f"🤖 Estratégia agora está em: {'AUTO' if state['auto_estrategia'] else 'MANUAL'}",
                reply_markup=main_menu(),
            )
            return

        if data == "modo":
            novo_modo = "REAL" if state["modo"] == "SIMULADO" else "SIMULADO"

            if novo_modo == "REAL" and (not BINANCE_API_KEY or not BINANCE_API_SECRET):
                await query.edit_message_text(
                    "❌ Não é possível ativar REAL sem BINANCE_API_KEY e BINANCE_API_SECRET.",
                    reply_markup=main_menu(),
                )
                return

            state["modo"] = novo_modo
            save_state(state)
            add_log(f"Modo alterado para {state['modo']}")

            await query.edit_message_text(
                f"🧠 Modo alterado para: {state['modo']}",
                reply_markup=main_menu(),
            )
            return

        if data == "start":
            state["rodando"] = True
            save_state(state)
            add_log("Bot ligado")
            await query.edit_message_text(
                "▶️ Bot ligado.",
                reply_markup=main_menu(),
            )
            return

        if data == "stop":
            state["rodando"] = False
            save_state(state)
            add_log("Bot parado")
            await query.edit_message_text(
                "⛔ Bot parado.",
                reply_markup=main_menu(),
            )
            return

        if data == "buy":
            preco = get_public_price()
            state["ultimo_preco"] = preco

            try:
                buy_position(preco, "compra manual")
                last = state["posicoes"][-1]
                await query.edit_message_text(
                    (
                        f"🟢 Compra manual {state['modo']} feita.\n"
                        f"Preço: {format_brl(float(last['preco']))}\n"
                        f"Valor: {format_brl(float(last['valor']))}\n"
                        f"Qtd BTC: {float(last['q']):.8f}"
                    ),
                    reply_markup=main_menu(),
                )
            except Exception as e:
                add_log(f"Erro na compra manual: {e}")
                await query.edit_message_text(
                    f"❌ Erro na compra manual:\n{e}",
                    reply_markup=main_menu(),
                )
            return

        if data == "sell_all":
            preco = get_public_price()
            abertas = list(open_positions())

            if not abertas:
                await query.edit_message_text(
                    "ℹ️ Não há posições abertas.",
                    reply_markup=main_menu(),
                )
                return

            try:
                if state["modo"] == "REAL":
                    lucro_total_saida = close_all_real_positions(preco, "venda manual geral")
                else:
                    lucro_total_saida = 0.0
                    for p in abertas:
                        lucro_total_saida += close_position_simulada(p, preco, "venda manual geral")

                await query.edit_message_text(
                    f"🔴 Todas as posições foram vendidas.\nLucro desta saída: {format_brl(lucro_total_saida)}",
                    reply_markup=main_menu(),
                )
            except Exception as e:
                add_log(f"Erro em vender tudo: {e}")
                await query.edit_message_text(
                    f"❌ Erro ao vender tudo:\n{e}",
                    reply_markup=main_menu(),
                )
            return

        if data == "cfg_queda_menu":
            await query.edit_message_text(
                "📉 Escolha a queda:",
                reply_markup=queda_menu(),
            )
            return

        if data == "cfg_lucro_menu":
            await query.edit_message_text(
                "📈 Escolha o lucro:",
                reply_markup=lucro_menu(),
            )
            return

        if data == "cfg_valor_menu":
            await query.edit_message_text(
                "💰 Escolha o valor por compra:",
                reply_markup=valor_menu(),
            )
            return

        if data == "cfg_dca_menu":
            await query.edit_message_text(
                "📚 Escolha o máximo de DCA:",
                reply_markup=dca_menu(),
            )
            return

        if data == "cfg_cooldown_menu":
            await query.edit_message_text(
                "⏱ Escolha o cooldown:",
                reply_markup=cooldown_menu(),
            )
            return

        if data.startswith("set_queda_"):
            valor = float(data.replace("set_queda_", ""))
            state["config"]["queda"] = valor
            save_state(state)
            add_log(f"Queda manual alterada para {valor}%")
            await query.edit_message_text(
                f"✅ Queda manual alterada para {valor}%",
                reply_markup=config_menu(),
            )
            return

        if data.startswith("set_lucro_"):
            valor = float(data.replace("set_lucro_", ""))
            state["config"]["lucro"] = valor

            if not state.get("auto_estrategia", True):
                for p in open_positions():
                    entrada = float(p["preco"])
                    p["tp"] = entrada * (1 + valor / 100)

            save_state(state)
            add_log(f"Lucro manual alterado para {valor}%")
            await query.edit_message_text(
                f"✅ Lucro manual alterado para {valor}%",
                reply_markup=config_menu(),
            )
            return

        if data.startswith("set_valor_"):
            valor = float(data.replace("set_valor_", ""))
            state["config"]["valor"] = valor
            save_state(state)
            add_log(f"Valor manual alterado para {valor}")
            await query.edit_message_text(
                f"✅ Valor manual alterado para {format_brl(valor)}",
                reply_markup=config_menu(),
            )
            return

        if data.startswith("set_dca_"):
            valor = int(data.replace("set_dca_", ""))
            state["config"]["max_dca"] = valor
            save_state(state)
            add_log(f"Max DCA manual alterado para {valor}")
            await query.edit_message_text(
                f"✅ Max DCA manual alterado para {valor}",
                reply_markup=config_menu(),
            )
            return

        if data.startswith("set_cooldown_"):
            valor = int(data.replace("set_cooldown_", ""))
            state["config"]["cooldown"] = valor
            save_state(state)
            add_log(f"Cooldown manual alterado para {valor}s")
            await query.edit_message_text(
                f"✅ Cooldown manual alterado para {valor}s",
                reply_markup=config_menu(),
            )
            return

        await query.edit_message_text(
            "⚠️ Ação não reconhecida.",
            reply_markup=main_menu(),
        )

    except Exception as e:
        logger.exception("Erro no callback: %s", e)
        add_log(f"Erro callback {data}: {e}")
        await query.message.reply_text(
            f"❌ Erro ao processar o botão:\n{e}",
            reply_markup=main_menu(),
        )


# =========================================================
# MAIN
# =========================================================

def main() -> None:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN não definido.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(handle_callback))

    if app.job_queue is None:
        raise RuntimeError(
            "JobQueue não disponível. Confirme que o requirements.txt usa "
            'python-telegram-bot[job-queue]==21.6'
        )

    app.job_queue.run_repeating(bot_loop, interval=10, first=5)

    logger.info("BOT INICIADO")
    app.run_polling()


if __name__ == "__main__":
    main()
