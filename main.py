import os
import time
import threading
import requests
from datetime import datetime

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
    "estrategia": "AUTO",

    "valor": 200,
    "take": 1.2,
    "stop": -7,
    "queda": 2,
    "max_niveis": 5,

    "historico": [],
    "historico_pnl": [],

    "lucro_total": 0,
    "trades": 0,
    "wins": 0,

    # DCA
    "dca_ativo": False,
    "dca_qtd": 0,
    "dca_total": 0,
    "dca_niveis": 0,
    "dca_preco_base": 0,
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
        [InlineKeyboardButton("⚙️ Config", callback_data="config")],
        [InlineKeyboardButton("🤖 Auto ON/OFF", callback_data="auto")],
        [InlineKeyboardButton("🧠 Estratégia", callback_data="estrategia")],
    ])


def menu_config():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Valor", callback_data="cfg_valor")],
        [InlineKeyboardButton("🎯 Take", callback_data="cfg_take")],
        [InlineKeyboardButton("📉 Stop", callback_data="cfg_stop")],
        [InlineKeyboardButton("📊 Queda", callback_data="cfg_queda")],
    ])


def menu_estrategia():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("AUTO", callback_data="auto_mode")],
        [InlineKeyboardButton("REVERSÃO", callback_data="rev")],
        [InlineKeyboardButton("DCA", callback_data="dca")],
    ])

# ================= BOTÕES =================
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "painel":
        await q.message.reply_text(painel())

    elif data == "config":
        await q.message.reply_text("Configurações:", reply_markup=menu_config())

    elif data == "auto":
        estado["auto"] = not estado["auto"]
        await q.message.reply_text(f"Auto: {estado['auto']}")

    elif data == "estrategia":
        await q.message.reply_text("Escolha:", reply_markup=menu_estrategia())

    elif data == "auto_mode":
        estado["estrategia"] = "AUTO"
        await q.message.reply_text("🤖 AUTO ativado")

    elif data == "rev":
        estado["estrategia"] = "REVERSAO"
        await q.message.reply_text("🔄 Reversão ativa")

    elif data == "dca":
        estado["estrategia"] = "DCA"
        estado["dca_ativo"] = False
        await q.message.reply_text("💰 DCA ativado")

    elif data == "cfg_valor":
        estado["valor"] += 50
        await q.message.reply_text(f"Valor: {estado['valor']}")

    elif data == "cfg_take":
        estado["take"] += 0.2
        await q.message.reply_text(f"Take: {estado['take']}%")

    elif data == "cfg_stop":
        estado["stop"] -= 1
        await q.message.reply_text(f"Stop: {estado['stop']}%")

    elif data == "cfg_queda":
        estado["queda"] += 0.5
        await q.message.reply_text(f"Queda: {estado['queda']}%")


# ================= PAINEL =================
def painel():
    p = preco()

    media = 0
    if estado["dca_qtd"] > 0:
        media = estado["dca_total"] / estado["dca_qtd"]

    winrate = (estado["wins"] / estado["trades"] * 100) if estado["trades"] else 0

    return f"""
📊 PAINEL

Auto: {estado['auto']}
Estratégia: {estado['estrategia']}
Preço: {round(p,2)}

💰 Lucro: R${round(estado['lucro_total'],2)}
📈 Trades: {estado['trades']}
🎯 Winrate: {round(winrate,2)}%

🧠 DCA:
Ativo: {estado['dca_ativo']}
Níveis: {estado['dca_niveis']}
Preço médio: {round(media,2) if media else "-"}
"""

# ================= ESTRATÉGIA AUTO =================
def escolher_estrategia():
    if len(estado["historico"]) < 10:
        return "DCA"

    vol = (max(estado["historico"]) - min(estado["historico"])) / min(estado["historico"]) * 100

    if vol > 1.5:
        return "DCA"
    return "REVERSAO"

# ================= DCA PROFISSIONAL =================
def dca():
    p = preco()

    TAXA = 0.001

    estado["historico"].append(p)
    if len(estado["historico"]) > 50:
        estado["historico"].pop(0)

    if not estado["dca_ativo"]:
        qtd = (estado["valor"] * (1 - TAXA)) / p

        estado["dca_ativo"] = True
        estado["dca_qtd"] = qtd
        estado["dca_total"] = estado["valor"]
        estado["dca_niveis"] = 1
        estado["dca_preco_base"] = p

        send(f"🟢 DCA INÍCIO {p}")
        return

    media = estado["dca_total"] / estado["dca_qtd"]

    queda = (estado["dca_preco_base"] - p) / estado["dca_preco_base"] * 100

    if queda >= estado["queda"] and estado["dca_niveis"] < estado["max_niveis"]:
        qtd = (estado["valor"] * (1 - TAXA)) / p

        estado["dca_qtd"] += qtd
        estado["dca_total"] += estado["valor"]
        estado["dca_niveis"] += 1
        estado["dca_preco_base"] = p

        send("📉 DCA MÉDIA")

    valor_final = estado["dca_qtd"] * p * (1 - TAXA)
    lucro = valor_final - estado["dca_total"]
    lucro_pct = (lucro / estado["dca_total"]) * 100

    if lucro_pct >= estado["take"]:
        estado["lucro_total"] += lucro
        estado["trades"] += 1
        if lucro > 0:
            estado["wins"] += 1

        send(f"💰 VENDA | lucro R${round(lucro,2)} ({round(lucro_pct,2)}%)")

        estado["dca_ativo"] = False
        estado["dca_qtd"] = 0
        estado["dca_total"] = 0
        estado["dca_niveis"] = 0

    if lucro_pct <= estado["stop"]:
        send("🚨 STOP")

        estado["trades"] += 1

        estado["dca_ativo"] = False
        estado["dca_qtd"] = 0
        estado["dca_total"] = 0
        estado["dca_niveis"] = 0


# ================= LOOP =================
def loop():
    while True:
        try:
            if estado["auto"]:
                modo = estado["estrategia"]

                if modo == "AUTO":
                    modo = escolher_estrategia()

                if modo == "DCA":
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
