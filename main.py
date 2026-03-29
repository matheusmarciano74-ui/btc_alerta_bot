import os
import time
import json
import requests
import pandas as pd
from ta.momentum import RSIIndicator
from binance.client import Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

client = Client(API_KEY, API_SECRET)

STATE_FILE = "state.json"

# ================= LOAD =================
def load():
    if not os.path.exists(STATE_FILE):
        return {
            "modo": "SIMULADO",
            "rodando": True,
            "config": {
                "queda": 2,
                "lucro": 3,
                "valor": 100,
                "max_dca": 5,
                "cooldown": 600
            },
            "posicoes": [],
            "lucro_total": 0,
            "ultima_compra": 0
        }
    return json.load(open(STATE_FILE))

def save():
    json.dump(state, open(STATE_FILE, "w"))

state = load()

# ================= TELEGRAM =================
def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Status", callback_data="status")],
        [InlineKeyboardButton("⚙️ Config", callback_data="config")],
        [InlineKeyboardButton("🧠 Modo: " + state["modo"], callback_data="modo")],
        [InlineKeyboardButton("▶️ Start", callback_data="start"),
         InlineKeyboardButton("⛔ Stop", callback_data="stop")]
    ])

async def send(msg, ctx):
    await ctx.bot.send_message(chat_id=CHAT_ID, text=msg)

# ================= PREÇO =================
def get_price():
    return float(client.get_symbol_ticker(symbol="BTCBRL")["price"])

def get_rsi():
    klines = client.get_klines(symbol="BTCBRL", interval="1m", limit=50)
    df = pd.DataFrame(klines)
    df[4] = df[4].astype(float)
    rsi = RSIIndicator(df[4]).rsi().iloc[-1]
    return rsi

# ================= COMPRA =================
def comprar(preco):
    valor = state["config"]["valor"]
    q = valor / preco

    if state["modo"] == "REAL":
        client.order_market_buy(symbol="BTCBRL", quoteOrderQty=valor)

    state["posicoes"].append({"preco": preco, "q": q})
    state["ultima_compra"] = time.time()

# ================= VENDA =================
def vender(p, preco):
    if state["modo"] == "REAL":
        client.order_market_sell(symbol="BTCBRL", quantity=p["q"])

    lucro = (preco - p["preco"]) * p["q"]
    state["lucro_total"] += lucro

# ================= IA =================
def entrada_inteligente(preco):
    rsi = get_rsi()

    if rsi < 30:
        return True
    return False

# ================= LOOP =================
async def loop(ctx: ContextTypes.DEFAULT_TYPE):
    while True:
        try:
            if state["rodando"]:
                preco = get_price()

                if entrada_inteligente(preco):
                    comprar(preco)

                novas = []
                for p in state["posicoes"]:
                    ganho = ((preco - p["preco"]) / p["preco"]) * 100
                    if ganho >= state["config"]["lucro"]:
                        vender(p, preco)
                    else:
                        novas.append(p)

                state["posicoes"] = novas
                print(preco)

            save()
            time.sleep(10)

        except Exception as e:
            print(e)
            time.sleep(5)

# ================= CALLBACK =================
async def click(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "modo":
        state["modo"] = "REAL" if state["modo"] == "SIMULADO" else "SIMULADO"
        await send(f"Modo alterado para {state['modo']}", ctx)

    elif q.data == "status":
        txt = f"""
Modo: {state['modo']}
Preço: {get_price()}
Posições: {len(state['posicoes'])}
Lucro: {state['lucro_total']}
"""
        await send(txt, ctx)

    elif q.data == "start":
        state["rodando"] = True
        await send("Bot ligado", ctx)

    elif q.data == "stop":
        state["rodando"] = False
        await send("Bot parado", ctx)

    save()

# ================= START =================
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("BOT PRO", reply_markup=menu())

# ================= MAIN =================
app = Application.builder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(click))

app.job_queue.run_repeating(loop, interval=10, first=5)

app.run_polling()
