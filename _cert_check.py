import ssl, socket

host = "makertools.pythonanywhere.com"
ctx = ssl._create_unverified_context()
with socket.create_connection((host, 443), timeout=15) as sock:
    with ctx.wrap_socket(sock, server_hostname=host) as ssock:
        der = ssock.getpeercert(binary_form=True)

out_path = "_cert.der"
with open(out_path, "wb") as f:
    f.write(der)
print(f"Wrote {len(der)} bytes to {out_path}")
print("Now run:  certutil -dump _cert.der")
