from flask import Flask, render_template, request, jsonify
import requests
import os
import time
import threading
import json
import g4f  # GPT-4 Free

app = Flask(__name__)

# Caché para almacenar los datos de los jugadores
cache = {
    "datos_jugadores": None,
    "timestamp": 0
}
CACHE_TIMEOUT = 300  # 5 minutos

# Para proteger la caché en un entorno multihilo
cache_lock = threading.Lock()

# Historial de conversación por sesión (simple, sin persistencia)
conversation_history = []

# Cargar campeones
def cargar_campeones():
    url_campeones = "https://ddragon.leagueoflegends.com/cdn/14.20.1/data/es_ES/champion.json"
    response = requests.get(url_campeones)
    if response.status_code == 200:
        campeones = response.json()["data"]
        return {int(campeon["key"]): campeon["id"] for campeon in campeones.values()}
    else:
        print(f"Error al cargar campeones: {response.status_code}")
        return {}

campeones = cargar_campeones()

def obtener_nombre_campeon(champion_id):
    return campeones.get(champion_id, "Desconocido")

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
    if response.status_code == 200:
        data = response.json()
        for participant in data.get("participants", []):
            if participant['puuid'] == puuid:
                return participant.get('championId', None)
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

def calcular_valor_clasificacion(tier, rank, league_points):
    tierOrden = {
        "CHALLENGER": 7,
        "GRANDMASTER": 7,
        "MASTER": 7,
        "DIAMOND": 6,
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

    return (tierValue * 400 + rankValue * 100 + league_points - 100)

def obtener_datos_jugadores():
    global cache

    with cache_lock:
        if cache['datos_jugadores'] is not None and (time.time() - cache['timestamp']) < CACHE_TIMEOUT:
            return cache['datos_jugadores'], cache['timestamp']

        api_key = os.environ.get('RIOT_API_KEY', 'TU_RIOT_API_KEY')
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
                        champion_id = esta_en_partida(api_key, puuid)
                        riot_id_modified = riot_id.replace("#", "-")
                        url_perfil = f"https://www.op.gg/summoners/euw/{riot_id_modified}"
                        url_ingame = f"https://www.op.gg/summoners/euw/{riot_id_modified}/ingame"

                        for entry in elo_info:
                            nombre_campeon = obtener_nombre_campeon(champion_id) if champion_id else "Desconocido"

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
                                "en_partida": champion_id is not None,
                                "valor_clasificacion": calcular_valor_clasificacion(
                                    entry.get('tier', 'Sin rango'),
                                    entry.get('rank', ''),
                                    entry.get('leaguePoints', 0)
                                ),
                                "nombre_campeon": nombre_campeon,
                                "champion_id": champion_id if champion_id else "Desconocido"
                            }
                            todos_los_datos.append(datos_jugador)

        cache['datos_jugadores'] = todos_los_datos
        cache['timestamp'] = time.time()

        return todos_los_datos, cache['timestamp']

@app.route('/')
def index():
    datos_jugadores, timestamp = obtener_datos_jugadores()
    return render_template('index.html', datos_jugadores=datos_jugadores, timestamp=timestamp)

@app.route('/chat', methods=['GET'])
def chat():
    user_message = request.args.get('message', '')
    
    # Asegúrate de que g4f esté siendo llamado correctamente.
    try:
        chatbot_response = g4f.ChatCompletion.create(model="gpt-4", messages=[{"role": "user", "content": user_message}])
        return jsonify({"reply": chatbot_response['choices'][0]['message']['content']})
    except Exception as e:
        print(f"Error en el chatbot: {e}")
        return jsonify({"error": "No se pudo obtener una respuesta del chatbot."})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
