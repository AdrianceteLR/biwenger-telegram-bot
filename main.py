import logging
import requests
import time
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ==========================================
# CONFIGURACIÓN E INICIALIZACIÓN
# ==========================================
BEARER_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOjI4NzEyNTkwLCJpYXQiOjE3ODk2MzE2MDB9.Cq88teHka0Q7zcsoX_x_hRLXAtUG8AMtoxKBYvgsL6E"
X_LEAGUE_ID = "2148505"
X_USER_ID = "14006706"

TELEGRAM_BOT_TOKEN = "8825840797:AAEZ-O3KUH8duOm5JRsRwl5NZjgl_qAOH40"
TELEGRAM_CHAT_ID = "727756988"

headers = {
    "Authorization": f"Bearer {BEARER_TOKEN.replace('Bearer ', '').strip()}",
    "x-league": str(X_LEAGUE_ID).strip(),
    "x-user": str(X_USER_ID).strip(),
    "x-version": "650",
    "x-lang": "es",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Origin": "https://biwenger.as.com",
    "Referer": "https://biwenger.as.com/"
}

POSICIONES_VALIDAS = {
    1: "🧤 POR",
    2: "🛡️ DEF",
    3: "⚙️ MC",
    4: "⚽ DEL"
}

STATUS_MAP = {
    'ok': '🟢 Disponible',
    'injured': '🚑 Lesionado',
    'doubtful': '❓ Duda',
    'suspended': '🟨 Sancionado'
}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Memoria global para evitar enviar la misma alerta flash repetidas veces
alertas_enviadas = set()
# Memoria para los temporizadores de cláusulas programados
alertas_clausulas_programadas = set()

def fmt_eur(valor):
    return f"{valor:,.0f}€".replace(",", ".")

# ==========================================
# OBTENCIÓN DE DATOS DE BIWENGER
# ==========================================
def cargar_datos_globales():
    url = "https://biwenger.as.com/api/v2/competitions/la-liga/data?lang=es&score=1"
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json().get('data', {})
            equipos = {int(k): v.get('name', 'Desconocido') for k, v in data.get('teams', {}).items()}
            jugadores = {int(k): v for k, v in data.get('players', {}).items()}
            return equipos, jugadores
    except Exception as e:
        logging.error(f"Error cargando datos globales: {e}")
    return {}, {}

def calcular_media_y_racha(p_info):
    fitness_list = p_info.get('fitness', [])
    puntos_totales = p_info.get('points', 0)
    partidos_jugados = p_info.get('playedMatches', 0)
    
    media = 0.0
    racha_str = "Sin datos"
    if isinstance(fitness_list, list) and len(fitness_list) > 0:
        puntos_recientes = [p for p in fitness_list if isinstance(p, (int, float))]
        if puntos_recientes:
            media = sum(puntos_recientes) / len(puntos_recientes)
            racha_str = " ➔ ".join([str(p) for p in puntos_recientes[-4:]])
    elif partidos_jugados > 0:
        media = puntos_totales / partidos_jugados

    return round(media, 1), racha_str

# ==========================================
# GENERADORES DE MENSAJES PARA TELEGRAM
# ==========================================
def obtener_mensaje_mercado():
    dict_equipos, dict_jugadores = cargar_datos_globales()
    url_market = "https://biwenger.as.com/api/v2/market"
    res = requests.get(url_market, headers=headers)
    
    if res.status_code != 200:
        return "❌ Error al consultar el mercado."

    sales = res.json().get('data', {}).get('sales', [])
    lineas = ["🛒 <b>MERCADO DE FICHAJES Y PUJAS SUGERIDAS</b>\n"]
    
    hay_libres = False
    for sale in sales:
        if sale.get('user') is None:
            hay_libres = True
            player_data_market = sale.get('player', {})
            p_id = player_data_market.get('id')
            p_info = dict_jugadores.get(p_id, {})
            
            nombre = p_info.get('name', player_data_market.get('name', 'Jugador'))
            pos_nombre = POSICIONES_VALIDAS.get(p_info.get('position', 0), "JUG")
            equipo_nombre = dict_equipos.get(p_info.get('teamID', 0), 'Sin equipo')
            
            precio = p_info.get('price', 0)
            incremento = p_info.get('priceIncrement', 0)
            status_code = p_info.get('status', 'ok')
            status_str = STATUS_MAP.get(status_code, '⚪ Desconocido')
            
            media_puntos, racha = calcular_media_y_racha(p_info)

            # Lógica de Puja
            if status_code in ['injured', 'suspended']:
                estrategia = "🚑 Lesionado/Sancionado"
                puja_maxima = precio
            elif incremento <= 0:
                estrategia = "⚠️ Sin tendencia"
                puja_maxima = precio
            elif incremento >= 40000 and media_puntos < 4.5:
                estrategia = "💸 Especular (Resell)"
                puja_maxima = precio + (incremento * 2)
            elif precio >= 8000000 or media_puntos >= 5.5:
                estrategia = "⭐ Alineación TOP"
                puja_maxima = precio * 1.25
            elif precio >= 2000000 and media_puntos >= 3.5:
                estrategia = "⚽ Titular Regular"
                puja_maxima = precio * 1.15
            else:
                estrategia = "🧩 Fondo de Armario"
                puja_maxima = precio * 1.10

            if puja_maxima > precio and status_code == 'ok':
                pico = (p_id % 85 + 12) * 1000 + 340
                puja_maxima = int(puja_maxima + pico)

            signo = "📈 +" if incremento > 0 else "📉 " if incremento < 0 else "➖ "

            lineas.append(f"• <b>{nombre}</b> ({pos_nombre}) | <i>{equipo_nombre}</i> [{status_str}]")
            lineas.append(f"   💰 VM: <b>{fmt_eur(precio)}</b> ¦ Var: <code>{signo}{fmt_eur(incremento)}</code> ¦ Media: <b>{media_puntos}</b>")
            lineas.append(f"   🎯 <i>{estrategia}</i> ➔ Max Puja: <code><b>{fmt_eur(puja_maxima)}</b></code>\n")

    if not hay_libres:
        return "<i>No hay jugadores libres en el mercado actualmente.</i>"
    
    return "\n".join(lineas)

def obtener_mensaje_clausulas():
    dict_equipos, dict_jugadores = cargar_datos_globales()
    url_league = "https://biwenger.as.com/api/v2/league?fields=*,users(id,name)"
    res = requests.get(url_league, headers=headers)
    
    if res.status_code != 200:
        return "❌ Error al obtener los datos de la liga."

    users_list = res.json().get('data', {}).get('users', [])
    lineas = ["🏆 <b>OPORTUNIDADES DE CLÁUSULA</b>\n"]
    now_ts = int(time.time())

    for u in users_list:
        u_id = u.get('id')
        nombre_rival = u.get('name', 'Usuario')
        if str(u_id) == X_USER_ID:
            continue

        res_user = requests.get(f"https://biwenger.as.com/api/v2/user/{u_id}?fields=*,players(*,owner)", headers=headers)
        if res_user.status_code != 200:
            continue

        players_array = res_user.json().get('data', {}).get('players', [])
        chollos = []

        for p_item in players_array:
            p_id = p_item.get('id')
            p_info = dict_jugadores.get(p_id, {})
            precio = p_info.get('price', 0)
            incremento = p_info.get('priceIncrement', 0)
            nombre_j = p_info.get('name', '')
            pos_id = p_info.get('position', 0)

            if precio <= 0 or not nombre_j:
                continue

            owner_info = p_item.get('owner', {})
            if not isinstance(owner_info, dict): owner_info = {}

            clause_price = owner_info.get('clause', 0)
            until_date = owner_info.get('clauseLockedUntil', 0)
            if until_date > 1e11: until_date = int(until_date / 1000)

            tiempo_restante = until_date - now_ts
            disponible_hoy = (until_date == 0) or (tiempo_restante <= 86400)
            is_modified = owner_info.get('clauseModified', False)

            media_puntos, _ = calcular_media_y_racha(p_info)
            sobreprecio_pct = (clause_price - precio) / precio if precio > 0 else 999

            if disponible_hoy and clause_price > 0 and not is_modified:
                if sobreprecio_pct <= 0.20 and (incremento >= 0 or media_puntos >= 3.5):
                    hora_str = datetime.fromtimestamp(until_date).strftime("%H:%M") if until_date > now_ts else "ABIERTO 🔓"
                    chollos.append((nombre_j, POSICIONES_VALIDAS.get(pos_id, "JUG"), precio, clause_price, round(sobreprecio_pct*100, 1), media_puntos, hora_str))

        if chollos:
            lineas.append(f"👤 <b>{nombre_rival.upper()}</b>")
            for c in chollos:
                lineas.append(f"  ▪️ <b>{c[0]}</b> ({c[1]}) ➔ ⏰ {c[6]}")
                lineas.append(f"     VM: <b>{fmt_eur(c[2])}</b> | Cláusula: <b>{fmt_eur(c[3])}</b> (<code>+{c[4]}%</code>) | Media: <b>{c[5]}</b>\n")

    return "\n".join(lineas) if len(lineas) > 1 else "<i>No hay cláusulas atractivas para ejecutar hoy.</i>"

def analizar_jugador(busqueda):
    dict_equipos, dict_jugadores = cargar_datos_globales()
    busqueda_lc = busqueda.lower().strip()
    
    coincidencias = []
    for p_id, p in dict_jugadores.items():
        if busqueda_lc in p.get('name', '').lower():
            coincidencias.append(p)

    if not coincidencias:
        return f"🔍 No se encontró ningún jugador con el nombre <b>'{busqueda}'</b>."

    p = coincidencias[0]
    media, racha = calcular_media_y_racha(p)
    equipo = dict_equipos.get(p.get('teamID', 0), 'Sin equipo')
    pos = POSICIONES_VALIDAS.get(p.get('position', 0), 'JUG')
    status_str = STATUS_MAP.get(p.get('status', 'ok'), '⚪ Desconocido')
    precio = p.get('price', 0)
    inc = p.get('priceIncrement', 0)
    signo = "📈 +" if inc > 0 else "📉 " if inc < 0 else "➖ "

    res = [
        f"📊 <b>INFORME DE JUGADOR: {p.get('name').upper()}</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"👤 Posición: <b>{pos}</b> | Equipo: <i>{equipo}</i>",
        f"🏥 Estado: <b>{status_str}</b>",
        f"💰 Valor de Mercado: <b>{fmt_eur(precio)}</b>",
        f"📉 Var. Diaria: <code>{signo}{fmt_eur(inc)}</code>",
        f"⭐ Media Puntos: <b>{media}</b>",
        f"📈 Última Racha: <code>{racha}</code>",
        f"🏆 Puntos Totales: <b>{p.get('points', 0)}</b>"
    ]
    return "\n".join(res)

# ==========================================
# MENÚ TELEGRAM CON BOTONES INLINE
# ==========================================
def menu_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("🛒 Mercado Libre", callback_data="btn_mercado"),
            InlineKeyboardButton("🏆 Cláusulas", callback_data="btn_clausulas")
        ],
        [
            InlineKeyboardButton("⚡ Chollos Flash", callback_data="btn_flash"),
            InlineKeyboardButton("❓ Ayuda", callback_data="btn_ayuda")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 <b>¡Bienvenido a tu Asistente Biwenger Pro!</b>\n\n"
        "Selecciona una opción del menú inferior o usa comandos directos:\n"
        "• <code>/analizar [Nombre]</code> - Informe completo de un jugador.\n"
        "• <code>/mercado</code> - Ver mercado libre y pujas máximas.\n"
        "• <code>/clausulas</code> - Buscar chollos en rivales."
    )
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=menu_keyboard())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "btn_mercado":
        await query.edit_message_text("🔄 Cargando mercado...", parse_mode="HTML")
        txt = obtener_mensaje_mercado()
        await query.edit_message_text(txt, parse_mode="HTML", reply_markup=menu_keyboard())

    elif query.data == "btn_clausulas":
        await query.edit_message_text("🔄 Analizando plantillas rivales...", parse_mode="HTML")
        txt = obtener_mensaje_clausulas()
        await query.edit_message_text(txt, parse_mode="HTML", reply_markup=menu_keyboard())

    elif query.data == "btn_flash":
        await query.edit_message_text("⚡ No hay alertas flash críticas pendientes en este instante.", reply_markup=menu_keyboard())

    elif query.data == "btn_ayuda":
        txt = (
            "📖 <b>GUÍA DE USO</b>\n\n"
            "• Para analizar cualquier jugador de LaLiga escribe:\n"
            "  <code>/analizar Vinicius</code> o <code>/analizar Mbappé</code>\n\n"
            "• El bot monitorea el mercado cada hora y te enviará un mensaje urgente al instante si sale un TOP o un Chollo >150k de subida."
        )
        await query.edit_message_text(txt, parse_mode="HTML", reply_markup=menu_keyboard())

async def analizar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Indica el nombre del jugador. Ejemplo: <code>/analizar Bellingham</code>", parse_mode="HTML")
        return
    nombre_busqueda = " ".join(context.args)
    txt = analizar_jugador(nombre_busqueda)
    await update.message.reply_text(txt, parse_mode="HTML")

# ==========================================
# TAREAS AUTOMÁTICAS EN SEGUNDO PLANO (SCHEDULER)
# ==========================================
async def tarea_alertas_flash(app):
    """Monitorea el mercado libre y envía alertas en tiempo real por jugadores TOP o Mega Chollos."""
    global alertas_enviadas
    dict_equipos, dict_jugadores = cargar_datos_globales()
    url_market = "https://biwenger.as.com/api/v2/market"
    
    try:
        res = requests.get(url_market, headers=headers)
        if res.status_code != 200: return
        
        sales = res.json().get('data', {}).get('sales', [])
        for sale in sales:
            if sale.get('user') is None:
                p_id = sale.get('player', {}).get('id')
                if p_id in alertas_enviadas:
                    continue

                p_info = dict_jugadores.get(p_id, {})
                precio = p_info.get('price', 0)
                inc = p_info.get('priceIncrement', 0)
                media, _ = calcular_media_y_racha(p_info)
                nombre = p_info.get('name', 'Jugador')

                es_top = (precio >= 8000000 or media >= 5.5)
                es_super_chollo = (inc >= 150000)

                if es_top or es_super_chollo:
                    alertas_enviadas.add(p_id)
                    motivo = "⭐ JUGADOR TOP EN EL MERCADO" if es_top else "⚡ MEGA CHOLLO DE ESPECULACIÓN (+150k/día)"
                    puja = int(precio * 1.20 if es_top else precio + (inc * 2))

                    msg = (
                        f"🚨 <b>¡ALERTA FLASH DE MERCADO!</b> 🚨\n\n"
                        f"<b>{motivo}</b>\n"
                        f"• <b>{nombre}</b> ({POSICIONES_VALIDAS.get(p_info.get('position',0),'JUG')})\n"
                        f"💰 VM: <b>{fmt_eur(precio)}</b> | Var: <code>+{fmt_eur(inc)}</code>\n"
                        f"📊 Media: <b>{media}</b>\n"
                        f"🎯 <b>Puja Máxima Sugerida: <code>{fmt_eur(puja)}</code></b>"
                    )
                    await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Error en tarea alertas flash: {e}")

async def tarea_recordatorio_clausulas(app):
    """Programa una alarma 15 minutos antes de que abra la cláusula de un jugador objetivo."""
    global alertas_clausulas_programadas
    dict_equipos, dict_jugadores = cargar_datos_globales()
    url_league = "https://biwenger.as.com/api/v2/league?fields=*,users(id,name)"
    
    try:
        res = requests.get(url_league, headers=headers)
        if res.status_code != 200: return
        
        users_list = res.json().get('data', {}).get('users', [])
        now_ts = int(time.time())

        for u in users_list:
            u_id = u.get('id')
            if str(u_id) == X_USER_ID: continue

            res_user = requests.get(f"https://biwenger.as.com/api/v2/user/{u_id}?fields=*,players(*,owner)", headers=headers)
            if res_user.status_code != 200: continue

            players_array = res_user.json().get('data', {}).get('players', [])
            for p_item in players_array:
                p_id = p_item.get('id')
                owner_info = p_item.get('owner', {})
                if not isinstance(owner_info, dict): continue

                until_date = owner_info.get('clauseLockedUntil', 0)
                if until_date > 1e11: until_date = int(until_date / 1000)

                segundos_restantes = until_date - now_ts
                
                # Si falta entre 14 y 16 minutos para que se abra y no se ha avisado
                if 840 <= segundos_restantes <= 960 and p_id not in alertas_clausulas_programadas:
                    alertas_clausulas_programadas.add(p_id)
                    p_info = dict_jugadores.get(p_id, {})
                    nombre = p_info.get('name', 'Jugador')
                    clause_price = owner_info.get('clause', 0)
                    
                    hora_apertura = datetime.fromtimestamp(until_date).strftime("%H:%M")

                    msg = (
                        f"⏰ <b>¡RECORDATORIO DE CLÁUSULA! (En 15 minutos)</b>\n\n"
                        f"El candado de <b>{nombre}</b> (Rival: <i>{u.get('name')}</i>) se abre a las <b>{hora_apertura}h</b>.\n"
                        f"💰 Precio Cláusula: <b>{fmt_eur(clause_price)}</b>\n\n"
                        f"⚡ <i>¡Prepara tu puja en Biwenger para estar listo al segundo exacto!</i>"
                    )
                    await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Error en tarea recordatorio cláusulas: {e}")

# ==========================================
# MAIN Y EJECUCIÓN (CORREGIDO PARA PYTHON 3.14)
# ==========================================
import asyncio

async def post_init(app):
    """Inicia el programador cuando el bucle de eventos de asyncio ya está corriendo."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(tarea_alertas_flash, 'interval', minutes=30, args=[app])
    scheduler.add_job(tarea_recordatorio_clausulas, 'interval', minutes=2, args=[app])
    scheduler.start()
    print("⏰ Programador de tareas automáticas iniciado correctamente.")

def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # Handlers de comandos
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", start_command))
    app.add_handler(CommandHandler("mercado", lambda u, c: u.message.reply_text(obtener_mensaje_mercado(), parse_mode="HTML")))
    app.add_handler(CommandHandler("clausulas", lambda u, c: u.message.reply_text(obtener_mensaje_clausulas(), parse_mode="HTML")))
    app.add_handler(CommandHandler("analizar", analizar_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("🤖 Bot interactivo de Biwenger en ejecución...")
    app.run_polling()

if __name__ == "__main__":
    main()
