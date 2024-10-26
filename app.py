from flask import Flask, render_template, jsonify
import requests
import os
import time
import threading
from datetime import datetime  # Importar datetime

app = Flask(__name__)

# Caché para almacenar los datos de los jugadores
cache = {
    "datos_jugadores": None,
    "timestamp": 0
}
CACHE_TIMEOUT = 300  # 300 segundos = 5 minutos

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

def obtener_estado_partida(summoner_id, api_key):
    url = f"https://euw1.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{summoner_id}?api_key={api_key}"
    try:
        response = requests.get(url)
        return response.status_code == 200
    except:
        return False

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

def obtener_datos_jugadores():
    global cache

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
                en_partida = obtener_estado_partida(summoner_id, api_key)

                if elo_info:
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
                            "puuid": puuid,
                            "en_partida": en_partida
                        }
                        todos_los_datos.append(datos_jugador)

    cache['datos_jugadores'] = todos_los_datos
    cache['timestamp'] = time.time()
    
    return todos_los_datos, cache['timestamp']

def formatear_timestamp(timestamp):
    dt_object = datetime.fromtimestamp(timestamp)  # Convertir el timestamp a datetime
    return dt_object.strftime("%d-%m-%Y %H:%M:%S")  # Formato deseado

@app.route('/')
def index():
    datos_jugadores, timestamp = obtener_datos_jugadores()
    formatted_timestamp = formatear_timestamp(timestamp)  # Formatear timestamp
    # Enviar el timestamp formateado para ser procesado en la plantilla
    return render_template('index.html', datos_jugadores=datos_jugadores, timestamp=formatted_timestamp)

@app.route('/estado-partida')
def estado_partida():
    api_key = os.environ.get('RIOT_API_KEY')
    datos_jugadores = cache.get('datos_jugadores', [])
    estados = []
    
    for jugador in datos_jugadores:
        summoner_info = obtener_id_invocador(api_key, jugador['puuid'])
        if summoner_info:
            en_partida = obtener_estado_partida(summoner_info['id'], api_key)
            estados.append({
                'puuid': jugador['puuid'],
                'en_partida': en_partida
            })
    
    return jsonify(estados)

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
