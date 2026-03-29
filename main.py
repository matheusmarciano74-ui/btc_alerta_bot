# =========================
# IMPORTS
# =========================
import os
import time
import math
import threading
from datetime import datetime

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from binance.client import Client

# =========================
# CONFIG
# =========================
TOKEN = os.getenv("TOKEN")
CHAT_ID = str(os.getenv("CHAT_ID"))

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")

SYMBOL = "BTCBRL"

client = Client(API_KEY, API_SECRET)

# =========================
# ESTADO
# =========================
estado = {
    "auto": True,
    "modo": "SIMULADO",
    "estrategia": "REVERSAO",

    "valor_auto": 200,
    "max_posicoes": 3,
    "cooldown": 60,

    "posicoes": [],

    # ===== DCA =====
    "dca_ativo": False,
    "dca_preco_base": None,
    "dca_total_investido": 0,
    "dca_total_qtd": 0,
    "dca_niveis": 0,

    "dca_max_niveis": 3,
    "dca_distancia": 2.0,
    "dca_take": 1.5,
    "dca_stop": 7.0,
}

lock = threading.Lock()

# =========================
# UTILS
# =========================
def autorizado(update):
    return True  # depois você trava com chat_id


def preco():
    return float(client.get_symbol_ticker(symbol=SYMBOL)["price"])


def fmt(v):
    return f"R${v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def calc_qtd(valor, p):
    return round(valor / p, 6)


def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": msg},
    )

# =========================
# MENU
# =========================
def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Painel", callback_data="painel")],
        [InlineKeyboardButton("💰 Comprar", callback_data="buy")],
        [InlineKeyboardButton("🤖 Auto ON/OFF", callback_data="auto")],
        [InlineKeyboardButton("📜 Histórico", callback_data="hist")],
        [InlineKeyboardButton("⚙️ Config", callback_data="config")],
        [InlineKeyboardButton("🧠 Estratégia", callback_data="estrategia")],
    ])


def menu_estrategia():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Scalping", callback_data="est_scalp")],
        [InlineKeyboardButton("🔄 Reversão", callback_data="est_rev")],
        [InlineKeyboardButton("📈 Tendência", callback_data="est_trend")],
        [InlineKeyboardButton("💰 DCA", callback_data="est_dca")],
    ])

# =========================
# PAINEL
# =========================
def painel():
    p = preco()
    return (
        "📊 PAINEL\n\n"
        f"Auto: {estado['auto']}\n"
        f"Estratégia: {estado['estrategia']}\n"
        f"Preço: {fmt(p)}\n"
        f"Posições: {len(estado['posicoes'])}\n"
        f"DCA ativo: {estado['dca_ativo']}"
    )

# =========================
# DCA LOGIC
# =========================
def dca_logic():
    p = preco()

    if not estado["dca_ativo"]:
        valor = estado["valor_auto"]
        qtd = calc_qtd(valor, p)

        estado["dca_ativo"] = True
        estado["dca_preco_base"] = p
        estado["dca_total_investido"] = valor
        estado["dca_total_qtd"] = qtd
        estado["dca_niveis"] = 1

        send(f"🟢 DCA INÍCIO {fmt(p)}")
        return

    media = estado["dca_total_investido"] / estado["dca_total_qtd"]

    queda = ((estado["dca_preco_base"] - p) / estado["dca_preco_base"]) * 100

    if queda >= estado["dca_distancia"] and estado["dca_niveis"] < estado["dca_max_niveis"]:
        valor = estado["valor_auto"]
        qtd = calc_qtd(valor, p)

        estado["dca_total_investido"] += valor
        estado["dca_total_qtd"] += qtd
        estado["dca_niveis"] += 1
        estado["dca_preco_base"] = p

        send(f"📉 DCA MÉDIA {estado['dca_niveis']}")

    lucro = ((p - media) / media) * 100

    if lucro >= estado["dca_take"]:
        send(f"💰 DCA VENDA lucro {lucro:.2f}%")

        estado["dca_ativo"] = False
        estado["dca_total_qtd"] = 0
        estado["dca_total_investido"] = 0

    perda = ((p - media) / media) * 100

    if perda <= -estado["dca_stop"]:
        send("🚨 STOP GLOBAL DCA")

        estado["dca_ativo"] = False
        estado["dca_total_qtd"] = 0
        estado["dca_total_investido"] = 0

# =========================
# LOOP
# =========================
def loop():
    while True:
        try:
            if estado["auto"]:

                if estado["estrategia"] == "DCA":
                    dca_logic()

            time.sleep(5)

        except Exception as e:
            print("erro:", e)

# =========================
# BOTÕES
# =========================
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data

    if data == "painel":
        await q.message.reply_text(painel())

    elif data == "auto":
        estado["auto"] = not estado["auto"]
        await q.message.reply_text(f"Auto: {estado['auto']}")

    elif data == "estrategia":
        await q.message.reply_text("Escolha:", reply_markup=menu_estrategia())

    elif data == "est_dca":
        estado["estrategia"] = "DCA"
        await q.message.reply_text("💰 DCA ativado")

# =========================
# COMMANDS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update): return
    await update.message.reply_text("🚀 MENU", reply_markup=menu())


# =========================
# MAIN
# =========================
def main():
    threading.Thread(target=loop, daemon=True).start()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))

    app.run_polling()


if __name__ == "__main__":
    main()
