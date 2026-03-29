import os
import time
import json
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

STATE_FILE = "state.json"

# ================= LOAD =================
def load():
    if not os.path.exists(STATE_FILE):
        return {
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
            "ultima_compra": 0,
            "esperando": None
        }
    return json.load(open(STATE_FILE))

def save():
    json.dump(state, open(STATE_FILE, "w"))

state = load()

# ================= PREÇO =================
def get_preco():
    r = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": "BTCBRL"})
    return float(r.json()["price"])

# ================= TELEGRAM =================
def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Status", callback_data="status")],
        [InlineKeyboardButton("⚙️ Config", callback_data="config")],
        [InlineKeyboardButton("▶️ Start", callback_data="start"),
         InlineKeyboardButton("⛔ Stop", callback_data="stop")]
    ])

def config_menu():
    c = state["config"]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Queda: {c['queda']}%", callback_data="set_queda")],
        [InlineKeyboardButton(f"Lucro: {c['lucro']}%", callback_data="set_lucro")],
        [InlineKeyboardButton(f"Valor: R${c['valor']}", callback_data="set_valor")],
        [InlineKeyboardButton(f"Max DCA: {c['max_dca']}", callback_data="set_dca")],
        [InlineKeyboardButton(f"Cooldown: {c['cooldown']}s", callback_data="set_cool")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="menu")]
    ])

async def send(msg, ctx):
    await ctx.bot.send_message(chat_id=CHAT_ID, text=msg)

# ================= DCA =================
def comprar(preco):
    q = state["config"]["valor"] / preco
    state["posicoes"].append({"preco": preco, "q": q})
    state["ultima_compra"] = time.time()

def vender(p, preco):
    lucro = (preco - p["preco"]) * p["q"]
    state["lucro_total"] += lucro

# ================= LOGICA =================
def verificar(preco):
    if not state["posicoes"]:
        comprar(preco)
        return

    ultima = state["posicoes"][-1]["preco"]
    queda = ((ultima - preco)/ultima)*100

    if (
        queda >= state["config"]["queda"]
        and len(state["posicoes"]) < state["config"]["max_dca"]
        and time.time() - state["ultima_compra"] > state["config"]["cooldown"]
    ):
        comprar(preco)

def vendas(preco):
    novas = []
    for p in state["posicoes"]:
        ganho = ((preco - p["preco"]) / p["preco"]) * 100
        if ganho >= state["config"]["lucro"]:
            vender(p, preco)
        else:
            novas.append(p)
    state["posicoes"] = novas

# ================= CALLBACK =================
async def click(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "menu":
        await q.message.reply_text("Menu", reply_markup=menu())

    elif q.data == "config":
        await q.message.reply_text("Configuração:", reply_markup=config_menu())

    elif q.data.startswith("set_"):
        campo = q.data.replace("set_", "")
        state["esperando"] = campo
        await send(f"Digite novo valor para {campo}", ctx)

    elif q.data == "start":
        state["rodando"] = True
        await send("Bot ligado", ctx)

    elif q.data == "stop":
        state["rodando"] = False
        await send("Bot parado", ctx)

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

# ================= STATUS =================
async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = f"""
Preço: {get_preco():.2f}
Posições: {len(state['posicoes'])}
Lucro: {state['lucro_total']:.2f}
"""
    await update.message.reply_text(txt, reply_markup=menu())

# ================= LOOP =================
async def loop(ctx: ContextTypes.DEFAULT_TYPE):
    while True:
        try:
            if state["rodando"]:
                preco = get_preco()
                verificar(preco)
                vendas(preco)
                print(preco)

            save()
            time.sleep(10)
        except:
            time.sleep(5)

# ================= MAIN =================
app = Application.builder().token(TOKEN).build()

app.add_handler(CommandHandler("start", status))
app.add_handler(CallbackQueryHandler(click))
app.add_handler(MessageHandler(filters.TEXT, receber))

app.job_queue.run_repeating(loop, interval=10, first=5)

app.run_polling()
