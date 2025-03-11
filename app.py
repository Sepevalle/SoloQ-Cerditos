import os
import requests
from flask import Flask, render_template, request, jsonify
from datetime import datetime

app = Flask(__name__)

# Clave API de Riot Games (obtenida de variable de entorno)
RIOT_API_KEY = os.environ.get('RIOT_API_KEY')
RIOT_API_URL = "https://la2.api.riotgames.com/lol"

# Clave API de Hugging Face (obtenida de variable de entorno)
HUGGINGFACE_API_KEY = os.environ.get('HUGGINGFACE_API_KEY')
HUGGINGFACE_API_URL = "https://api-inference.huggingface.co/models/facebook/blenderbot-400M-distill"

# Datos de los jugadores
jugadores = {
    "Sepevalle": {"game_name": "Sepevalle", "tag_line": "LAN"},
    "Kaka": {"game_name": "Kaka", "tag_line": "LAN"},
    "Lechita": {"game_name": "Lechita", "tag_line": "LAN"},
    "Misterpig": {"game_name": "Misterpig", "tag_line": "LAN"},
    "Nerf": {"game_name": "Nerf", "tag_line": "LAN"},
    "Poke": {"game_name": "Poke", "tag_line": "LAN"},
    "Sora": {"game_name": "Sora", "tag_line": "LAN"},
}

# Función para obtener datos de un jugador
def get_player_data(jugador):
    try:
        game_name = jugadores[jugador]["game_name"]
        tag_line = jugadores[jugador]["tag_line"]

        # Obtener PUUID
        puuid_url = f"https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}?api_key={RIOT_API_KEY}"
        puuid_response = requests.get(puuid_url)
        puuid_response.raise_for_status()
        puuid = puuid_response.json()['puuid']

        # Obtener summonerId
        summoner_url = f"{RIOT_API_URL}/summoner/v4/summoners/by-puuid/{puuid}?api_key={RIOT_API_KEY}"
        summoner_response = requests.get(summoner_url)
        summoner_response.raise_for_status()
        summoner_id = summoner_response.json()['id']

        # Obtener datos de liga (SoloQ y Flex)
        league_url = f"{RIOT_API_URL}/league/v4/entries/by-summoner/{summoner_id}?api_key={RIOT_API_KEY}"
        league_response = requests.get(league_url)
        league_response.raise_for_status()
        league_data = league_response.json()

        # Determinar si el jugador está en partida
        spectator_url = f"{RIOT_API_URL}/spectator/v4/active-games/by-summoner/{summoner_id}?api_key={RIOT_API_KEY}"
        spectator_response = requests.get(spectator_url)
        en_partida = spectator_response.status_code == 200
        nombre_campeon = None
        if en_partida:
            game_data = spectator_response.json()
            participant = next((p for p in game_data['participants'] if p['summonerId'] == summoner_id), None)
            if participant:
                nombre_campeon = participant['championId']
                # Convertir championId a nombre
                champion_data_url = "http://ddragon.leagueoflegends.com/cdn/14.20.1/data/en_US/champion.json"
                champion_response = requests.get(champion_data_url)
                champion_data = champion_response.json()['data']
                for champ in champion_data.values():
                    if int(champ['key']) == nombre_campeon:
                        nombre_campeon = champ['id']
                        break

        # Procesar datos de liga
        soloq_data = next((entry for entry in league_data if entry['queueType'] == 'RANKED_SOLO_5x5'), None)
        flex_data = next((entry for entry in league_data if entry['queueType'] == 'RANKED_FLEX_SR'), None)

        # Usar SoloQ por defecto, si no existe usar Flex, si no existe usar valores por defecto
        if soloq_data:
            queue_type = 'RANKED_SOLO_5x5'
            tier = soloq_data['tier']
            rank = soloq_data['rank']
            league_points = soloq_data['leaguePoints']
            wins = soloq_data['wins']
            losses = soloq_data['losses']
            valor_clasificacion = calculate_valor_clasificacion(tier, rank, league_points)
        elif flex_data:
            queue_type = 'RANKED_FLEX_SR'
            tier = flex_data['tier']
            rank = flex_data['rank']
            league_points = flex_data['leaguePoints']
            wins = flex_data['wins']
            losses = flex_data['losses']
            valor_clasificacion = calculate_valor_clasificacion(tier, rank, league_points)
        else:
            queue_type = 'UNRANKED'
            tier = 'UNRANKED'
            rank = ''
            league_points = 0
            wins = 0
            losses = 0
            valor_clasificacion = 0

        return {
            'jugador': jugador,
            'game_name': game_name,
            'queue_type': queue_type,
            'tier': tier,
            'rank': rank,
            'league_points': league_points,
            'wins': wins,
            'losses': losses,
            'valor_clasificacion': valor_clasificacion,
            'en_partida': en_partida,
            'nombre_campeon': nombre_campeon,
            'url_perfil': f"https://www.op.gg/summoners/lan/{game_name}-{tag_line}",
            'url_ingame': f"https://www.op.gg/summoners/lan/{game_name}-{tag_line}/ingame"
        }
    except Exception as e:
        print(f"Error al obtener datos de {jugador}: {str(e)}")
        return {
            'jugador': jugador,
            'game_name': game_name,
            'queue_type': 'UNRANKED',
            'tier': 'UNRANKED',
            'rank': '',
            'league_points': 0,
            'wins': 0,
            'losses': 0,
            'valor_clasificacion': 0,
            'en_partida': False,
            'nombre_campeon': None,
            'url_perfil': f"https://www.op.gg/summoners/lan/{game_name}-{tag_line}",
            'url_ingame': f"https://www.op.gg/summoners/lan/{game_name}-{tag_line}/ingame"
        }

# Función para calcular el valor de clasificación (para ordenar)
def calculate_valor_clasificacion(tier, rank, league_points):
    tier_values = {
        'UNRANKED': 0,
        'IRON': 1000,
        'BRONZE': 2000,
        'SILVER': 3000,
        'GOLD': 4000,
        'PLATINUM': 5000,
        'EMERALD': 6000,
        'DIAMOND': 7000,
        'MASTER': 8000,
        'GRANDMASTER': 9000,
        'CHALLENGER': 10000
    }
    rank_values = {'IV': 0, 'III': 250, 'II': 500, 'I': 750}
    base_value = tier_values.get(tier, 0)
    rank_value = rank_values.get(rank, 0) if rank else 0
    return base_value + rank_value + league_points

# Función para obtener el contexto de los jugadores
def get_players_context():
    datos_jugadores = [get_player_data(jugador) for jugador in jugadores.keys()]
    context = "Información de los jugadores:\n"
    for jugador in datos_jugadores:
        context += f"- {jugador['jugador']} (Game Name: {jugador['game_name']}) está en {jugador['tier']} {jugador['rank']} con {jugador['league_points']} LP, "
        context += f"Wins: {jugador['wins']}, Losses: {jugador['losses']}"
        if jugador['en_partida']:
            context += f", actualmente en partida jugando con {jugador['nombre_campeon']}"
        else:
            context += ", no está en partida"
        context += ".\n"
    return context

# Función para el chatbot con Hugging Face
def get_chatbot_response(user_message):
    if not HUGGINGFACE_API_KEY:
        return "Error: La clave API de Hugging Face no está configurada. Por favor, configura la variable de entorno HUGGINGFACE_API_KEY."
    
    context = get_players_context()
    full_message = f"{context}Mensaje del usuario: {user_message}"
    
    headers = {
        "Authorization": f"Bearer {HUGGINGFACE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Formato específico para BlenderBot
    payload = {
        "past_user_inputs": [context],  # Contexto como entrada previa
        "generated_responses": [],      # Respuestas previas (vacías por ahora)
        "text": user_message            # Mensaje actual del usuario
    }
    
    try:
        response = requests.post(HUGGINGFACE_API_URL, headers=headers, json=payload, timeout=10)
        response.raise_for_status()  # Esto lanzará una excepción si hay un error
        data = response.json()
        return data["generated_text"] if "generated_text" in data else "No se recibió respuesta del modelo."
    except requests.exceptions.HTTPError as e:
        return f"Error al procesar: {str(e)}"
    except Exception as e:
        return f"Error al procesar: {str(e)}"

# Ruta principal
@app.route('/')
def index():
    datos_jugadores = [get_player_data(jugador) for jugador in jugadores.keys()]
    return render_template('index.html', datos_jugadores=datos_jugadores, timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

# Ruta para el chatbot
@app.route('/chat', methods=['GET'])
def chat():
    user_message = request.args.get('message', '')
    if not user_message:
        return jsonify({"reply": "Por favor, escribe un mensaje."})
    
    response = get_chatbot_response(user_message)
    return jsonify({"reply": response})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
