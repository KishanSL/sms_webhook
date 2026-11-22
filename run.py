#!/usr/bin/env python3
"""
Homelab HTTPS & HTTP SMS Webhook Service Runner.
Runs both HTTPS (with self-signed TLS) and HTTP fallback concurrently
so homelab Android devices can connect seamlessly.
"""

import argparse
import asyncio
import sys
from pathlib import Path

import uvicorn

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from app.cert_manager import ensure_self_signed_cert, get_all_local_ips
from app.config import settings
from app.main import app


async def run_dual_servers(
    host: str,
    https_port: int,
    http_port: int | None,
    ssl_cert_path: str | None,
    ssl_key_path: str | None,
):
    servers = []

    if ssl_cert_path and ssl_key_path:
        # 1. HTTPS Server on the primary port
        https_config = uvicorn.Config(
            app,
            host=host,
            port=https_port,
            ssl_certfile=ssl_cert_path,
            ssl_keyfile=ssl_key_path,
            log_level="info",
        )
        servers.append(uvicorn.Server(https_config).serve())

        # 2. Optional plain-HTTP fallback on a separate port
        if http_port and http_port != https_port:
            http_config = uvicorn.Config(app, host=host, port=http_port, log_level="info")
            servers.append(uvicorn.Server(http_config).serve())
    else:
        # HTTPS disabled: serve plain HTTP on the primary port (PORT), plus the fallback port.
        servers.append(uvicorn.Server(uvicorn.Config(app, host=host, port=https_port, log_level="info")).serve())
        if http_port and http_port != https_port:
            servers.append(uvicorn.Server(uvicorn.Config(app, host=host, port=http_port, log_level="info")).serve())

    await asyncio.gather(*servers)


def main():
    parser = argparse.ArgumentParser(
        description="Homelab HTTPS SMS Webhook Service (Message487 / Generic)"
    )
    parser.add_argument(
        "--host",
        default=settings.HOST,
        help=f"Bind host address (default: {settings.HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.PORT,
        help=f"HTTPS listen port (default: {settings.PORT})",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=8080,
        help="Optional HTTP fallback port (default: 8080, set 0 to disable)",
    )
    parser.add_argument(
        "--no-https",
        action="store_true",
        help="Disable HTTPS and run HTTP only",
    )
    parser.add_argument(
        "--cert",
        default=settings.SSL_CERTFILE,
        help="Path to TLS/SSL certificate file (PEM)",
    )
    parser.add_argument(
        "--key",
        default=settings.SSL_KEYFILE,
        help="Path to TLS/SSL private key file (PEM)",
    )
    parser.add_argument(
        "--secret",
        default=None,
        help="Set required Bearer token / secret for incoming webhooks",
    )

    args = parser.parse_args()

    if args.secret:
        settings.WEBHOOK_SECRET = args.secret

    use_https = (not args.no_https) and settings.HTTPS_ENABLED
    http_port = args.http_port if args.http_port > 0 else None

    local_ips = get_all_local_ips()
    primary_lan_ip = next((ip for ip in local_ips if not ip.startswith("127.") and ":" not in ip), "127.0.0.1")

    ssl_cert_path = None
    ssl_key_path = None

    if use_https:
        cert_file = Path(args.cert) if args.cert else None
        key_file = Path(args.key) if args.key else None

        if cert_file and cert_file.exists() and key_file and key_file.exists():
            ssl_cert_path = str(cert_file)
            ssl_key_path = str(key_file)
        elif getattr(settings, "AUTO_GENERATE_CERT", True):
            cert_p, key_p, ca_p = ensure_self_signed_cert(
                cert_dir=settings.CERT_DIR,
                days_valid=settings.CERT_VALID_DAYS,
            )
            ssl_cert_path = str(cert_p)
            ssl_key_path = str(key_p)
        else:
            raise FileNotFoundError(
                f"TLS certificate files not found: cert={args.cert}, key={args.key}"
            )

    print("=" * 70)
    print("  🚀  HOMELAB SMS WEBHOOK SERVICE IS ACTIVE")
    print("=" * 70)
    if use_https:
        print(f"  🔒  HTTPS URL:      https://{primary_lan_ip}:{args.port}/webhook/message487")
        print(f"      Dashboard:      https://{primary_lan_ip}:{args.port}/dashboard")
        print(f"      Download CA:    https://{primary_lan_ip}:{args.port}/ca.crt")
    else:
        print(f"  🌐  HTTP URL:       http://{primary_lan_ip}:{args.port}/webhook/message487")
        print(f"      Dashboard:      http://{primary_lan_ip}:{args.port}/dashboard")
    if http_port and http_port != args.port:
        print(f"  🌐  HTTP (Fallback):http://{primary_lan_ip}:{http_port}/webhook/message487")
        print(f"      HTTP Dashboard: http://{primary_lan_ip}:{http_port}/dashboard")
    print(f"  💾  SQLite Database:{settings.DB_PATH}")
    if settings.WEBHOOK_SECRET:
        print(f"  🔑  Bearer Secret:  Required ({settings.WEBHOOK_SECRET})")
    else:
        print("  🔑  Bearer Secret:  None (Open to all incoming webhooks)")
    print("-" * 70)
    print("  📱  MESSAGE487 ANDROID CONNECTION OPTIONS:")
    if http_port:
        print("  Option A (HTTP - No SSL Cert Required):")
        print(f"    URL: http://{primary_lan_ip}:{http_port}/webhook/message487")
    if use_https:
        print("  Option B (HTTPS - Self-Signed):")
        print(f"    URL: https://{primary_lan_ip}:{args.port}/webhook/message487")
        print(f"    Note: Requires installing CA cert on phone from https://{primary_lan_ip}:{args.port}/ca.crt")
    print("=" * 70)

    try:
        asyncio.run(
            run_dual_servers(
                host=args.host,
                https_port=args.port,
                http_port=http_port,
                ssl_cert_path=ssl_cert_path if use_https else None,
                ssl_key_path=ssl_key_path if use_https else None,
            )
        )
    except (KeyboardInterrupt, SystemExit):
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
