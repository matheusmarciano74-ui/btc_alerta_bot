# ================= IMPORTS =================
import os
import math
import time
import threading
from datetime import datetime

import requests
from binance.client import Client
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ================= CONFIG =================
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SYMBOL = "BTCBRL"

client = Client(API_KEY, API_SECRET)

# ================= ESTADO =================
estado = {
    "modo": "SIMULADO",
    "auto": True,
    "queda": 1.0,
    "take": 1.3,
    "stop": 1.0,
    "valor": 200,
    "referencia": None,
    "posicoes": [],
    "precos": [],
    "ultima_minima": None,
    "aguardando_reversao": False,
    "lucro_total": 0,
}

# ================= UTILS =================
def preco():
    return float(client.get_symbol_ticker(symbol=SYMBOL)["price"])

def fmt(v):
    return f"R${v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def calc_qtd(valor, p):
    step = 0.00001
    return round(math.floor((valor / p) / step) * step, 5)

def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": msg},
    )

# ================= NOVA ESTRATÉGIA =================
def estrategia_nova():
    p = preco()

    # histórico de preços
    estado["precos"].append(p)
    if len(estado["precos"]) > 30:
        estado["precos"].pop(0)

    # referência inicial
    if estado["referencia"] is None:
        estado["referencia"] = p
        return

    # atualiza topo
    if p > estado["referencia"] and not estado["aguardando_reversao"]:
        estado["referencia"] = p
        return

    # calcula queda
    queda = ((estado["referencia"] - p) / estado["referencia"]) * 100

    if queda >= estado["queda"]:
        estado["aguardando_reversao"] = True

        if estado["ultima_minima"] is None or p < estado["ultima_minima"]:
            estado["ultima_minima"] = p

    # reversão
    if estado["aguardando_reversao"] and estado["ultima_minima"]:

        subida = ((p - estado["ultima_minima"]) / estado["ultima_minima"]) * 100

        # volatilidade
        if len(estado["precos"]) > 10:
            maior = max(estado["precos"])
            menor = min(estado["precos"])
            vol = ((maior - menor) / menor) * 100
        else:
            vol = 1

        # tendência simples
        if len(estado["precos"]) >= 10:
            media_curta = sum(estado["precos"][-5:]) / 5
            media_longa = sum(estado["precos"][-10:]) / 10
            tendencia_ok = media_curta >= media_longa
        else:
            tendencia_ok = True

        if subida >= 0.4 and vol > 0.3 and tendencia_ok:

            qtd = calc_qtd(estado["valor"], p)

            if estado["modo"] == "REAL":
                client.create_order(symbol=SYMBOL, side="BUY", type="MARKET", quantity=qtd)
            else:
                client.create_test_order(symbol=SYMBOL, side="BUY", type="MARKET", quantity=qtd)

            # TAKE DINÂMICO
            take = max(1.0, min(2.2, 1.0 + vol))

            pos = {
                "entrada": p,
                "qtd": qtd,
                "investido": estado["valor"],
                "alvo": p * (1 + take / 100),
                "stop": p * (1 - estado["stop"] / 100),
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
            estado["lucro_total"] += lucro
            send(f"💰 TAKE\nLucro: {fmt(lucro)}")

        elif p <= pos["stop"]:
            lucro = pos["qtd"] * p - pos["investido"]
            estado["lucro_total"] += lucro
            send(f"🛑 STOP\nResultado: {fmt(lucro)}")

        else:
            novas.append(pos)

    estado["posicoes"] = novas

# ================= MONITOR =================
def monitor():
    while True:
        try:
            if estado["auto"]:
                estrategia_nova()
                vendas()
            time.sleep(5)
        except Exception as e:
            print("ERRO:", e)

# ================= PAINEL =================
async def painel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p = preco()

    await update.message.reply_text(
        f"📊 PAINEL\n\n"
        f"Modo: {estado['modo']}\n"
        f"Auto: {estado['auto']}\n"
        f"Par: BTCBRL\n"
        f"Preço atual: {fmt(p)}\n"
        f"Referência: {fmt(estado['referencia'] or p)}\n\n"
        f"Queda compra: {estado['queda']}%\n"
        f"Take individual: {estado['take']}%\n"
        f"Stop individual: {estado['stop']}%\n"
        f"Valor por ordem: {fmt(estado['valor'])}\n\n"
        f"Posições abertas: {len(estado['posicoes'])}\n"
        f"Lucro total: {fmt(estado['lucro_total'])}"
    )

# ================= MAIN =================
def main():
    threading.Thread(target=monitor, daemon=True).start()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("painel", painel))

    print("BOT ONLINE 🚀")
    app.run_polling()

if __name__ == "__main__":
    main()
