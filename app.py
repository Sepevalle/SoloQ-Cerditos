from flask import Flask, render_template
import requests
import os
import time
import threading
import json

app = Flask(__name__)

# Caché para almacenar los datos de los jugadores
cache = {
    "datos_jugadores": None,
    "timestamp": 0
}
CACHE_TIMEOUT = 300  # 5 minutos

# Para proteger la caché en un entorno multihilo
cache_lock = threading.Lock()  # Crear un lock

# Cargar datos de campeones desde el archivo JSON
def cargar_datos_campeones():
    with open("champion.json", "r") as f:
        data = json.load(f)
        return {int(campeon["key"]): campeon["id"] for campeon in data["data"].values()}

# Diccionario global de campeones
campeones_dict = cargar_datos_campeones()

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

def obtener_datos_jugadores():
    global cache

    with cache_lock:
        if cache['datos_jugadores'] is not None and (time.time() - cache['timestamp']) < CACHE_TIMEOUT:
            return cache['datos_jugadores'], cache['timestamp']
        
        api_key = os.environ.get('RIOT_API_KEY', 'YOUR_RIOT_API_KEY')
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
                        en_partida = False
                        campeon_actual = None
                        url_imagen_campeon = None
                        
                        # Comprobar si el jugador está en partida
                        url_partida = f"https://euw1.api.riotgames.com/lol/spectator/v4/active-games/by-summoner/{summoner_id}?api_key={api_key}"
                        response_partida = requests.get(url_partida)

                        if response_partida.status_code == 200:
                            partida = response_partida.json()
                            en_partida = True
                            for participante in partida['participants']:
                                if participante['summonerId'] == summoner_id:
                                    champion_id = participante['championId']
                                    campeon_actual = campeones_dict.get(champion_id, "Desconocido")
                                    url_imagen_campeon = f"http://ddragon.leagueoflegends.com/cdn/13.18.1/img/champion/{campeon_actual}.png"
                                    break

                        riot_id_modified = riot_id.replace("#", "-")
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
                                "url_perfil": url_perfil,
                                "url_ingame": url_ingame,
                                "en_partida": en_partida,
                                "campeon_actual": campeon_actual,  
                                "url_imagen_campeon": url_imagen_campeon,  
                                "valor_clasificacion": calcular_valor_clasificacion(
                                    entry.get('tier', 'Sin rango'),
                                    entry.get('rank', ''),
                                    entry.get('leaguePoints', 0)
                                )
                            }
                            todos_los_datos.append(datos_jugador)

        cache['datos_jugadores'] = todos_los_datos
        cache['timestamp'] = time.time()

        return todos_los_datos, cache['timestamp']

@app.route('/')
def index():
    datos_jugadores, timestamp = obtener_datos_jugadores()
    return render_template('index.html', datos_jugadores=datos_jugadores, timestamp=timestamp)

# Mantener la app activa
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
