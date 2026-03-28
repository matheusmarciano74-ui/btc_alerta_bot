import os
import math
import time
import threading
from datetime import datetime

import requests
from binance.client import Client
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ================= CONFIG =================
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SYMBOL = "BTCBRL"

client = Client(API_KEY, API_SECRET)

# ================= ESTADO =================
estado = {
    "auto": False,
    "modo": "SIMULADO",
    "queda": 1.2,
    "take": 2.0,
    "stop": 1.2,
    "valor": 50,
    "referencia": None,
    "posicoes": [],
    "precos": [],
    "ultima_minima": None,
    "aguardando": False,
}

# ================= UTILS =================
def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": msg},
    )

def preco():
    return float(client.get_symbol_ticker(symbol=SYMBOL)["price"])

def fmt(v):
    return f"R${v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def calc_qtd(valor, p):
    step = 0.00001
    return round(math.floor((valor / p) / step) * step, 5)

# ================= TAKE AUTO =================
def atualizar_precos(p):
    estado["precos"].append(p)
    if len(estado["precos"]) > 30:
        estado["precos"].pop(0)

def take_auto():
    if len(estado["precos"]) < 5:
        return estado["take"]

    maior = max(estado["precos"])
    menor = min(estado["precos"])

    vol = ((maior - menor) / menor) * 100
    return max(1.0, min(2.2, 1.0 + vol))

# ================= COMPRA =================
def compra_inteligente():
    p = preco()
    atualizar_precos(p)

    if estado["referencia"] is None:
        estado["referencia"] = p
        return

    if p > estado["referencia"] and not estado["aguardando"]:
        estado["referencia"] = p
        return

    queda = ((estado["referencia"] - p) / estado["referencia"]) * 100

    if queda >= estado["queda"]:
        estado["aguardando"] = True

        if estado["ultima_minima"] is None or p < estado["ultima_minima"]:
            estado["ultima_minima"] = p

    if estado["aguardando"] and estado["ultima_minima"]:
        subida = ((p - estado["ultima_minima"]) / estado["ultima_minima"]) * 100

        if subida >= 0.5:
            qtd = calc_qtd(estado["valor"], p)

            if estado["modo"] == "REAL":
                client.create_order(symbol=SYMBOL, side="BUY", type="MARKET", quantity=qtd)
            else:
                client.create_test_order(symbol=SYMBOL, side="BUY", type="MARKET", quantity=qtd)

            take = take_auto()

            pos = {
                "entrada": p,
                "qtd": qtd,
                "investido": estado["valor"],
                "alvo": p * (1 + take / 100),
                "stop": p * (1 - estado["stop"] / 100),
            }

            estado["posicoes"].append(pos)

            send(f"🟢 COMPRA\nPreço: {fmt(p)}\nTake: {take:.2f}%")

            estado["referencia"] = p
            estado["ultima_minima"] = None
            estado["aguardando"] = False

# ================= VENDA =================
def vendas():
    p = preco()
    novas = []

    for pos in estado["posicoes"]:
        if p >= pos["alvo"]:
            lucro = pos["qtd"] * p - pos["investido"]
            send(f"💰 TAKE\nLucro: {fmt(lucro)}")

        elif p <= pos["stop"]:
            lucro = pos["qtd"] * p - pos["investido"]
            send(f"🛑 STOP\nResultado: {fmt(lucro)}")

        else:
            novas.append(pos)

    estado["posicoes"] = novas

# ================= MONITOR =================
def monitor():
    while True:
        try:
            if estado["auto"]:
                compra_inteligente()
                vendas()
            time.sleep(5)
        except Exception as e:
            print("ERRO:", e)

# ================= MENU =================
def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Painel", callback_data="painel")],
        [InlineKeyboardButton("🤖 Auto ON/OFF", callback_data="auto")],
        [InlineKeyboardButton("💰 Zerar", callback_data="zerar")]
    ])

# ================= BOTÕES =================
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()

        print("CLICK:", query.data)

        if query.data == "painel":
            p = preco()
            await query.message.reply_text(
                f"📊 Preço: {fmt(p)}\nPosições: {len(estado['posicoes'])}"
            )

        elif query.data == "auto":
            estado["auto"] = not estado["auto"]
            await query.message.reply_text(f"Auto: {estado['auto']}")

        elif query.data == "zerar":
            estado["posicoes"] = []
            await query.message.reply_text("💰 Zerado")

    except Exception as e:
        print("ERRO BOTÃO:", e)

# ================= COMANDOS =================
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("MENU", reply_markup=menu())

# ================= MAIN =================
def main():
    threading.Thread(target=monitor, daemon=True).start()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(buttons))

    print("BOT ONLINE 🚀")
    app.run_polling()

if __name__ == "__main__":
    main()
