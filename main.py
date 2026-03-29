import os
import json
import asyncio
import requests
import pandas as pd
from ta.momentum import RSIIndicator
from binance.client import Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ================= CONFIG =================
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
            "rodando": False,
            "config": {
                "queda": 2,
                "lucro": 3,
                "valor": 100,
                "max_dca": 5,
                "cooldown": 600
            },
            "posicoes": [],
            "lucro_total": 0,
            "ultima_compra": 0,
            "esperando": None
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
        [InlineKeyboardButton(f"🧠 Modo: {state['modo']}", callback_data="modo")],
        [InlineKeyboardButton("▶️ Start", callback_data="start"),
         InlineKeyboardButton("⛔ Stop", callback_data="stop")],
        [InlineKeyboardButton("🟢 Comprar", callback_data="buy"),
         InlineKeyboardButton("🔴 Vender tudo", callback_data="sell")]
    ])

def config_menu():
    c = state["config"]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Queda: {c['queda']}%", callback_data="set_queda")],
        [InlineKeyboardButton(f"Lucro: {c['lucro']}%", callback_data="set_lucro")],
        [InlineKeyboardButton(f"Valor: {c['valor']}", callback_data="set_valor")],
        [InlineKeyboardButton(f"DCA: {c['max_dca']}", callback_data="set_dca")],
        [InlineKeyboardButton(f"Cooldown: {c['cooldown']}", callback_data="set_cool")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="menu")]
    ])

async def send(msg, ctx):
    await ctx.bot.send_message(chat_id=CHAT_ID, text=msg)

# ================= BINANCE =================
def get_price():
    return float(client.get_symbol_ticker(symbol="BTCBRL")["price"])

def get_rsi():
    klines = client.get_klines(symbol="BTCBRL", interval="1m", limit=50)
    df = pd.DataFrame(klines)
    df[4] = df[4].astype(float)
    rsi = RSIIndicator(df[4]).rsi().iloc[-1]
    return rsi

# ================= TRADING =================
def comprar(preco):
    valor = state["config"]["valor"]
    q = valor / preco

    if state["modo"] == "REAL":
        client.order_market_buy(symbol="BTCBRL", quoteOrderQty=valor)

    state["posicoes"].append({"preco": preco, "q": q})
    state["ultima_compra"] = asyncio.get_event_loop().time()

def vender(p, preco):
    if state["modo"] == "REAL":
        client.order_market_sell(symbol="BTCBRL", quantity=p["q"])

    lucro = (preco - p["preco"]) * p["q"]
    state["lucro_total"] += lucro

# ================= IA =================
def entrada_inteligente(preco):
    rsi = get_rsi()
    return rsi < 30

# ================= LOOP =================
async def loop(context: ContextTypes.DEFAULT_TYPE):
    try:
        if state["rodando"]:
            preco = get_price()

            if entrada_inteligente(preco):
                if len(state["posicoes"]) < state["config"]["max_dca"]:
                    comprar(preco)

            novas = []
            for p in state["posicoes"]:
                ganho = ((preco - p["preco"]) / p["preco"]) * 100
                if ganho >= state["config"]["lucro"]:
                    vender(p, preco)
                else:
                    novas.append(p)

            state["posicoes"] = novas
            print(f"Preço: {preco}")

        save()

    except Exception as e:
        print("ERRO:", e)

# ================= CALLBACK =================
async def click(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "menu":
        await query.message.reply_text("Menu", reply_markup=menu())

    elif data == "status":
        txt = f"""
Modo: {state['modo']}
Preço: {get_price()}
Posições: {len(state['posicoes'])}
Lucro: {state['lucro_total']}
"""
        await send(txt, ctx)

    elif data == "config":
        await query.message.reply_text("Config:", reply_markup=config_menu())

    elif data == "modo":
        state["modo"] = "REAL" if state["modo"] == "SIMULADO" else "SIMULADO"
        await send(f"Modo: {state['modo']}", ctx)

    elif data == "start":
        state["rodando"] = True
        await send("Bot ligado", ctx)

    elif data == "stop":
        state["rodando"] = False
        await send("Bot parado", ctx)

    elif data == "buy":
        preco = get_price()
        comprar(preco)
        await send("Compra manual", ctx)

    elif data == "sell":
        preco = get_price()
        for p in state["posicoes"]:
            vender(p, preco)
        state["posicoes"] = []
        await send("Vendeu tudo", ctx)

    elif data.startswith("set_"):
        campo = data.replace("set_", "")
        state["esperando"] = campo
        await send(f"Digite novo valor para {campo}", ctx)

    save()

# ================= INPUT =================
async def receber(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not state["esperando"]:
        return

    campo = state["esperando"]
    valor = float(update.message.text)

    if campo == "queda":
        state["config"]["queda"] = valor
    elif campo == "lucro":
        state["config"]["lucro"] = valor
    elif campo == "valor":
        state["config"]["valor"] = valor
    elif campo == "dca":
        state["config"]["max_dca"] = int(valor)
    elif campo == "cool":
        state["config"]["cooldown"] = int(valor)

    state["esperando"] = None

    await update.message.reply_text("Atualizado!", reply_markup=config_menu())
    save()

# ================= START =================
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("BOT ONLINE", reply_markup=menu())

# ================= MAIN =================
app = Application.builder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(click))
app.add_handler(MessageHandler(filters.TEXT, receber))

app.job_queue.run_repeating(loop, interval=10, first=5)

print("BOT INICIADO")

app.run_polling()
