import os
import json
from http.server import SimpleHTTPRequestHandler, HTTPServer
from urllib.parse import unquote  # Importar unquote para decodificar la URL

DIRECTORIO_USUARIOS = r'C:\Users\josep\Desktop\league-elo\usuarios'

class MyHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/lista-archivos':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()

            # Obtener lista de archivos JSON en el directorio
            archivos_json = [f for f in os.listdir(DIRECTORIO_USUARIOS) if f.endswith('.json')]
            self.wfile.write(json.dumps(archivos_json).encode())

        elif self.path.startswith('/usuarios/'):
            # Decodificar la URL para reemplazar %20 por espacios
            archivo_nombre = unquote(self.path.split('/', 2)[-1])
            archivo_path = os.path.join(DIRECTORIO_USUARIOS, archivo_nombre)

            print(f"Buscando archivo: {archivo_path}")  # Imprimir el nombre del archivo que se está buscando
            if os.path.exists(archivo_path):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                with open(archivo_path, 'rb') as archivo:
                    self.wfile.write(archivo.read())
            else:
                print(f"Archivo no encontrado: {archivo_path}")  # Indicar que el archivo no se encontró
                self.send_response(404)
                self.end_headers()
        else:
            super().do_GET()

def run(server_class=HTTPServer, handler_class=MyHandler):
    server_address = ('', 8000)
    httpd = server_class(server_address, handler_class)
    print('Servidor ejecutándose en el puerto 8000...')
    httpd.serve_forever()

if __name__ == '__main__':
    run()
