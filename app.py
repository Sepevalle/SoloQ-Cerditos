from flask import Flask, render_template, request, jsonify
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
cache_lock = threading.Lock()

# Clave API de Hugging Face (obtenida de variable de entorno)
HUGGINGFACE_API_KEY = os.environ.get('HUGGINGFACE_API_KEY')
HUGGINGFACE_API_URL = "https://api-inference.huggingface.co/models/microsoft/DialoGPT-medium"

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

# Función para obtener el contexto de los datos de jugadores
def get_players_context():
    datos_jugadores, _ = obtener_datos_jugadores()
    context = "Lista de jugadores y su estado:\n"
    for jugador in datos_jugadores:
        context += f"- {jugador['jugador']} ({jugador['game_name']}): {jugador['tier']} {jugador['rank']}, "
        if jugador['en_partida']:
            context += f"en partida con {jugador['nombre_campeon']}.\n"
        else:
            context += "no en partida.\n"
    return context

# Función para el chatbot con historial conversacional
def get_chatbot_response(user_message):
    global conversation_history

    if not HUGGINGFACE_API_KEY:
        return "Error: La clave API de Hugging Face no está configurada. Por favor, configura la variable de entorno HUGGINGFACE_API_KEY."

    # Añadir el mensaje del usuario al historial
    conversation_history.append({"role": "user", "content": user_message})

    # Construir el contexto inicial
    context = get_players_context()
    prompt = f"Contexto: {context}\n"
    for entry in conversation_history[-5:]:  # Limitar a los últimos 5 intercambios
        prompt += f"{entry['role']}: {entry['content']}\n"
    prompt += "assistant: "

    headers = {
        "Authorization": f"Bearer {HUGGINGFACE_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "inputs": prompt,
        "parameters": {"max_length": 100, "temperature": 0.7}
    }

    try:
        response = requests.post(HUGGINGFACE_API_URL, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        print(f"Respuesta cruda de la API: {data}")  # Depuración
        generated_text = data[0]["generated_text"] if isinstance(data, list) and "generated_text" in data[0] else "Lo siento, no sé cómo responder."
        # Extraer solo la respuesta del assistant con mejor manejo
        lines = generated_text.split("\n")
        assistant_response = next((line for line in lines if line.startswith("assistant:")), generated_text).replace("assistant:", "").strip()
        # Añadir la respuesta al historial
        conversation_history.append({"role": "assistant", "content": assistant_response})
        return assistant_response or "¡Hola! ¿En qué puedo ayudarte con los jugadores?"
    except requests.exceptions.HTTPError as e:
        return f"Error al procesar: {str(e)}"
    except Exception as e:
        return f"Error al procesar: {str(e)}"

# Ruta principal
@app.route('/')
def index():
    try:
        datos_jugadores, timestamp = obtener_datos_jugadores()
        print(json.dumps(datos_jugadores, indent=2))
        return render_template('index.html', datos_jugadores=datos_jugadores, timestamp=timestamp)
    except Exception as e:
        print(f"Error en index: {str(e)}")
        return "Error al cargar la página", 500

# Ruta para el chatbot
@app.route('/chat', methods=['GET'])
def chat():
    user_message = request.args.get('message', '')
    if not user_message:
        return jsonify({"reply": "Por favor, envía un mensaje."})
    
    chatbot_response = get_chatbot_response(user_message)
    return jsonify({"reply": chatbot_response})

# Función para evitar hibernación
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
    print(f"Iniciando aplicación en puerto {port}")
    app.run(host='0.0.0.0', port=port)
