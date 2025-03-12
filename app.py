from flask import Flask, render_template, request, jsonify
import requests
import os
import time
import threading
import json
from openai import OpenAI
import httpx

app = Flask(__name__)

# Caché para almacenar los datos de los jugadores
cache = {
    "datos_jugadores": None,
    "timestamp": 0
}
CACHE_TIMEOUT = 300  # 5 minutos

# Para proteger la caché en un entorno multihilo
cache_lock = threading.Lock()

# Clave API de OpenAI (obtenida de variable de entorno)
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

# Crear un cliente HTTP sin proxies
http_client = httpx.Client(proxies=None)

# Inicializar el cliente de OpenAI con el cliente HTTP personalizado
openai_client = OpenAI(api_key=OPENAI_API_KEY, http_client=http_client) if OPENAI_API_KEY else None

# Historial de conversación por sesión
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
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error al obtener PUUID: {response.status_code} - {response.text}")
            return None
    except requests.RequestException as e:
        print(f"Excepción al obtener PUUID: {str(e)}")
        return None

def obtener_id_invocador(api_key, puuid):
    url = f"https://euw1.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={api_key}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error al obtener ID del invocador: {response.status_code} - {response.text}")
            return None
    except requests.RequestException as e:
        print(f"Excepción al obtener ID del invocador: {str(e)}")
        return None

def obtener_elo(api_key, summoner_id):
    url = f"https://euw1.api.riotgames.com/lol/league/v4/entries/by-summoner/{summoner_id}?api_key={api_key}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error al obtener Elo
