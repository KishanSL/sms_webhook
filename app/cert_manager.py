"""
Self-signed TLS Certificate & CA Generator for Homelab HTTPS Webhook Service.
Creates a Local Root CA and issues a server certificate signed by it with SAN
covering localhost, 127.0.0.1, IPv6 loopback, local machine hostname, and all local LAN IPs.
"""

import os
import socket
import subprocess
import sys
from pathlib import Path


def get_all_local_ips() -> set[str]:
    """Detect local IP addresses across active network interfaces."""
    ips = {"127.0.0.1", "::1"}

    # Socket connect trick to find preferred outbound LAN IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = str(info[4][0])
            if ip and not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass

    return ips


def ensure_self_signed_cert(
    cert_dir: str | Path,
    days_valid: int = 825,
    extra_domains: list[str] | None = None,
    extra_ips: list[str] | None = None,
    force_overwrite: bool = False,
) -> tuple[Path, Path, Path]:
    """
    Ensure Root CA and Server TLS certificates exist.
    Generates:
      - ca.key & ca.crt (Local Root CA)
      - ca.der (Android binary format)
      - key.pem (Server private key)
      - cert.pem (Server certificate signed by Local Root CA)
    Returns: (cert_path, key_path, ca_cert_path)
    """
    directory = Path(cert_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)

    ca_key = directory / "ca.key"
    ca_crt = directory / "ca.crt"
    ca_der = directory / "ca.der"
    server_key = directory / "key.pem"
    server_crt = directory / "cert.pem"

    if (
        ca_crt.exists()
        and server_key.exists()
        and server_crt.exists()
        and not force_overwrite
    ):
        return server_crt, server_key, ca_crt

    hostname = socket.gethostname()
    domains = {"localhost", hostname, f"{hostname}.local"}
    if extra_domains:
        domains.update(extra_domains)

    ips = get_all_local_ips()
    if extra_ips:
        ips.update(extra_ips)

    print(f"Generating Homelab Local CA and Server Certificate in: {directory}")
    print(f"  Domains included: {', '.join(sorted(domains))}")
    print(f"  IPs included:     {', '.join(sorted(ips))}")

    # 1. Generate Local Root CA
    ca_cnf = """
[req]
default_bits = 4096
prompt = no
default_md = sha256
distinguished_name = ca_dn
x509_extensions = v3_ca

[ca_dn]
C = US
ST = Homelab
L = Homelab
O = Homelab Webhook Root CA
CN = Homelab Webhook Root CA

[v3_ca]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer
basicConstraints = critical, CA:true, pathlen:0
keyUsage = critical, digitalSignature, cRLSign, keyCertSign
"""
    ca_cnf_path = directory / "ca_openssl.cnf"
    ca_cnf_path.write_text(ca_cnf.strip(), encoding="utf-8")

    try:
        # Generate CA Key and Self-Signed Root Cert
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-new",
                "-nodes",
                "-days",
                "3650",  # 10 years for Root CA
                "-newkey",
                "rsa:4096",
                "-keyout",
                str(ca_key),
                "-out",
                str(ca_crt),
                "-config",
                str(ca_cnf_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        os.chmod(ca_key, 0o600)

        # Export CA in DER format for Android certificate installer
        subprocess.run(
            [
                "openssl",
                "x509",
                "-outform",
                "der",
                "-in",
                str(ca_crt),
                "-out",
                str(ca_der),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        # 2. Build SAN configuration for Server Cert
        alt_names_lines = []
        for idx, domain in enumerate(sorted(domains), start=1):
            alt_names_lines.append(f"DNS.{idx} = {domain}")
        for idx, ip in enumerate(sorted(ips), start=1):
            alt_names_lines.append(f"IP.{idx} = {ip}")

        alt_names_block = "\n".join(alt_names_lines)

        server_cnf = f"""
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = server_dn
req_extensions = v3_req

[server_dn]
C = US
ST = Homelab
L = Homelab
O = Homelab SMS Webhook
CN = {sorted(ips)[0] if ips else 'localhost'}

[v3_req]
basicConstraints = CA:FALSE
keyUsage = nonRepudiation, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
{alt_names_block}
"""
        server_cnf_path = directory / "server_openssl.cnf"
        server_cnf_path.write_text(server_cnf.strip(), encoding="utf-8")

        # Generate Server CSR & Key
        server_csr = directory / "server.csr"
        subprocess.run(
            [
                "openssl",
                "req",
                "-new",
                "-nodes",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(server_key),
                "-out",
                str(server_csr),
                "-config",
                str(server_cnf_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        os.chmod(server_key, 0o600)

        # Sign Server Certificate using our Local Root CA with extensions
        subprocess.run(
            [
                "openssl",
                "x509",
                "-req",
                "-in",
                str(server_csr),
                "-CA",
                str(ca_crt),
                "-CAkey",
                str(ca_key),
                "-CAcreateserial",
                "-out",
                str(server_crt),
                "-days",
                str(days_valid),
                "-extfile",
                str(server_cnf_path),
                "-extensions",
                "v3_req",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        if server_csr.exists():
            server_csr.unlink()

        print("Self-signed TLS setup completed:")
        print(f"  Root CA:        {ca_crt}")
        print(f"  Android DER CA: {ca_der}")
        print(f"  Server Cert:    {server_crt}")
        print(f"  Server Key:     {server_key}")

    except subprocess.CalledProcessError as e:
        print(f"Error during certificate generation: {e.stderr}", file=sys.stderr)
        raise
    finally:
        if ca_cnf_path.exists():
            ca_cnf_path.unlink()
        if (directory / "server_openssl.cnf").exists():
            (directory / "server_openssl.cnf").unlink()

    return server_crt, server_key, ca_crt
