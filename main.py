import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

import requests
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
STATE_FILE = "state.json"

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
        "modo": "SIMULADO",          # SIMULADO / REAL (nesta versão REAL é apenas modo visual)
        "rodando": False,
        "config": {
            "queda": 2.0,
            "lucro": 3.0,
            "valor": 100.0,
            "max_dca": 5,
            "cooldown": 600
        },
        "posicoes": [],
        "lucro_total": 0.0,
        "ultima_compra_ts": 0,
        "ultimo_preco": None,
        "preco_referencia": None,
        "ultimo_check": None,
        "logs": []
    }


def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        state = default_state()
        save_state(state)
        return state

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        base = default_state()
        base.update(state)

        if not isinstance(base.get("config"), dict):
            base["config"] = default_state()["config"]

        if not isinstance(base.get("posicoes"), list):
            base["posicoes"] = []

        if not isinstance(base.get("logs"), list):
            base["logs"] = []

        return base
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
    state["logs"] = state["logs"][-30:]
    save_state(state)


# =========================================================
# HELPERS
# =========================================================

def format_brl(v: float) -> str:
    s = f"{v:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def format_pct(v: float) -> str:
    return f"{v:.2f}%"


def get_price() -> float:
    r = requests.get(
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": "BTCBRL"},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return float(data["price"])


def open_positions() -> List[Dict[str, Any]]:
    return [p for p in state["posicoes"] if p.get("status") == "OPEN"]


def total_investido_aberto() -> float:
    return sum(float(p["valor"]) for p in open_positions())


def total_pnl_aberto(preco_atual: float | None) -> float:
    if preco_atual is None:
        return 0.0

    total = 0.0
    for p in open_positions():
        qtd = float(p["q"])
        valor_atual = qtd * preco_atual
        total += valor_atual - float(p["valor"])
    return total


def can_buy() -> bool:
    if len(open_positions()) >= int(state["config"]["max_dca"]):
        return False

    cooldown = int(state["config"]["cooldown"])
    last_buy = int(state.get("ultima_compra_ts", 0))
    now_ts = int(datetime.now().timestamp())
    return (now_ts - last_buy) >= cooldown


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
            InlineKeyboardButton("⚙️ Config", callback_data="config"),
            InlineKeyboardButton("🧾 Logs", callback_data="logs"),
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
    open_pos = open_positions()

    txt = [
        "📊 STATUS DO BOT",
        "",
        f"Modo: {state['modo']}",
        f"Rodando: {'✅ SIM' if state['rodando'] else '⛔ NÃO'}",
        f"Preço atual: {format_brl(float(preco)) if preco else '---'}",
        f"Referência: {format_brl(float(state['preco_referencia'])) if state.get('preco_referencia') else '---'}",
        f"Posições abertas: {len(open_pos)} / {int(state['config']['max_dca'])}",
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

    lines = ["📦 POSIÇÕES ABERTAS", ""]
    for i, p in enumerate(pos, start=1):
        lines.extend([
            f"{i}) Entrada: {format_brl(float(p['preco']))}",
            f"   Valor: {format_brl(float(p['valor']))}",
            f"   Qtd BTC: {float(p['q']):.8f}",
            f"   TP: {format_brl(float(p['tp']))}",
            "",
        ])
    return "\n".join(lines).strip()


def logs_text() -> str:
    logs = state.get("logs", [])
    if not logs:
        return "🧾 Sem logs ainda."
    return "🧾 ÚLTIMOS LOGS\n\n" + "\n".join(logs[-10:])


# =========================================================
# TRADING SIMULADO
# =========================================================

def buy_simulated(preco: float, motivo: str) -> None:
    valor = float(state["config"]["valor"])
    qtd = valor / preco
    lucro_pct = float(state["config"]["lucro"])
    tp = preco * (1 + lucro_pct / 100)

    state["posicoes"].append({
        "status": "OPEN",
        "preco": preco,
        "q": qtd,
        "valor": valor,
        "tp": tp,
        "opened_at": datetime.now().isoformat(timespec="seconds"),
        "motivo": motivo,
    })
    state["ultima_compra_ts"] = int(datetime.now().timestamp())
    save_state(state)
    add_log(f"Compra simulada em {format_brl(preco)} | motivo: {motivo}")


def sell_position_simulated(p: Dict[str, Any], preco_saida: float, motivo: str) -> float:
    qtd = float(p["q"])
    valor_entrada = float(p["valor"])
    valor_saida = qtd * preco_saida
    lucro = valor_saida - valor_entrada

    p["status"] = "CLOSED"
    p["closed_at"] = datetime.now().isoformat(timespec="seconds")
    p["preco_saida"] = preco_saida
    p["lucro"] = lucro
    p["sell_reason"] = motivo

    state["lucro_total"] = float(state["lucro_total"]) + lucro
    save_state(state)
    add_log(f"Venda simulada em {format_brl(preco_saida)} | lucro {format_brl(lucro)} | motivo: {motivo}")
    return lucro


# =========================================================
# BOT LOOP
# =========================================================

async def bot_loop(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        preco = get_price()
        state["ultimo_preco"] = preco
        state["ultimo_check"] = datetime.now().strftime("%d/%m %H:%M:%S")

        if state["preco_referencia"] is None:
            state["preco_referencia"] = preco

        if state["rodando"]:
            # Atualiza referência quando não há posição aberta e o preço sobe
            if len(open_positions()) == 0 and preco > float(state["preco_referencia"]):
                state["preco_referencia"] = preco

            # Compra automática por queda
            ref = float(state["preco_referencia"])
            queda_pct = float(state["config"]["queda"])
            preco_disparo = ref * (1 - queda_pct / 100)

            if preco <= preco_disparo and can_buy():
                buy_simulated(preco, "queda automática")
                state["preco_referencia"] = preco

                if CHAT_ID:
                    await context.bot.send_message(
                        chat_id=CHAT_ID,
                        text=(
                            "🟢 COMPRA AUTOMÁTICA\n"
                            f"Modo: {state['modo']}\n"
                            f"Preço: {format_brl(preco)}\n"
                            f"Valor: {format_brl(float(state['config']['valor']))}"
                        ),
                    )

            # Venda por take profit
            for p in list(open_positions()):
                if preco >= float(p["tp"]):
                    lucro = sell_position_simulated(p, preco, "take profit")

                    if CHAT_ID:
                        await context.bot.send_message(
                            chat_id=CHAT_ID,
                            text=(
                                "🔴 VENDA AUTOMÁTICA\n"
                                f"Modo: {state['modo']}\n"
                                f"Entrada: {format_brl(float(p['preco']))}\n"
                                f"Saída: {format_brl(preco)}\n"
                                f"Lucro: {format_brl(lucro)}"
                            ),
                        )

            # Se zerou tudo, redefine referência
            if len(open_positions()) == 0:
                state["preco_referencia"] = preco

        save_state(state)

    except Exception as e:
        add_log(f"Erro no loop: {e}")


# =========================================================
# COMMANDS
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
        # ===== menus principais =====
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
                reply_markup=main_menu(),
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
                "⚙️ CONFIGURAÇÃO",
                reply_markup=config_menu(),
            )
            return

        if data == "modo":
            state["modo"] = "REAL" if state["modo"] == "SIMULADO" else "SIMULADO"
            save_state(state)
            add_log(f"Modo alterado para {state['modo']}")
            await query.edit_message_text(
                f"🧠 Modo alterado para: {state['modo']}\n\nNesta versão o modo REAL ainda é apenas visual.",
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
            preco = get_price()
            state["ultimo_preco"] = preco
            buy_simulated(preco, "compra manual")
            await query.edit_message_text(
                f"🟢 Compra manual feita em {format_brl(preco)}.",
                reply_markup=main_menu(),
            )
            return

        if data == "sell_all":
            preco = get_price()
            abertas = list(open_positions())

            if not abertas:
                await query.edit_message_text(
                    "ℹ️ Não há posições abertas.",
                    reply_markup=main_menu(),
                )
                return

            lucro_total = 0.0
            for p in abertas:
                lucro_total += sell_position_simulated(p, preco, "venda manual geral")

            await query.edit_message_text(
                f"🔴 Todas as posições foram vendidas.\nLucro desta saída: {format_brl(lucro_total)}",
                reply_markup=main_menu(),
            )
            return

        # ===== submenus config =====
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

        # ===== setters =====
        if data.startswith("set_queda_"):
            valor = float(data.replace("set_queda_", ""))
            state["config"]["queda"] = valor
            save_state(state)
            add_log(f"Queda alterada para {valor}%")
            await query.edit_message_text(
                f"✅ Queda alterada para {valor}%",
                reply_markup=config_menu(),
            )
            return

        if data.startswith("set_lucro_"):
            valor = float(data.replace("set_lucro_", ""))
            state["config"]["lucro"] = valor

            # Atualiza TP das posições abertas
            for p in open_positions():
                entrada = float(p["preco"])
                p["tp"] = entrada * (1 + valor / 100)

            save_state(state)
            add_log(f"Lucro alterado para {valor}%")
            await query.edit_message_text(
                f"✅ Lucro alterado para {valor}%",
                reply_markup=config_menu(),
            )
            return

        if data.startswith("set_valor_"):
            valor = float(data.replace("set_valor_", ""))
            state["config"]["valor"] = valor
            save_state(state)
            add_log(f"Valor alterado para {valor}")
            await query.edit_message_text(
                f"✅ Valor alterado para {format_brl(valor)}",
                reply_markup=config_menu(),
            )
            return

        if data.startswith("set_dca_"):
            valor = int(data.replace("set_dca_", ""))
            state["config"]["max_dca"] = valor
            save_state(state)
            add_log(f"Max DCA alterado para {valor}")
            await query.edit_message_text(
                f"✅ Max DCA alterado para {valor}",
                reply_markup=config_menu(),
            )
            return

        if data.startswith("set_cooldown_"):
            valor = int(data.replace("set_cooldown_", ""))
            state["config"]["cooldown"] = valor
            save_state(state)
            add_log(f"Cooldown alterado para {valor}s")
            await query.edit_message_text(
                f"✅ Cooldown alterado para {valor}s",
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

    app.job_queue.run_repeating(bot_loop, interval=10, first=5)

    logger.info("BOT INICIADO")
    app.run_polling()


if __name__ == "__main__":
    main()
