import re
import time
import html
import os
import requests  # pyright: ignore[reportMissingModuleSource]
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==========================================
# CONFIGURACIÓN E INICIALIZACIÓN
# ==========================================
# Lee de las variables de entorno de GitHub Actions o usa los valores por defecto localmente
BEARER_TOKEN = os.getenv("BEARER_TOKEN", "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOjI4NzEyNTkwLCJpYXQiOjE3ODk2MzE2MDB9.Cq88teHka0Q7zcsoX_x_hRLXAtUG8AMtoxKBYvgsL6E")
X_LEAGUE_ID = os.getenv("X_LEAGUE_ID", "2148505")
X_USER_ID = os.getenv("X_USER_ID", "14006706")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8825840797:AAEZ-O3KUH8duOm5JRsRwl5NZjgl_qAOH40")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "727756988")

# Sesión HTTP persistente
http_session = requests.Session()
http_session.headers.update({
    "Authorization": f"Bearer {BEARER_TOKEN.replace('Bearer ', '').strip()}",
    "x-league": str(X_LEAGUE_ID).strip(),
    "x-user": str(X_USER_ID).strip(),
    "x-version": "650",
    "x-lang": "es",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Origin": "https://biwenger.as.com",
    "Referer": "https://biwenger.as.com/"
})

POSICIONES_VALIDAS = {
    1: "🧤 POR",
    2: "🛡️ DEF",
    3: "⚙️ MC",
    4: "⚽ DEL"
}

def fmt_eur(valor):
    return f"{valor:,.0f}€".replace(",", ".")

def limpiar_texto_html(texto):
    """Limpia caracteres especiales para evitar errores en el parseo HTML de Telegram."""
    if not texto:
        return ""
    t = html.escape(str(texto))
    t = t.replace('"', '&quot;').replace("'", '&#39;')
    t = re.sub(r'<[^>]*>', '', t)
    return t.strip()

# ==========================================
# OBTENCIÓN Y CÁLCULO DE DATOS
# ==========================================
def cargar_datos_globales():
    url = "https://biwenger.as.com/api/v2/competitions/la-liga/data?lang=es&score=1"
    try:
        res = http_session.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json().get('data', {})
            equipos = {int(k): v.get('name', 'Desconocido') for k, v in data.get('teams', {}).items()}
            jugadores = {int(k): v for k, v in data.get('players', {}).items()}
            return equipos, jugadores
    except Exception as e:
        print(f"❌ Error cargando datos globales: {e}")
    return {}, {}

def calcular_media(p_info):
    fitness_list = p_info.get('fitness', [])
    puntos_totales = p_info.get('points', 0)
    partidos_jugados = p_info.get('playedMatches', 0)
    
    if isinstance(fitness_list, list) and len(fitness_list) > 0:
        puntos_recientes = [p for p in fitness_list if isinstance(p, (int, float))]
        if puntos_recientes:
            return round(sum(puntos_recientes) / len(puntos_recientes), 1)
    elif partidos_jugados > 0:
        return round(puntos_totales / partidos_jugados, 1)

    return 0.0

def obtener_chollos_usuario(u, dict_jugadores, now_ts):
    """Procesa las plantillas de los rivales en paralelo."""
    u_id = u.get('id')
    nombre_rival = u.get('name', 'Usuario')
    if str(u_id) == X_USER_ID:
        return None

    res_user = http_session.get(f"https://biwenger.as.com/api/v2/user/{u_id}?fields=*,players(*,owner)")
    if res_user.status_code != 200:
        return None

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
        if not isinstance(owner_info, dict): 
            owner_info = {}

        clause_price = owner_info.get('clause', 0)
        until_date = owner_info.get('clauseLockedUntil', 0)
        if until_date > 1e11: 
            until_date = int(until_date / 1000)

        tiempo_restante = until_date - now_ts
        disponible_hoy = (until_date == 0) or (tiempo_restante <= 86400)
        is_modified = owner_info.get('clauseModified', False)

        media_puntos = calcular_media(p_info)
        sobreprecio_pct = (clause_price - precio) / precio if precio > 0 else 999

        if disponible_hoy and clause_price > 0 and not is_modified:
            if sobreprecio_pct <= 0.20 and (incremento >= 0 or media_puntos >= 3.5):
                # Formato HORA:MINUTO:SEGUNDO
                hora_str = datetime.fromtimestamp(until_date).strftime("%H:%M:%S") if until_date > now_ts else "ABIERTO 🔓"
                chollos.append((
                    nombre_j, 
                    POSICIONES_VALIDAS.get(pos_id, "JUG"), 
                    precio, 
                    clause_price, 
                    round(sobreprecio_pct * 100, 1), 
                    media_puntos, 
                    hora_str,
                    incremento
                ))

    if chollos:
        return (nombre_rival, chollos)
    return None

def generar_reporte_clausulas():
    dict_equipos, dict_jugadores = cargar_datos_globales()
    url_league = "https://biwenger.as.com/api/v2/league?fields=*,users(id,name)"
    res = http_session.get(url_league)
    
    if res.status_code != 200:
        return "❌ Error al obtener los datos de la liga."

    users_list = res.json().get('data', {}).get('users', [])
    lineas = ["🏆 <b>OPORTUNIDADES DE CLÁUSULA</b>\n"]
    now_ts = int(time.time())

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(obtener_chollos_usuario, u, dict_jugadores, now_ts) for u in users_list]
        for future in as_completed(futures):
            res_u = future.result()
            if res_u:
                nombre_rival, chollos = res_u
                nombre_rival_clean = limpiar_texto_html(nombre_rival).upper()
                
                lineas.append("━━━━━━━━━━━━━━━━━━━━")
                lineas.append(f"👤 <b>RIVAL: {nombre_rival_clean}</b>\n")
                
                for c in chollos:
                    nombre_j = limpiar_texto_html(c[0])
                    pos_j = limpiar_texto_html(c[1])
                    hora_j = limpiar_texto_html(c[6])
                    
                    inc = c[7]
                    signo = "+" if inc > 0 else ""
                    
                    lineas.append(f"▫️ <b>{nombre_j}</b> ({pos_j}) ➔ ⏰ <b>{hora_j}</b>")
                    lineas.append(
                        f"   💰 <b>VM:</b> {fmt_eur(c[2])} (<b>{signo}{fmt_eur(inc)}</b>) | "
                        f"<b>Cláusula:</b> {fmt_eur(c[3])} (<b>+{c[4]}%</b>) | "
                        f"<b>Media:</b> {c[5]}\n"
                    )

    if len(lineas) <= 1:
        return "<i>No hay cláusulas atractivas para ejecutar hoy.</i>"

    lineas.append("━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lineas)

# ==========================================
# ENVÍO A TELEGRAM Y EJECUCIÓN ÚNICA
# ==========================================
def enviar_telegram(mensaje):
    """Envía el reporte formateado directamente por la API Bot de Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    MAX_LEN = 3800
    if len(mensaje) > MAX_LEN:
        lineas = mensaje.split('\n')
        bloque = ""
        for linea in lineas:
            if len(bloque) + len(linea) + 1 > MAX_LEN:
                requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": bloque, "parse_mode": "HTML"})
                bloque = ""
            bloque += linea + "\n"
        if bloque:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": bloque, "parse_mode": "HTML"})
    else:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": mensaje,
            "parse_mode": "HTML"
        }
        res = requests.post(url, json=payload)
        if res.status_code != 200:
            print(f"❌ Error al enviar mensaje a Telegram: {res.text}")

def main():
    print("🔄 Consultando oportunidades de cláusula en Biwenger...")
    reporte = generar_reporte_clausulas()
    
    print("🚀 Enviando reporte a Telegram...")
    enviar_telegram(reporte)
    print("✅ ¡Proceso completado con éxito! Finalizando script.")

if __name__ == "__main__":
    main()