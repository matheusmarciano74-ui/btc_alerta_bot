import json
import os
import requests
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
from telegram import *
from telegram.ext import *
from binance.client import Client

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET)

STATE_FILE = "state.json"

def load():
    if not os.path.exists(STATE_FILE):
        return {
            "modo": "SIMULADO",
            "rodando": False,
            "config": {"queda": 2, "lucro": 3, "valor": 100, "max_dca": 5},
            "posicoes": [],
            "lucro_total": 0,
            "historico": []
        }
    return json.load(open(STATE_FILE))

def save():
    json.dump(state, open(STATE_FILE, "w"))

state = load()

# ================= PREÇO =================
def get_price():
    return float(requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCBRL").json()["price"])

# ================= COMPRA =================
def comprar(preco):
    valor = state["config"]["valor"]
    qtd = valor / preco

    if state["modo"] == "REAL":
        client.order_market_buy(symbol="BTCBRL", quoteOrderQty=valor)

    state["posicoes"].append({"preco": preco, "q": qtd})
    save()

# ================= VENDA =================
def vender(p, preco):
    if state["modo"] == "REAL":
        client.order_market_sell(symbol="BTCBRL", quantity=p["q"])

    lucro = (preco - p["preco"]) * p["q"]
    state["lucro_total"] += lucro
    state["historico"].append(state["lucro_total"])
    save()

# ================= GRAFICO BTC =================
def grafico_btc():
    klines = client.get_klines(symbol="BTCBRL", interval="5m", limit=100)
    df = pd.DataFrame(klines)
    df[4] = df[4].astype(float)

    plt.figure()
    plt.plot(df[4])
    plt.title("BTC/BRL")
    plt.savefig("btc.png")
    plt.close()

# ================= GRAFICO LUCRO =================
def grafico_lucro():
    plt.figure()
    plt.plot(state["historico"])
    plt.title("Lucro")
    plt.savefig("lucro.png")
    plt.close()

# ================= MENU =================
def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Status", callback_data="status")],
        [InlineKeyboardButton("📈 BTC", callback_data="btc")],
        [InlineKeyboardButton("💰 Lucro", callback_data="lucro")],
        [InlineKeyboardButton("⚙️ Config", callback_data="config")],
        [InlineKeyboardButton(f"🧠 {state['modo']}", callback_data="modo")],
        [InlineKeyboardButton("▶️ Start", callback_data="start"),
         InlineKeyboardButton("⛔ Stop", callback_data="stop")]
    ])

# ================= CALLBACK =================
async def click(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data

    if data == "status":
        await q.message.reply_text(f"""
Modo: {state['modo']}
Preço: {get_price()}
Lucro: {state['lucro_total']}
Posições: {len(state['posicoes'])}
""")

    elif data == "btc":
        grafico_btc()
        await ctx.bot.send_photo(chat_id=CHAT_ID, photo=open("btc.png","rb"))

    elif data == "lucro":
        grafico_lucro()
        await ctx.bot.send_photo(chat_id=CHAT_ID, photo=open("lucro.png","rb"))

    elif data == "modo":
        state["modo"] = "REAL" if state["modo"] == "SIMULADO" else "SIMULADO"
        save()
        await q.message.reply_text(f"Modo: {state['modo']}")

    elif data == "start":
        state["rodando"] = True
        save()
        await q.message.reply_text("Bot ON")

    elif data == "stop":
        state["rodando"] = False
        save()
        await q.message.reply_text("Bot OFF")

# ================= LOOP =================
async def loop(ctx):
    if not state["rodando"]:
        return

    preco = get_price()

    if len(state["posicoes"]) == 0:
        comprar(preco)

    novas = []
    for p in state["posicoes"]:
        ganho = ((preco - p["preco"]) / p["preco"]) * 100
        if ganho >= state["config"]["lucro"]:
            vender(p, preco)
        else:
            novas.append(p)

    state["posicoes"] = novas
    save()

# ================= START =================
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("BOT ONLINE", reply_markup=menu())

app = Application.builder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(click))

app.job_queue.run_repeating(loop, interval=10, first=5)

print("BOT PRO INICIADO")

app.run_polling()
