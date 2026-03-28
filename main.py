import os
import math
import time
import threading
import requests

from binance.client import Client
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
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

            print("COMPRA:", p)

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
            print("TAKE:", lucro)

        elif p <= pos["stop"]:
            lucro = pos["qtd"] * p - pos["investido"]
            print("STOP:", lucro)

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
def menu_principal():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Painel", callback_data="painel")],
        [InlineKeyboardButton("🤖 Auto ON/OFF", callback_data="auto")],
        [InlineKeyboardButton("💰 Comprar", callback_data="comprar")],
        [InlineKeyboardButton("⚙️ Config", callback_data="config")]
    ])

def menu_config():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 Queda", callback_data="set_queda")],
        [InlineKeyboardButton("🎯 Take", callback_data="set_take")],
        [InlineKeyboardButton("🛑 Stop", callback_data="set_stop")],
        [InlineKeyboardButton("💵 Valor", callback_data="set_valor")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="voltar")]
    ])

# ================= BOTÕES =================
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    print("CLICK:", data)

    if data == "painel":
        p = preco()
        await query.message.reply_text(
            f"📊 PAINEL\n"
            f"Preço: {fmt(p)}\n"
            f"Auto: {estado['auto']}\n"
            f"Queda: {estado['queda']}%\n"
            f"Take: {estado['take']}%\n"
            f"Valor: {fmt(estado['valor'])}\n"
            f"Posições: {len(estado['posicoes'])}"
        )

    elif data == "auto":
        estado["auto"] = not estado["auto"]
        await query.message.reply_text(f"🤖 Auto: {estado['auto']}")

    elif data == "comprar":
        context.user_data["modo"] = "comprar"
        await query.message.reply_text("Digite o valor em R$:")

    elif data == "config":
        await query.message.reply_text("⚙️ Configurações:", reply_markup=menu_config())

    elif data == "voltar":
        await query.message.reply_text("Menu:", reply_markup=menu_principal())

    elif data == "set_queda":
        context.user_data["modo"] = "queda"
        await query.message.reply_text("Digite nova queda (%):")

    elif data == "set_take":
        context.user_data["modo"] = "take"
        await query.message.reply_text("Digite novo take (%):")

    elif data == "set_stop":
        context.user_data["modo"] = "stop"
        await query.message.reply_text("Digite novo stop (%):")

    elif data == "set_valor":
        context.user_data["modo"] = "valor"
        await query.message.reply_text("Digite valor em R$:")

# ================= TEXTO =================
async def receber_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "modo" not in context.user_data:
        return

    try:
        valor = float(update.message.text.replace(",", "."))

        modo = context.user_data["modo"]

        if modo == "queda":
            estado["queda"] = valor

        elif modo == "take":
            estado["take"] = valor

        elif modo == "stop":
            estado["stop"] = valor

        elif modo == "valor":
            estado["valor"] = valor

        elif modo == "comprar":
            p = preco()
            qtd = calc_qtd(valor, p)

            client.create_test_order(
                symbol=SYMBOL,
                side="BUY",
                type="MARKET",
                quantity=qtd
            )

            await update.message.reply_text(f"✅ Compra simulada: {fmt(valor)}")

        context.user_data.pop("modo")

        await update.message.reply_text("Atualizado!", reply_markup=menu_principal())

    except:
        await update.message.reply_text("Valor inválido")

# ================= COMMAND =================
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Menu:", reply_markup=menu_principal())

# ================= MAIN =================
def main():
    threading.Thread(target=monitor, daemon=True).start()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_texto))

    print("BOT ONLINE 🚀")
    app.run_polling()

if __name__ == "__main__":
    main()
