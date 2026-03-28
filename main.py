# ================= IMPORTS =================
import os
import csv
import math
import time
import threading
from datetime import datetime

import requests
import matplotlib.pyplot as plt
from binance.client import Client
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

# ================= CONFIG =================
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SYMBOL = "BTCBRL"
TRADES_FILE = "historico_trades.csv"

client = Client(API_KEY, API_SECRET)

# ================= ESTADO =================
estado = {
    "modo": "SIMULADO",
    "auto": False,
    "queda": 1.2,
    "take": 2.0,
    "stop": 1.2,
    "valor": 50.0,
    "referencia": None,
    "posicoes": [],
    "max_posicoes": 3,
    "cooldown": 30,
    "ultimo_trade": 0,

    # TAKE AUTO
    "take_auto": True,
    "take_base": 1.0,
    "take_factor": 8,
    "take_min": 1.0,
    "take_max": 2.2,
    "precos": [],

    # NÍVEL 2
    "aguardando_reversao": False,
    "ultima_minima": None,
    "reversao_pct": 0.5,

    # MÉDIA
    "usar_media": True,
    "media": [],
    "periodo_media": 12,
}

lock = threading.RLock()

# ================= UTILS =================
def send(msg):
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  data={"chat_id": CHAT_ID, "text": msg})

def preco():
    return float(client.get_symbol_ticker(symbol=SYMBOL)["price"])

def calc_qtd(valor, p):
    step = 0.00001
    qtd = math.floor((valor / p) / step) * step
    return round(qtd, 5)

def fmt(v):
    return f"R${v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# ================= TAKE AUTO =================
def atualizar_precos(p):
    estado["precos"].append(p)
    if len(estado["precos"]) > 30:
        estado["precos"].pop(0)

def calcular_take():
    if len(estado["precos"]) < 5:
        return estado["take"]

    maior = max(estado["precos"])
    menor = min(estado["precos"])

    vol = ((maior - menor) / menor) * 100
    take = estado["take_base"] + (vol * estado["take_factor"] / 10)

    return max(estado["take_min"], min(take, estado["take_max"]))

# ================= MÉDIA =================
def atualizar_media(p):
    estado["media"].append(p)
    if len(estado["media"]) > estado["periodo_media"]:
        estado["media"].pop(0)

def media():
    if not estado["media"]:
        return None
    return sum(estado["media"]) / len(estado["media"])

# ================= COMPRA INTELIGENTE =================
def compra_nivel2():
    p = preco()
    atualizar_precos(p)
    atualizar_media(p)

    if estado["referencia"] is None:
        estado["referencia"] = p
        return

    if p > estado["referencia"] and not estado["aguardando_reversao"]:
        estado["referencia"] = p
        return

    queda = ((estado["referencia"] - p) / estado["referencia"]) * 100

    if queda >= estado["queda"]:
        estado["aguardando_reversao"] = True

        if estado["ultima_minima"] is None or p < estado["ultima_minima"]:
            estado["ultima_minima"] = p

    if estado["aguardando_reversao"] and estado["ultima_minima"]:
        subida = ((p - estado["ultima_minima"]) / estado["ultima_minima"]) * 100

        cond_media = True
        if estado["usar_media"]:
            m = media()
            if m:
                cond_media = p > m

        if subida >= estado["reversao_pct"] and cond_media:
            qtd = calc_qtd(estado["valor"], p)

            if estado["modo"] == "REAL":
                client.create_order(symbol=SYMBOL, side="BUY", type="MARKET", quantity=qtd)
            else:
                client.create_test_order(symbol=SYMBOL, side="BUY", type="MARKET", quantity=qtd)

            take = calcular_take() if estado["take_auto"] else estado["take"]

            pos = {
                "entrada": p,
                "qtd": qtd,
                "investido": estado["valor"],
                "alvo": p * (1 + take / 100),
                "stop": p * (1 - estado["stop"] / 100)
            }

            estado["posicoes"].append(pos)

            send(f"🟢 COMPRA INTELIGENTE\nPreço: {fmt(p)}\nTake: {take:.2f}%")

            estado["referencia"] = p
            estado["ultima_minima"] = None
            estado["aguardando_reversao"] = False

# ================= VENDAS =================
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
                compra_nivel2()
                vendas()
            time.sleep(5)
        except Exception as e:
            print(e)

# ================= MENU =================
def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Painel", callback_data="painel")],
        [InlineKeyboardButton("🤖 Auto", callback_data="auto")]
    ])

async def help_menu(update, context):
    await update.message.reply_text("Menu:", reply_markup=menu())

async def buttons(update, context):
    q = update.callback_query
    await q.answer()

    if q.data == "painel":
        p = preco()
        await q.message.reply_text(f"Preço: {fmt(p)}\nPosições: {len(estado['posicoes'])}")

    if q.data == "auto":
        estado["auto"] = not estado["auto"]
        await q.message.reply_text(f"Auto: {estado['auto']}")

# ================= MAIN =================
def main():
    threading.Thread(target=monitor, daemon=True).start()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("help", help_menu))
    app.add_handler(CallbackQueryHandler(buttons))

    print("BOT ONLINE 🚀")
    app.run_polling()

if __name__ == "__main__":
    main()
