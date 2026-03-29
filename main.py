import os
import time
import threading
from datetime import datetime

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from binance.client import Client

# ================= CONFIG =================
TOKEN = os.getenv("TOKEN")
CHAT_ID = str(os.getenv("CHAT_ID"))

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")

client = Client(API_KEY, API_SECRET)

# ================= ESTADO =================
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

    # histórico
    "historico": []
}

# ================= UTILS =================
def preco():
    return float(client.get_symbol_ticker(symbol="BTCBRL")["price"])


def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": msg},
    )


# ================= MENU =================
def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Painel", callback_data="painel")],
        [InlineKeyboardButton("🤖 Auto ON/OFF", callback_data="auto")],
        [InlineKeyboardButton("🧠 Estratégia", callback_data="estrategia")],
    ])


def menu_estrategia():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Reversão", callback_data="rev")],
        [InlineKeyboardButton("💰 DCA Inteligente", callback_data="dca")],
    ])


# ================= BOTÕES =================
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
        await query.message.reply_text("💰 DCA Inteligente ativado")


# ================= PAINEL =================
def painel():
    p = preco()

    media = 0
    if estado["dca_qtd"] > 0:
        media = estado["dca_total"] / estado["dca_qtd"]

    return f"""
📊 PAINEL

Auto: {estado['auto']}
Estratégia: {estado['estrategia']}
Preço: {round(p,2)}

🧠 DCA:
Ativo: {estado['dca_ativo']}
Níveis: {estado['dca_niveis']}
Preço médio: {round(media,2) if media else "-"}
"""


# ================= DCA INTELIGENTE =================
def dca():
    p = preco()

    estado["historico"].append(p)
    if len(estado["historico"]) > 20:
        estado["historico"].pop(0)

    # ================= TENDÊNCIA =================
    tendencia_ok = True

    if len(estado["historico"]) >= 10:
        media_curta = sum(estado["historico"][-5:]) / 5
        media_longa = sum(estado["historico"][-10:]) / 10

        if media_curta < media_longa:
            tendencia_ok = False

    # ================= INICIO =================
    if not estado["dca_ativo"]:

        if not tendencia_ok:
            print("⛔ Evitando compra em queda forte")
            return

        estado["dca_ativo"] = True
        estado["dca_preco_base"] = p
        estado["dca_qtd"] = 1
        estado["dca_total"] = estado["valor"]
        estado["dca_niveis"] = 1

        send(f"🟢 DCA INÍCIO {p}")
        return

    media = estado["dca_total"] / estado["dca_qtd"]

    # ================= VOLATILIDADE =================
    maior = max(estado["historico"])
    menor = min(estado["historico"])

    volatilidade = ((maior - menor) / menor) * 100

    distancia = max(1.5, min(3.0, volatilidade))

    queda = ((estado["dca_preco_base"] - p) / estado["dca_preco_base"]) * 100

    # ================= MÉDIA =================
    if (
        queda >= distancia
        and estado["dca_niveis"] < 3
        and tendencia_ok
    ):
        estado["dca_qtd"] += 1
        estado["dca_total"] += estado["valor"]
        estado["dca_niveis"] += 1
        estado["dca_preco_base"] = p

        send(f"📉 DCA MÉDIA {estado['dca_niveis']} | {round(p,2)}")

    # ================= TAKE =================
    take = max(1.0, min(2.2, volatilidade))

    lucro = ((p - media) / media) * 100

    if lucro >= take:
        send(f"💰 VENDA DCA | lucro {round(lucro,2)}%")

        estado["dca_ativo"] = False
        estado["dca_total"] = 0
        estado["dca_qtd"] = 0

    # ================= STOP =================
    perda = ((p - media) / media) * 100

    if perda <= -6:
        send("🚨 STOP GLOBAL")

        estado["dca_ativo"] = False
        estado["dca_total"] = 0
        estado["dca_qtd"] = 0


# ================= LOOP =================
def loop():
    while True:
        try:
            if estado["auto"]:

                if estado["estrategia"] == "DCA":
                    dca()

            time.sleep(5)

        except Exception as e:
            print("erro:", e)


# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 MENU", reply_markup=menu())


# ================= MAIN =================
def main():
    threading.Thread(target=loop, daemon=True).start()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))

    app.run_polling()


if __name__ == "__main__":
    main()
