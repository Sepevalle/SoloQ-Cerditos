from flask import Flask, render_template
import requests
import os
import time
import threading

app = Flask(__name__)

# Caché para almacenar los datos de los jugadores
cache = {
    "datos_jugadores": None,
    "timestamp": 0
}
CACHE_TIMEOUT = 300  # 5 minutos

# Para proteger la caché en un entorno multihilo
cache_lock = threading.Lock()  # Crear un lock

def obtener_puuid(api_key, riot_id, region):
    url = f"https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{riot_id}/{region}?api_key={api_key}"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error al obtener PUUID: {response.status_code} - {response.text}")
        return None

def obtener_id_invocador(api_key, puuid):
    url = f"https://euw1.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={api_key}"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error al obtener ID del invocador: {response.status_code} - {response.text}")
        return None

def obtener_elo(api_key, summoner_id):
    url = f"https://euw1.api.riotgames.com/lol/league/v4/entries/by-summoner/{summoner_id}?api_key={api_key}"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error al obtener Elo: {response.status_code} - {response.text}")
        return None

def esta_en_partida(api_key, puuid):
    url = f"https://euw1.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}?api_key={api_key}"
    response = requests.get(url)
    return response.status_code == 200  # Devuelve True si está en partida, False si no

def leer_cuentas(url):
    try:
        response = requests.get(url)
        if response.status_code == 200:
            contenido = response.text.strip().split(';')
            cuentas = []
            for linea in contenido:
                partes = linea.split(',')
                if len(partes) == 2:
                    riot_id = partes[0].strip()
                    jugador = partes[1].strip()
                    cuentas.append((riot_id, jugador))
            return cuentas
        else:
            print(f"Error al leer el archivo: {response.status_code}")
            return []
    except Exception as e:
        print(f"Error al leer las cuentas: {e}")
        return []

def calcular_valor_clasificacion(tier, rank, league_points):
    tierOrden = {
        "CHALLENGER": 8,
        "GRANDMASTER": 7,
        "MASTER": 6,
        "EMERALD": 5,
        "PLATINUM": 4,
        "GOLD": 3,
        "SILVER": 2,
        "BRONZE": 1,
        "IRON": 0
    }

    rankOrden = {
        "I": 4,
        "II": 3,
        "III": 2,
        "IV": 1
    }

    tierValue = tierOrden.get(tier.upper(), 0)
    rankValue = rankOrden.get(rank, 0)

    return tierValue * 10000 + rankValue * 1000 + league_points

def obtener_datos_jugadores():
    global cache

    # Bloqueo para controlar el acceso a la caché
    with cache_lock:
        if cache['datos_jugadores'] is not None and (time.time() - cache['timestamp']) < CACHE_TIMEOUT:
            return cache['datos_jugadores'], cache['timestamp']
        
        api_key = os.environ.get('RIOT_API_KEY', 'RGAPI-68c71be0-a708-4d02-b503-761f6a83e3ae')
        url_cuentas = "https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/cuentas.txt"
        cuentas = leer_cuentas(url_cuentas)
        todos_los_datos = []

        for riot_id, jugador in cuentas:
            region = riot_id.split('#')[-1]
            puuid_info = obtener_puuid(api_key, riot_id.split('#')[0], region)
            if puuid_info:
                puuid = puuid_info['puuid']
                summoner_info = obtener_id_invocador(api_key, puuid)
                if summoner_info:
                    summoner_id = summoner_info['id']
                    elo_info = obtener_elo(api_key, summoner_id)

                    if elo_info:
                        en_partida = esta_en_partida(api_key, puuid)  # Verifica si el jugador está en partida

                        # Definir las URLs del perfil y del estado en partida
                        riot_id_modified = riot_id.replace("#", "-")

                        # Genera las URLs
                        url_perfil = f"https://www.op.gg/summoners/euw/{riot_id_modified}"
                        url_ingame = f"https://www.op.gg/summoners/euw/{riot_id_modified}/ingame"

                        for entry in elo_info:
                            datos_jugador = {
                                "game_name": riot_id,
                                "queue_type": entry.get('queueType', 'Desconocido'),
                                "tier": entry.get('tier', 'Sin rango'),
                                "rank": entry.get('rank', ''),
                                "league_points": entry.get('leaguePoints', 0),
                                "wins": entry.get('wins', 0),
                                "losses": entry.get('losses', 0),
                                "jugador": jugador,
                                "url_perfil": url_perfil,  # URL del perfil
                                "url_ingame": url_ingame,    # URL del estado en partida
                                "en_partida": en_partida,     # Agrega el estado del jugador
                                "valor_clasificacion": calcular_valor_clasificacion(
                                    entry.get('tier', 'Sin rango'),
                                    entry.get('rank', ''),
                                    entry.get('leaguePoints', 0)
                                )  # Calcular el valor de clasificación
                            }
                            todos_los_datos.append(datos_jugador)

        # Actualizar la caché solo después de completar todos los datos
        cache['datos_jugadores'] = todos_los_datos
        cache['timestamp'] = time.time()

        return todos_los_datos, cache['timestamp']

@app.route('/')
def index():
    datos_jugadores, timestamp = obtener_datos_jugadores()
    return render_template('index.html', datos_jugadores=datos_jugadores, timestamp=timestamp)

# Función que hará peticiones periódicas a la app para evitar hibernación
def keep_alive():
    while True:
        try:
            requests.get('https://soloq-cerditos.onrender.com/')
            print("Manteniendo la aplicación activa con una solicitud.")
        except requests.exceptions.RequestException as e:
            print(f"Error: {e}")
        time.sleep(600)

if __name__ == "__main__":
    thread = threading.Thread(target=keep_alive)
    thread.daemon = True
    thread.start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
