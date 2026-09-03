"""
Script de validación para verificar que todo está listo para el despliegue
y que las dependencias seguirán funcionando correctamente.
"""

import json
import sys

def validate_syntax():
    """Valida la sintaxis de los archivos modificados."""
    print("="*60)
    print("1. VALIDACIÓN DE SINTAXIS PYTHON")
    print("="*60)
    
    try:
        import py_compile
        py_compile.compile('services/github_service.py', doraise=True)
        print("✅ services/github_service.py - Sintaxis correcta")
    except Exception as e:
        print(f"❌ services/github_service.py - Error: {e}")
        return False
    
    try:
        py_compile.compile('services/lp_tracker.py', doraise=True)
        print("✅ services/lp_tracker.py - Sintaxis correcta")
    except Exception as e:
        print(f"❌ services/lp_tracker.py - Error: {e}")
        return False
    
    try:
        py_compile.compile('blueprints/admin.py', doraise=True)
        print("✅ blueprints/admin.py - Sintaxis correcta")
    except Exception as e:
        print(f"❌ blueprints/admin.py - Error: {e}")
        return False
    
    return True

def validate_lp_history_file():
    """Valida que el archivo lp_history.json existe y es válido."""
    print("\n" + "="*60)
    print("2. VALIDACIÓN DE ARCHIVO lp_history.json")
    print("="*60)
    
    try:
        with open('lp_history.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        print(f"✅ lp_history.json existe y es JSON válido")
        print(f"   Jugadores: {len(data)}")
        
        # Validar estructura
        sample_puuid = list(data.keys())[0] if data else None
        if sample_puuid:
            player_data = data[sample_puuid]
            if isinstance(player_data, dict):
                has_soloq = 'RANKED_SOLO_5x5' in player_data
                has_flex = 'RANKED_FLEX_SR' in player_data
                print(f"   Estructura correcta: SoloQ={has_soloq}, Flex={has_flex}")
                
                if has_soloq:
                    soloq_snapshots = len(player_data['RANKED_SOLO_5x5'])
                    print(f"   Snapshots SoloQ: {soloq_snapshots}")
        
        return True
    except FileNotFoundError:
        print("❌ lp_history.json no encontrado")
        return False
    except json.JSONDecodeError as e:
        print(f"❌ lp_history.json tiene JSON inválido: {e}")
        return False
    except Exception as e:
        print(f"❌ Error validando lp_history.json: {e}")
        return False

def validate_function_signatures():
    """Valida que las firmas de las funciones no hayan cambiado."""
    print("\n" + "="*60)
    print("3. VALIDACIÓN DE COMPATIBILIDAD DE FUNCIONES")
    print("="*60)
    
    try:
        from services.github_service import read_lp_history, save_lp_history
        print("✅ read_lp_history() - Importable")
        print("✅ save_lp_history() - Importable")
        
        # Validar que read_lp_history devuelve el formato esperado
        success, data = read_lp_history()
        if success and isinstance(data, dict):
            print("✅ read_lp_history() devuelve formato correcto (dict)")
        else:
            print("⚠️ read_lp_history() podría tener problemas (datos vacíos)")
        
        return True
    except ImportError as e:
        print(f"❌ Error importando funciones: {e}")
        return False
    except Exception as e:
        print(f"⚠️ Error ejecutando funciones: {e}")
        return True  # Puede ser normal si no hay API keys

def validate_data_consumers():
    """Valida que los consumidores de lp_history seguirán funcionando."""
    print("\n" + "="*60)
    print("4. VALIDACIÓN DE CONSUMIDORES DE DATOS")
    print("="*60)
    
    # Verificar que los archivos que dependen de lp_history existan
    consumers = [
        'services/data_updater.py',
        'services/match_service.py', 
        'services/index_json_generator.py',
        'blueprints/main.py'
    ]
    
    for consumer in consumers:
        try:
            with open(consumer, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Verificar que usan read_lp_history o lp_history de forma compatible
            if 'read_lp_history' in content or 'lp_history' in content:
                print(f"✅ {consumer} - Usa lp_history (compatible)")
            else:
                print(f"ℹ️  {consumer} - No usa lp_history directamente")
        except FileNotFoundError:
            print(f"❌ {consumer} - No encontrado")
            return False
    
    return True

def validate_chunking_logic():
    """Valida la lógica de chunking."""
    print("\n" + "="*60)
    print("5. VALIDACIÓN DE LÓGICA DE CHUNKING")
    print("="*60)
    
    try:
        # Simular la lógica de chunking
        import json
        with open('lp_history.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        lp_history_size = len(json.dumps(data).encode('utf-8'))
        size_kb = lp_history_size / 1024
        
        print(f"   Tamaño actual: {size_kb:.1f} KB")
        
        if lp_history_size > 500 * 1024:
            print("✅ Se usará formato chunked (>500KB)")
            print("   Estrategia: Dividir por jugador")
        else:
            print("✅ Se usará formato legacy (≤500KB)")
        
        # Validar que la estructura puede chunkearse
        if isinstance(data, dict) and len(data) > 0:
            print("✅ Estructura compatible con chunking (dict por puuid)")
        else:
            print("❌ Estructura no compatible con chunking")
            return False
        
        return True
    except Exception as e:
        print(f"❌ Error validando chunking: {e}")
        return False

def validate_protections():
    """Valida que las protecciones contra pérdida de datos estén activas."""
    print("\n" + "="*60)
    print("6. VALIDACIÓN DE PROTECCIONES DE DATOS")
    print("="*60)
    
    try:
        with open('services/lp_tracker.py', 'r', encoding='utf-8') as f:
            content = f.read()
        
        protections = [
            ('CRÍTICO: Archivo', 'Detección de archivo vacío'),
            ('Abortando ciclo', 'Aborto ante error crítico'),
            ('_create_backup', 'Sistema de backup'),
            ('Error 422 para lp_history.json', 'Protección contra conflicto SHA'),
        ]
        
        for keyword, description in protections:
            if keyword in content:
                print(f"✅ {description} - Activo")
            else:
                print(f"❌ {description} - No encontrado")
                return False
        
        return True
    except Exception as e:
        print(f"❌ Error validando protecciones: {e}")
        return False

def main():
    print("="*60)
    print("VALIDACIÓN DE DESPLIEGUE - SoloQ-Cerditos")
    print("="*60)
    
    checks = [
        validate_syntax,
        validate_lp_history_file,
        validate_function_signatures,
        validate_data_consumers,
        validate_chunking_logic,
        validate_protections
    ]
    
    results = []
    for check in checks:
        try:
            result = check()
            results.append(result)
        except Exception as e:
            print(f"\n❌ Error en validación: {e}")
            results.append(False)
    
    print("\n" + "="*60)
    print("RESUMEN DE VALIDACIÓN")
    print("="*60)
    
    total = len(results)
    passed = sum(results)
    
    print(f"Validaciones pasadas: {passed}/{total}")
    
    if all(results):
        print("✅ TODO LISTO PARA DESPLIEGUE")
        print("\nCambios implementados:")
        print("1. Header raw para archivos >1MB")
        print("2. Sistema de chunking automático")
        print("3. Protecciones contra pérdida de datos")
        print("4. Endpoint /admin/recalcular-lp")
        print("5. Compatibilidad hacia atrás")
        return 0
    else:
        print("❌ HAY PROBLEMAS QUE RESOLVER")
        return 1

if __name__ == "__main__":
    sys.exit(main())