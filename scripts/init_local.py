"""Create local settings without replacing existing secrets. Python standard library only."""
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
for name in ("artifacts", "local", "backups"):
    (root / name).mkdir(exist_ok=True)
try:
    fd = os.open(root / ".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print("Existing .env preserved.")
else:
    with os.fdopen(fd, "w") as output:
        output.write(f"BROWSER_VIEW_PASSWORD={secrets.token_hex(8)}\nBROWSER_VIEW_PORT=7900\n")
    print("Created .env with a random local browser-view password (not printed).")
print("Ready: docker compose up -d --build --wait")
