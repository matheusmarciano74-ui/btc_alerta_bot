import os
import time
import threading
from datetime import datetime

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from binance.client import Client

TOKEN = os.getenv("TOKEN")
CHAT_ID = str(os.getenv("CHAT_ID"))

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")

client = Client(API_KEY, API_SECRET)

estado = {
    "auto": True,
    "estrategia": "REVERSAO",
    "valor": 200,

    # DCA
    "dca_ativo": False,
    "dca_preco_base": 0,
    "dca_qtd": 0,
    "dca_total": 0,
    "dca_niveis": 0,
}

# ========================
# UTILS
# ========================

def preco():
    return float(client.get_symbol_ticker(symbol="BTCBRL")["price"])


def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": msg},
    )


# ========================
# MENU
# ========================

def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Painel", callback_data="painel")],
        [InlineKeyboardButton("💰 Comprar", callback_data="comprar")],
        [InlineKeyboardButton("🤖 Auto ON/OFF", callback_data="auto")],
        [InlineKeyboardButton("🧠 Estratégia", callback_data="estrategia")],
    ])


def menu_estrategia():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Reversão", callback_data="rev")],
        [InlineKeyboardButton("💰 DCA", callback_data="dca")],
    ])


# ========================
# BOTÕES
# ========================

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "painel":
        await query.message.reply_text(painel())

    elif data == "auto":
        estado["auto"] = not estado["auto"]
        await query.message.reply_text(f"Auto: {estado['auto']}")

    elif data == "estrategia":
        await query.message.reply_text("Escolha:", reply_markup=menu_estrategia())

    elif data == "rev":
        estado["estrategia"] = "REVERSAO"
        await query.message.reply_text("🔄 Reversão ativa")

    elif data == "dca":
        estado["estrategia"] = "DCA"
        estado["dca_ativo"] = False
        await query.message.reply_text("💰 DCA ativo")


# ========================
# PAINEL
# ========================

def painel():
    return f"""
📊 PAINEL

Auto: {estado['auto']}
Estratégia: {estado['estrategia']}
Preço: {preco()}
DCA ativo: {estado['dca_ativo']}
"""


# ========================
# DCA
# ========================

def dca():
    p = preco()

    if not estado["dca_ativo"]:
        estado["dca_ativo"] = True
        estado["dca_preco_base"] = p
        estado["dca_qtd"] = 1
        estado["dca_total"] = estado["valor"]
        estado["dca_niveis"] = 1

        send(f"🟢 DCA INICIO {p}")
        return

    media = estado["dca_total"] / estado["dca_qtd"]

    queda = ((estado["dca_preco_base"] - p) / estado["dca_preco_base"]) * 100

    if queda >= 2 and estado["dca_niveis"] < 3:
        estado["dca_qtd"] += 1
        estado["dca_total"] += estado["valor"]
        estado["dca_niveis"] += 1
        estado["dca_preco_base"] = p

        send("📉 DCA MEDIA")

    lucro = ((p - media) / media) * 100

    if lucro >= 1.5:
        send("💰 VENDA DCA")
        estado["dca_ativo"] = False


# ========================
# LOOP
# ========================

def loop():
    while True:
        if estado["auto"]:

            if estado["estrategia"] == "DCA":
                dca()

        time.sleep(5)


# ========================
# START
# ========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 MENU", reply_markup=menu())


# ========================
# MAIN
# ========================

def main():
    threading.Thread(target=loop, daemon=True).start()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))

    app.run_polling()


if __name__ == "__main__":
    main()
