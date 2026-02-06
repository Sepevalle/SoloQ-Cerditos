"""
Script de validación para verificar que los LPs se asignan correctamente.
Detecta duplicados, inconsistencias y anomalías en la asignación de LPs.

Uso: python validate_lp_assignments.py
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timezone

def load_json_file(file_path):
    """Carga un archivo JSON de forma segura."""
    if not os.path.exists(file_path):
        print(f"⚠️ Archivo no encontrado: {file_path}")
        return {}
    
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ Error leyendo {file_path}: {e}")
        return {}

def validate_lp_history(lp_history_path, match_history_base_path="match_history"):
    """
    Valida que los LPs se asignen correctamente sin duplicados.
    """
    print("=" * 70)
    print("VALIDADOR DE ASIGNACIÓN DE LPs")
    print("=" * 70)
    
    lp_history = load_json_file(lp_history_path)
    
    if not lp_history:
        print("❌ No se pudo cargar el historial de LPs")
        return
    
    total_issues = 0
    
    # Estadísticas globales
    total_snapshots = 0
    total_duplicates = 0
    players_analyzed = 0
    
    print("\n📊 ANALIZANDO HISTORIAL DE LPs...\n")
    
    for puuid, queue_data in lp_history.items():
        players_analyzed += 1
        player_issues = 0
        
        for queue_name, snapshots in queue_data.items():
            if not snapshots:
                continue
            
            total_snapshots += len(snapshots)
            
            # Detector de duplicados
            seen_states = {}
            consecutive_duplicates = []
            
            for i, snapshot in enumerate(snapshots):
                timestamp = snapshot.get('timestamp')
                elo = snapshot.get('elo')
                
                key = (timestamp, elo)
                
                if key in seen_states:
                    total_duplicates += 1
                    consecutive_duplicates.append({
                        'index': i,
                        'timestamp': timestamp,
                        'elo': elo,
                        'previous_index': seen_states[key]
                    })
                else:
                    seen_states[key] = i
            
            if consecutive_duplicates:
                player_issues += len(consecutive_duplicates)
                print(f"⚠️ PUUID: {puuid[:16]}... | Cola: {queue_name}")
                print(f"   ├─ Duplicados encontrados: {len(consecutive_duplicates)}")
                for dup in consecutive_duplicates[:5]:  # Mostrar máximo 5
                    timestamp_str = datetime.fromtimestamp(dup['timestamp']/1000, tz=timezone.utc).isoformat()
                    print(f"   │  ├─ ELO {dup['elo']} en {timestamp_str}")
                if len(consecutive_duplicates) > 5:
                    print(f"   │  └─ ... y {len(consecutive_duplicates) - 5} más")
                print()
        
        if player_issues > 0:
            total_issues += player_issues
    
    # Resumen
    print("\n" + "=" * 70)
    print("📋 RESUMEN DE VALIDACIÓN")
    print("=" * 70)
    print(f"Jugadores analizados: {players_analyzed}")
    print(f"Total de snapshots: {total_snapshots}")
    print(f"Snapshots duplicados: {total_duplicates}")
    
    if total_duplicates == 0:
        print("\n✅ PERFECTO: No se encontraron duplicados")
    else:
        percentage = (total_duplicates / total_snapshots * 100) if total_snapshots > 0 else 0
        if percentage < 1:
            print(f"\n⚠️ ACEPTABLE: {percentage:.2f}% de duplicados (mínimo esperado)")
        else:
            print(f"\n❌ PROBLEMA: {percentage:.2f}% de duplicados detectados")
    
    print("\n📌 CONSEJOS:")
    print("  • Si ves duplicados: El lp_tracker está guardando snapshots innecesarios")
    print("  • Aumenta el intervalo entre snapshots si hay muchos duplicados")
    print("  • Verifica los logs del lp_tracker para detalles")
    
    return total_duplicates == 0

def validate_match_lp_assignments(match_history_base_path="match_history"):
    """
    Valida que cada partida tenga exactamente un valor de LP_change.
    """
    print("\n" + "=" * 70)
    print("VALIDADOR DE ASIGNACIÓN DE LPs POR PARTIDA")
    print("=" * 70)
    
    if not os.path.exists(match_history_base_path):
        print(f"⚠️ Directorio no encontrado: {match_history_base_path}")
        return
    
    # Cargar todos los archivos de historial de partidas
    matches_with_lp = 0
    matches_without_lp = 0
    matches_anomalous = 0
    
    print("\n📊 ANALIZANDO ASIGNACIÓN DE LPs A PARTIDAS...\n")
    
    for filename in os.listdir(match_history_base_path):
        if not filename.endswith('.json'):
            continue
        
        file_path = os.path.join(match_history_base_path, filename)
        match_data = load_json_file(file_path)
        
        if not match_data or 'matches' not in match_data:
            continue
        
        for match in match_data.get('matches', []):
            lp_change = match.get('lp_change_this_game')
            queue_id = match.get('queue_id')
            win = match.get('win')
            
            # Solo validar partidas clasificatorias
            if queue_id not in [420, 440]:
                continue
            
            if lp_change is None:
                matches_without_lp += 1
                # Esto es aceptable si no hay suficientes snapshots
            elif isinstance(lp_change, (int, float)):
                matches_with_lp += 1
                
                # Detectar anomalías (ganancias/pérdidas extremas)
                if queue_id == 420:  # Solo/Duo
                    # Rango normal: -30 a +30 LP
                    if lp_change > 50 or lp_change < -50:
                        matches_anomalous += 1
                        match_id = match.get('match_id')
                        print(f"🚨 ANOMALÍA: {match_id[:16]}...")
                        print(f"   ├─ LP Change: {lp_change} (extremo para Ranked Solo)")
                        print(f"   ├─ Resultado: {'Victoria' if win else 'Derrota'}")
                        print(f"   └─ Pre-game ELO: {match.get('pre_game_valor_clasificacion')}")
                        print()
    
    total_matches = matches_with_lp + matches_without_lp
    
    print("\n" + "=" * 70)
    print("📋 RESUMEN POR PARTIDA")
    print("=" * 70)
    
    if total_matches > 0:
        coverage = (matches_with_lp / total_matches * 100)
        print(f"Partidas clasificatorias: {total_matches}")
        print(f"Con LP asignado: {matches_with_lp} ({coverage:.1f}%)")
        print(f"Sin LP asignado: {matches_without_lp} ({100-coverage:.1f}%)")
        print(f"Anomalías detectadas: {matches_anomalous}")
        
        if coverage > 90:
            print(f"\n✅ EXCELENTE: {coverage:.1f}% de cobertura")
        elif coverage > 70:
            print(f"\n⚠️ ACEPTABLE: {coverage:.1f}% de cobertura")
        else:
            print(f"\n❌ INSUFICIENTE: {coverage:.1f}% de cobertura")
    else:
        print("ℹ️ No hay partidas clasificatorias para validar")
    
    print("\n📌 NOTAS:")
    print("  • Sin LP asignado (null) es normal para partidas antiguas")
    print("  • Incrementa snapshots si necesitas mejor cobertura")
    print("  • Las anomalías pueden indicar un problema con los snapshots")

if __name__ == "__main__":
    # Configuración
    LP_HISTORY_FILE = "lp_history.json"
    MATCH_HISTORY_DIR = "match_history"
    
    # Ejecutar validaciones
    validate_lp_history(LP_HISTORY_FILE, MATCH_HISTORY_DIR)
    validate_match_lp_assignments(MATCH_HISTORY_DIR)
    
    print("\n" + "=" * 70)
    print("✅ VALIDACIÓN COMPLETADA")
    print("=" * 70)
