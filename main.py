# ===== NOVOS IMPORTS =====
import matplotlib.pyplot as plt
import pandas as pd
from binance.client import Client

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
client = Client(API_KEY, API_SECRET)

# =========================
# 🔥 ADICIONA NO MENU PRINCIPAL
# =========================

def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Status", callback_data="status"),
            InlineKeyboardButton("📦 Posições", callback_data="positions"),
        ],
        [
            InlineKeyboardButton("📈 BTC", callback_data="grafico_btc"),
            InlineKeyboardButton("💰 Lucro", callback_data="grafico_lucro"),
        ],
        [
            InlineKeyboardButton("⚙️ Config", callback_data="config"),
            InlineKeyboardButton("🧾 Logs", callback_data="logs"),
        ],
        [
            InlineKeyboardButton(f"🧠 Modo: {state['modo']}", callback_data="modo"),
        ],
        [
            InlineKeyboardButton("▶️ Start", callback_data="start"),
            InlineKeyboardButton("⛔ Stop", callback_data="stop"),
        ],
        [
            InlineKeyboardButton("🟢 Comprar", callback_data="buy"),
            InlineKeyboardButton("🔴 Vender tudo", callback_data="sell_all"),
        ],
    ])

# =========================
# 🔥 COMPRA (ATUALIZADA)
# =========================

def buy_simulated(preco, motivo):
    valor = float(state["config"]["valor"])
    qtd = valor / preco

    if state["modo"] == "REAL":
        try:
            client.order_market_buy(symbol="BTCBRL", quoteOrderQty=valor)
        except Exception as e:
            add_log(f"ERRO compra real: {e}")

    lucro_pct = float(state["config"]["lucro"])
    tp = preco * (1 + lucro_pct / 100)

    state["posicoes"].append({
        "status": "OPEN",
        "preco": preco,
        "q": qtd,
        "valor": valor,
        "tp": tp,
    })

    save_state(state)
    add_log(f"Compra em {preco}")

# =========================
# 🔥 VENDA (ATUALIZADA)
# =========================

def sell_position_simulated(p, preco_saida, motivo):
    if state["modo"] == "REAL":
        try:
            client.order_market_sell(symbol="BTCBRL", quantity=p["q"])
        except Exception as e:
            add_log(f"ERRO venda real: {e}")

    lucro = (preco_saida - p["preco"]) * p["q"]
    state["lucro_total"] += lucro

    # 🔥 guarda histórico pro gráfico
    if "historico" not in state:
        state["historico"] = []

    state["historico"].append(state["lucro_total"])

    p["status"] = "CLOSED"

    save_state(state)
    return lucro

# =========================
# 🔥 GRAFICO BTC
# =========================

def gerar_grafico_btc():
    klines = client.get_klines(symbol="BTCBRL", interval="5m", limit=100)
    df = pd.DataFrame(klines)
    df[4] = df[4].astype(float)

    plt.figure()
    plt.plot(df[4])
    plt.title("BTC/BRL")
    plt.savefig("btc.png")
    plt.close()

# =========================
# 🔥 GRAFICO LUCRO
# =========================

def gerar_grafico_lucro():
    if "historico" not in state or len(state["historico"]) == 0:
        return False

    plt.figure()
    plt.plot(state["historico"])
    plt.title("Lucro acumulado")
    plt.savefig("lucro.png")
    plt.close()
    return True

# =========================
# 🔥 CALLBACK (ADICIONAR ESSES CASOS)
# =========================

# dentro do handle_callback:

elif data == "grafico_btc":
    gerar_grafico_btc()
    await context.bot.send_photo(chat_id=CHAT_ID, photo=open("btc.png", "rb"))

elif data == "grafico_lucro":
    ok = gerar_grafico_lucro()
    if ok:
        await context.bot.send_photo(chat_id=CHAT_ID, photo=open("lucro.png", "rb"))
    else:
        await context.bot.send_message(chat_id=CHAT_ID, text="Sem histórico ainda")

# =========================
# 🔥 MODO (AJUSTE)
# =========================

elif data == "modo":
    state["modo"] = "REAL" if state["modo"] == "SIMULADO" else "SIMULADO"
    save_state(state)
    add_log(f"Modo: {state['modo']}")
    await query.edit_message_text(
        f"Modo alterado para: {state['modo']}",
        reply_markup=main_menu(),
    )
