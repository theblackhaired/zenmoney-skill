"""Create the private Docker Compose credentials file without printing secrets."""

import os
import secrets
from pathlib import Path

target = Path(os.environ.get("ZENMONEY_IDP_ENV_FILE") or Path(__file__).resolve().parent / ".env")
target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
contents = (
    f"KEYCLOAK_DB_PASSWORD={secrets.token_urlsafe(48)}\n"
    f"KEYCLOAK_ADMIN_PASSWORD={secrets.token_urlsafe(48)}\n"
)
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    with os.fdopen(fd, "w", encoding="ascii", newline="\n") as output:
        output.write(contents)
except BaseException:
    target.unlink(missing_ok=True)
    raise
os.chmod(target, 0o600)
print(f"Created {target.name} with mode 0600")
