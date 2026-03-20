import csv
import math
import os
import threading
import time
from datetime import datetime

import matplotlib.pyplot as plt
import requests
from binance.client import Client
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ================= CONFIG =================
API_KEY = "hAt9bS0o0qVMzeEe4ksDRzbBKmFSUmJla7lhsFk9BLVS3fgyBS9ZacfPeiy0JFZy"
API_SECRET = "MxyH603vy9IC19XXjVoOTWbvWaCVcUttNi1S95gsTxJDAU0Irhn2DJrBmsrCCQfJ"
TOKEN = "8431647250:AAECG78Fy4xLJgmz-DQtwgKOMw699a4GPJs"
CHAT_ID = "8330721663"

SYMBOL = "BTCBRL"
STATE_FILE = "estado_bot_ui.json"
TRADES_FILE = "historico_trades.csv"

client = Client(API_KEY, API_SECRET)

estado = {
    "modo": "SIMULADO",      # SIMULADO | REAL
    "auto": False,
    "queda": 2.0,
    "take": 3.0,
    "stop": 1.5,
    "valor": 50.0,
    "referencia": None,
    "posicoes": [],
    "max_posicoes": 3,
    "cooldown": 60,
    "ultimo_trade": 0.0,
    "lucro_total": 0.0,
}

lock = threading.RLock()


# ================= PERSISTÊNCIA =================
def salvar_estado():
    import json
    with lock:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)


def carregar_estado():
    import json
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                dados = json.load(f)
            with lock:
                estado.update(dados)
        except Exception as e:
            print("Erro ao carregar estado:", e)


def garantir_csv():
    if not os.path.exists(TRADES_FILE):
        with open(TRADES_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "data",
                "tipo",
                "modo",
                "entrada",
                "saida",
                "quantidade",
                "investido",
                "valor_final",
                "lucro_reais",
                "lucro_pct",
                "motivo"
            ])


def registrar_trade(tipo, modo, entrada, saida, quantidade, investido, valor_final, lucro_reais, lucro_pct, motivo):
    garantir_csv()
    with open(TRADES_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            tipo,
            modo,
            entrada,
            saida,
            quantidade,
            investido,
            valor_final,
            lucro_reais,
            lucro_pct,
            motivo
        ])


# ================= UTILS =================
def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": msg},
        timeout=20,
    )


def send_photo(path, caption=""):
    with open(path, "rb") as f:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": caption},
            files={"photo": f},
            timeout=40,
        )


def autorizado(update: Update) -> bool:
    try:
        return str(update.effective_chat.id) == str(CHAT_ID)
    except Exception:
        return False


def preco():
    return float(client.get_symbol_ticker(symbol=SYMBOL)["price"])


def fmt_brl(v):
    return f"R${v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def calc_qtd(valor, p):
    step = 0.00001
    qtd = math.floor((valor / p) / step) * step
    return round(qtd, 5)


# ================= PAINEL =================
def painel_texto():
    p = preco()

    total_inv = sum(pos["investido"] for pos in estado["posicoes"])
    total_atual = sum(pos["qtd"] * p for pos in estado["posicoes"])
    lucro_aberto = total_atual - total_inv

    texto = (
        "📊 PAINEL\n\n"
        f"Modo: {estado['modo']}\n"
        f"Auto: {'ON' if estado['auto'] else 'OFF'}\n"
        f"Par: {SYMBOL}\n"
        f"Preço atual: {fmt_brl(p)}\n"
        f"Referência: {fmt_brl(estado['referencia']) if estado['referencia'] else 'None'}\n\n"
        f"Queda compra: {estado['queda']:.2f}%\n"
        f"Take individual: {estado['take']:.2f}%\n"
        f"Stop individual: {estado['stop']:.2f}%\n"
        f"Valor por ordem: {fmt_brl(estado['valor'])}\n"
        f"Máx posições: {estado['max_posicoes']}\n"
        f"Cooldown: {estado['cooldown']}s\n\n"
        f"Posições abertas: {len(estado['posicoes'])}\n"
        f"Investido aberto: {fmt_brl(total_inv)}\n"
        f"Valor aberto atual: {fmt_brl(total_atual)}\n"
        f"Resultado aberto: {fmt_brl(lucro_aberto)}\n"
        f"Lucro total realizado: {fmt_brl(estado['lucro_total'])}"
    )
    return texto


# ================= MENU =================
def menu_principal():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Painel", callback_data="painel")],
        [InlineKeyboardButton("📉 Comprar", callback_data="comprar"),
         InlineKeyboardButton("💰 Vender tudo", callback_data="zerar")],
        [InlineKeyboardButton("🤖 Auto ON/OFF", callback_data="auto"),
         InlineKeyboardButton("📜 Histórico", callback_data="historico")],
        [InlineKeyboardButton("📈 Gráfico", callback_data="grafico"),
         InlineKeyboardButton("⚙️ Config", callback_data="config")]
    ])


def menu_config():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Queda 2%", callback_data="q2"),
         InlineKeyboardButton("Queda 3%", callback_data="q3")],
        [InlineKeyboardButton("Take 2%", callback_data="t2"),
         InlineKeyboardButton("Take 3%", callback_data="t3")],
        [InlineKeyboardButton("Stop 1%", callback_data="s1"),
         InlineKeyboardButton("Stop 2%", callback_data="s2")],
    ])


# ================= EXECUÇÃO DE ORDENS =================
def ordem_buy(qtd):
    if estado["modo"] == "REAL":
        return client.create_order(symbol=SYMBOL, side="BUY", type="MARKET", quantity=qtd)
    return client.create_test_order(symbol=SYMBOL, side="BUY", type="MARKET", quantity=qtd)


def ordem_sell(qtd):
    if estado["modo"] == "REAL":
        return client.create_order(symbol=SYMBOL, side="SELL", type="MARKET", quantity=qtd)
    return client.create_test_order(symbol=SYMBOL, side="SELL", type="MARKET", quantity=qtd)


def executar_compra_manual(valor):
    p = preco()
    qtd = calc_qtd(valor, p)

    ordem_buy(qtd)

    pos = {
        "entrada": p,
        "qtd": qtd,
        "investido": valor,
        "alvo": p * (1 + estado["take"] / 100),
        "stop_preco": p * (1 - estado["stop"] / 100),
        "aberta_em": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "tipo": "MANUAL"
    }

    with lock:
        estado["posicoes"].append(pos)
        estado["referencia"] = p
        estado["ultimo_trade"] = time.time()
        salvar_estado()

    send(
        "✅ COMPRA MANUAL\n"
        f"Preço: {fmt_brl(p)}\n"
        f"Qtd: {qtd}\n"
        f"Investido: {fmt_brl(valor)}\n"
        f"Take: {fmt_brl(pos['alvo'])}\n"
        f"Stop: {fmt_brl(pos['stop_preco'])}"
    )


def vender_posicao(pos, motivo, preco_saida):
    ordem_sell(pos["qtd"])

    valor_final = pos["qtd"] * preco_saida
    lucro = valor_final - pos["investido"]
    lucro_pct = (lucro / pos["investido"]) * 100 if pos["investido"] else 0

    with lock:
        estado["lucro_total"] += lucro
        salvar_estado()

    registrar_trade(
        tipo="VENDA",
        modo=estado["modo"],
        entrada=pos["entrada"],
        saida=preco_saida,
        quantidade=pos["qtd"],
        investido=pos["investido"],
        valor_final=valor_final,
        lucro_reais=lucro,
        lucro_pct=lucro_pct,
        motivo=motivo
    )

    send(
        f"💰 VENDA ({motivo})\n"
        f"Entrada: {fmt_brl(pos['entrada'])}\n"
        f"Saída: {fmt_brl(preco_saida)}\n"
        f"Investido: {fmt_brl(pos['investido'])}\n"
        f"Valor final: {fmt_brl(valor_final)}\n"
        f"Lucro: {fmt_brl(lucro)}\n"
        f"Resultado: {lucro_pct:.2f}%"
    )


def zerar_tudo_sync():
    if not estado["posicoes"]:
        return "Não há posições abertas."

    p = preco()
    lucro_total = 0.0

    with lock:
        posicoes = list(estado["posicoes"])

    for pos in posicoes:
        ordem_sell(pos["qtd"])
        valor_final = pos["qtd"] * p
        lucro = valor_final - pos["investido"]
        lucro_pct = (lucro / pos["investido"]) * 100 if pos["investido"] else 0
        lucro_total += lucro

        registrar_trade(
            tipo="VENDA",
            modo=estado["modo"],
            entrada=pos["entrada"],
            saida=p,
            quantidade=pos["qtd"],
            investido=pos["investido"],
            valor_final=valor_final,
            lucro_reais=lucro,
            lucro_pct=lucro_pct,
            motivo="ZERAR TUDO"
        )

    with lock:
        estado["lucro_total"] += lucro_total
        estado["posicoes"] = []
        estado["referencia"] = p
        salvar_estado()

    return f"💰 Tudo vendido em {fmt_brl(p)}\nResultado total: {fmt_brl(lucro_total)}"


# ================= AUTO TRADING =================
def monitor():
    while True:
        try:
            if estado["auto"]:
                p = preco()
                agora = time.time()

                with lock:
                    if estado["referencia"] is None:
                        estado["referencia"] = p
                        salvar_estado()

                # Atualiza referência se subir
                if p > estado["referencia"]:
                    with lock:
                        estado["referencia"] = p
                        salvar_estado()

                # Compra automática
                alvo_compra = estado["referencia"] * (1 - estado["queda"] / 100)

                pode_comprar = (
                    p <= alvo_compra
                    and len(estado["posicoes"]) < estado["max_posicoes"]
                    and (agora - estado["ultimo_trade"] >= estado["cooldown"])
                )

                if pode_comprar:
                    qtd = calc_qtd(estado["valor"], p)
                    ordem_buy(qtd)

                    pos = {
                        "entrada": p,
                        "qtd": qtd,
                        "investido": estado["valor"],
                        "alvo": p * (1 + estado["take"] / 100),
                        "stop_preco": p * (1 - estado["stop"] / 100),
                        "aberta_em": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                        "tipo": "AUTO"
                    }

                    with lock:
                        estado["posicoes"].append(pos)
                        estado["referencia"] = p
                        estado["ultimo_trade"] = agora
                        salvar_estado()

                    send(
                        "📉 COMPRA AUTO\n"
                        f"Preço: {fmt_brl(p)}\n"
                        f"Qtd: {qtd}\n"
                        f"Investido: {fmt_brl(estado['valor'])}\n"
                        f"Take: {fmt_brl(pos['alvo'])}\n"
                        f"Stop: {fmt_brl(pos['stop_preco'])}"
                    )

                # Venda por take/stop
                novas = []
                for pos in estado["posicoes"]:
                    if p >= pos["alvo"]:
                        vender_posicao(pos, "TAKE PROFIT", p)
                    elif p <= pos["stop_preco"]:
                        vender_posicao(pos, "STOP LOSS", p)
                    else:
                        novas.append(pos)

                with lock:
                    estado["posicoes"] = novas
                    salvar_estado()

            time.sleep(5)

        except Exception as e:
            print("Erro no monitor:", e)
            time.sleep(5)


# ================= HISTÓRICO E GRÁFICO =================
def ler_historico():
    garantir_csv()
    linhas = []
    with open(TRADES_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            linhas.append(row)
    return linhas


async def enviar_historico(update_or_message):
    linhas = ler_historico()
    if not linhas:
        texto = "Nenhum trade registrado ainda."
    else:
        ultimas = linhas[-10:]
        partes = ["📜 HISTÓRICO (últimos 10)\n"]
        for row in ultimas:
            partes.append(
                f"{row['data']} | {row['motivo']} | "
                f"Entrada {fmt_brl(float(row['entrada']))} | "
                f"Saída {fmt_brl(float(row['saida']))} | "
                f"Lucro {fmt_brl(float(row['lucro_reais']))}"
            )
        texto = "\n".join(partes)

    if hasattr(update_or_message, "reply_text"):
        await update_or_message.reply_text(texto)
    else:
        await update_or_message.message.reply_text(texto)


def gerar_grafico():
    linhas = ler_historico()
    if not linhas:
        return None

    xs = []
    ys = []
    acumulado = 0.0

    for i, row in enumerate(linhas, start=1):
        acumulado += float(row["lucro_reais"])
        xs.append(i)
        ys.append(acumulado)

    plt.figure(figsize=(8, 5))
    plt.plot(xs, ys, marker="o")
    plt.title("Lucro acumulado por trade")
    plt.xlabel("Trade")
    plt.ylabel("Lucro acumulado (R$)")
    plt.tight_layout()

    path = "grafico_trades.png"
    plt.savefig(path)
    plt.close()
    return path


async def enviar_grafico(update_or_message):
    path = gerar_grafico()
    if not path:
        if hasattr(update_or_message, "reply_text"):
            await update_or_message.reply_text("Ainda não há trades para gerar gráfico.")
        else:
            await update_or_message.message.reply_text("Ainda não há trades para gerar gráfico.")
        return

    send_photo(path, "📈 Gráfico de lucro acumulado")
    if os.path.exists(path):
        os.remove(path)


# ================= COMANDOS =================
async def help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return
    await update.message.reply_text("🚀 MENU DO BOT", reply_markup=menu_principal())


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return
    resumo = {
        "modo": estado["modo"],
        "auto": estado["auto"],
        "queda": estado["queda"],
        "take": estado["take"],
        "stop": estado["stop"],
        "valor": estado["valor"],
        "referencia": estado["referencia"],
        "posicoes": len(estado["posicoes"]),
        "lucro_total": estado["lucro_total"],
    }
    await update.message.reply_text(str(resumo))


async def painel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return
    await update.message.reply_text(painel_texto())


async def historico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return
    await enviar_historico(update.message)


async def grafico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return
    await enviar_grafico(update.message)


async def modo_real(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return
    estado["modo"] = "REAL"
    salvar_estado()
    await update.message.reply_text("Modo REAL ativado")


async def modo_simulado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return
    estado["modo"] = "SIMULADO"
    salvar_estado()
    await update.message.reply_text("Modo SIMULADO ativado")


async def set_queda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return
    try:
        estado["queda"] = float(context.args[0].replace(",", "."))
        salvar_estado()
        await update.message.reply_text(f"Queda = {estado['queda']}%")
    except Exception:
        await update.message.reply_text("Use: /queda 2")


async def set_take(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return
    try:
        valor = float(context.args[0].replace(",", "."))
        estado["take"] = valor
        for pos in estado["posicoes"]:
            pos["alvo"] = pos["entrada"] * (1 + valor / 100)
        salvar_estado()
        await update.message.reply_text(f"Take = {valor}%")
    except Exception:
        await update.message.reply_text("Use: /take 3")


async def set_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return
    try:
        valor = float(context.args[0].replace(",", "."))
        estado["stop"] = valor
        for pos in estado["posicoes"]:
            pos["stop_preco"] = pos["entrada"] * (1 - valor / 100)
        salvar_estado()
        await update.message.reply_text(f"Stop = {valor}%")
    except Exception:
        await update.message.reply_text("Use: /stop 1.5")


async def set_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return
    try:
        estado["valor"] = float(context.args[0].replace(",", "."))
        salvar_estado()
        await update.message.reply_text(f"Valor por ordem = {fmt_brl(estado['valor'])}")
    except Exception:
        await update.message.reply_text("Use: /valor 50")


# ================= BOTÕES =================
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not autorizado(update):
        return

    if query.data == "painel":
        await query.message.reply_text(painel_texto())

    elif query.data == "comprar":
        context.user_data["compra"] = True
        await query.message.reply_text("Digite o valor em R$ para comprar:")

    elif query.data == "zerar":
        msg = zerar_tudo_sync()
        await query.message.reply_text(msg)

    elif query.data == "auto":
        estado["auto"] = not estado["auto"]
        salvar_estado()
        await query.message.reply_text(f"🤖 Auto: {'ON' if estado['auto'] else 'OFF'}")

    elif query.data == "historico":
        await enviar_historico(query.message)

    elif query.data == "grafico":
        await enviar_grafico(query.message)

    elif query.data == "config":
        await query.message.reply_text("⚙️ Configurações rápidas:", reply_markup=menu_config())

    elif query.data == "q2":
        estado["queda"] = 2.0
        salvar_estado()
        await query.message.reply_text("Queda = 2%")

    elif query.data == "q3":
        estado["queda"] = 3.0
        salvar_estado()
        await query.message.reply_text("Queda = 3%")

    elif query.data == "t2":
        estado["take"] = 2.0
        for pos in estado["posicoes"]:
            pos["alvo"] = pos["entrada"] * 1.02
        salvar_estado()
        await query.message.reply_text("Take = 2%")

    elif query.data == "t3":
        estado["take"] = 3.0
        for pos in estado["posicoes"]:
            pos["alvo"] = pos["entrada"] * 1.03
        salvar_estado()
        await query.message.reply_text("Take = 3%")

    elif query.data == "s1":
        estado["stop"] = 1.0
        for pos in estado["posicoes"]:
            pos["stop_preco"] = pos["entrada"] * 0.99
        salvar_estado()
        await query.message.reply_text("Stop = 1%")

    elif query.data == "s2":
        estado["stop"] = 2.0
        for pos in estado["posicoes"]:
            pos["stop_preco"] = pos["entrada"] * 0.98
        salvar_estado()
        await query.message.reply_text("Stop = 2%")


# ================= TEXTO LIVRE PARA COMPRA =================
async def texto_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorizado(update):
        return

    if context.user_data.get("compra"):
        try:
            valor = float(update.message.text.replace(",", "."))
            executar_compra_manual(valor)
            await update.message.reply_text("✅ Compra registrada com sucesso.")
        except Exception as e:
            await update.message.reply_text(f"Erro na compra: {e}")

        context.user_data["compra"] = False


# ================= MAIN =================
def main():
    carregar_estado()
    garantir_csv()

    threading.Thread(target=monitor, daemon=True).start()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("help", help_menu))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("painel", painel))
    app.add_handler(CommandHandler("historico", historico))
    app.add_handler(CommandHandler("grafico", grafico))
    app.add_handler(CommandHandler("modo_real", modo_real))
    app.add_handler(CommandHandler("modo_simulado", modo_simulado))
    app.add_handler(CommandHandler("queda", set_queda))
    app.add_handler(CommandHandler("take", set_take))
    app.add_handler(CommandHandler("stop", set_stop))
    app.add_handler(CommandHandler("valor", set_valor))

    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, texto_handler))

    print("BOT ONLINE 🚀")
    send("🤖 Bot iniciado com sucesso")
    app.run_polling()


if __name__ == "__main__":
    main()