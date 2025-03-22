from flask import Flask, render_template, request, jsonify
import requests
import os
import time
import threading
import json
import g4f  # Importar GPT-4 Free

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

# Función para el chatbot con GPT-4 gratis
def get_chatbot_response(user_message):
    global conversation_history

    # Agregar mensaje del usuario al historial
    conversation_history.append({"role": "user", "content": user_message})

    # Construir el contexto inicial
    context = get_players_context()
    system_message = {
        "role": "system",
        "content": f"Eres un asistente útil para jugadores de League of Legends. Usa este contexto para responder:\n{context}"
    }

    # Construir historial de mensajes (últimos 5 para optimizar)
    messages = [system_message] + conversation_history[-5:]

    try:
        response = g4f.ChatCompletion.create(
            model="gpt-4",  # Se usa GPT-4 gratis
            messages=messages
        )
        assistant_response = response  # GPT-4 Free devuelve el texto directamente
        conversation_history.append({"role": "assistant", "content": assistant_response})
        return assistant_response
    except Exception as e:
        return f"Error al procesar con GPT-4 Free: {str(e)}"

# Ruta principal
@app.route('/')
def index():
    try:
        datos_jugadores, timestamp = obtener_datos_jugadores()
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
