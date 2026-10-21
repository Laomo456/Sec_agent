"""TLS metadata inspection using only the Python standard library."""
import json
import socket
import ssl
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main(params: dict) -> None:
    host = str(params["host"])
    port = int(params.get("port", 443))
    timeout = int(params.get("timeout", 10))
    server_name = str(params.get("server_name") or host)
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=server_name) as tls:
                cert = tls.getpeercert()
                print(json.dumps({"host": host, "port": port, "server_name": server_name,
                                  "protocol": tls.version(), "cipher": tls.cipher(),
                                  "certificate": cert}, ensure_ascii=False, indent=2, default=str))
    except (OSError, ssl.SSLError) as e:
        print(f"TLS connection failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
