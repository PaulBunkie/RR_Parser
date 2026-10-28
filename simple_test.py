import socket
import threading

class SimpleHandler:
    def handle(self, conn):
        try:
            request = conn.recv(4096).decode('utf-8', errors='ignore')
            print(f'Received: {request[:100]}')
        except Exception as e:
            print(f'Decode error: {e}')
        response = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{"status": "ok"}'
        conn.send(response)
        conn.close()

def run_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('127.0.0.1', 5555))
    sock.listen(5)
    print('Server listening on 127.0.0.1:5555')
    while True:
        conn, addr = sock.accept()
        print(f'Connection from {addr}')
        threading.Thread(target=SimpleHandler().handle, args=(conn,)).start()

if __name__ == '__main__':
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    import time
    while True:
        time.sleep(1)