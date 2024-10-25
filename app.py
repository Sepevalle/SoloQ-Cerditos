from flask import Flask, render_template, jsonify
import requests
import os
import time
import threading

app = Flask(__name__)

# Caché general para datos de jugadores
cache = {
    "datos_jugadores": None,
    "timestamp": 0
}
CACHE_TIMEOUT = 300  # 5 minutos

# Caché específico para estados de partida
cache_estado_partida = {
    "datos": None,
    "timestamp": 0
}
CACHE_TIMEOUT_PARTIDA = 120  # 2 minutos

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


def obtener_partida(api_key, summoner_id):
    try:
        url = f"https://euw1.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}?api_key={api_key}"
        response = requests.get(url)
        
        if response.status_code == 429:  # Rate limit excedido
            retry_after = int(response.headers.get('Retry-After', 5))
            time.sleep(retry_after)
            return obtener_partida(api_key, summoner_id)
            
        return response.status_code == 200
        
    except Exception as e:
        print(f"Error al obtener estado de partida: {e}")
        return False

def obtener_datos_jugadores():
    global cache

    if cache['datos_jugadores'] is not None and (time.time() - cache['timestamp']) < CACHE_TIMEOUT:
        return cache['datos_jugadores'], cache['timestamp']

    api_key = os.environ.get('RIOT_API_KEY')
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
                en_partida = obtener_partida(api_key, summoner_id)

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
                            "summoner_id": summoner_id,
                            "en_partida": en_partida
                        }
                        todos_los_datos.append(datos_jugador)

    cache['datos_jugadores'] = todos_los_datos
    cache['timestamp'] = time.time()
    
    return todos_los_datos, cache['timestamp']

@app.route('/estado-partida')
def estado_partida():
    datos_jugadores, _ = obtener_datos_jugadores()
    estados = []
    api_key = os.environ.get('RIOT_API_KEY')

    for jugador in datos_jugadores:
        summoner_id = jugador.get('summoner_id')
        if summoner_id:
            estado = obtener_partida(api_key, summoner_id)
            estados.append({
                'summoner_id': summoner_id,
                'en_partida': estado
            })

    return jsonify(estados)

@app.route('/')
def index():
    datos_jugadores, timestamp = obtener_datos_jugadores()
    return render_template('index.html', datos_jugadores=datos_jugadores, timestamp=timestamp)


    
  

# Función que hará peticiones periódicas a la app para evitar hibernación
def keep_alive():
    while True:
        try:
            # Cambia la URL por la de tu aplicación en Render
            requests.get('https://soloq-cerditos.onrender.com/')
            print("Manteniendo la aplicación activa con una solicitud.")
        except requests.exceptions.RequestException as e:
            print(f"Error: {e}")
        time.sleep(600)  # Esperar 5 minutos

if __name__ == "__main__":
    # Iniciar el hilo para mantener la app activa
    thread = threading.Thread(target=keep_alive)
    thread.daemon = True  # El hilo se detendrá si el programa principal se detiene
    thread.start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
