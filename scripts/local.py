"""Start and configure the isolated FuturaNext development stack."""

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.local"
FRONTEND_URL = "http://127.0.0.1:8088"
SUPABASE_VERSION = "3.1.0"
DOCKER_NETWORK = "futuranext-local"
LOOPBACK_BIND = "com.docker.network.bridge.host_binding_ipv4"


def run(args, *, cwd=ROOT, env=None, capture=False):
    executable = shutil.which(args[0])
    if not executable:
        raise SystemExit(f"Comando ausente: {args[0]}")
    return subprocess.run(
        [executable, *args[1:]], cwd=cwd, env=env, check=True,
        capture_output=capture, text=True, encoding="utf-8", errors="replace",
    ).stdout


def supabase(*args):
    return run(["npx", "--yes", f"supabase@{SUPABASE_VERSION}", *args], capture=True)


def prepare_local_network():
    try:
        network = json.loads(run(["docker", "network", "inspect", DOCKER_NETWORK], capture=True))[0]
    except subprocess.CalledProcessError:
        run(["docker", "network", "create", "--driver", "bridge", "--opt",
             f"{LOOPBACK_BIND}=127.0.0.1", DOCKER_NETWORK], capture=True)
        return
    if network["Driver"] != "bridge" or network["Options"].get(LOOPBACK_BIND) != "127.0.0.1":
        raise SystemExit(f"A rede {DOCKER_NETWORK} precisa usar bridge com bind 127.0.0.1.")


def check_local_ports():
    containers = run(["docker", "ps", "--filter", "label=com.supabase.cli.project=futuranext",
                      "--format", "{{.Networks}} {{.Ports}}"], capture=True)
    if not containers or any(
        not line.startswith(f"{DOCKER_NETWORK} ") or "0.0.0.0:" in line or "[::]:" in line
        for line in containers.splitlines()
    ):
        raise SystemExit(
            "Rede inesperada nos serviços Supabase. Pare a pilha FuturaNext com "
            "npx --yes supabase@3.1.0 stop e execute o setup novamente."
        )


def ensure_local_url(value):
    url = urlsplit(value)
    if url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("O setup local requer endpoints de loopback.")
    return url


def app_database_url(owner_url, password):
    parsed = ensure_local_url(owner_url)
    host_port = parsed.netloc.rsplit("@", 1)[-1]
    return urlunsplit(parsed._replace(
        scheme="postgresql+psycopg",
        netloc=f"fn_app:{quote(password, safe='')}@{host_port}",
    ))


def read_local_env():
    if not ENV_FILE.exists():
        raise SystemExit("Execute primeiro python scripts/local.py setup.")
    return dict(line.split("=", 1) for line in ENV_FILE.read_text(encoding="utf-8").splitlines())


def save_local_env(values):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=ROOT,
                            prefix=".env.local.", delete=False) as output:
        temp_path = Path(output.name)
        for key, value in values.items():
            output.write(f"{key}={value}\n")
    try:
        temp_path.replace(ENV_FILE)
    finally:
        temp_path.unlink(missing_ok=True)


def take(values, *names):
    for name in names:
        if values.get(name):
            return values[name]
    raise SystemExit(f"Campo esperado no status local do Supabase: {', '.join(names)}")


def setup():
    print("Iniciando Supabase local. O primeiro início baixa as imagens do Docker.", flush=True)
    prepare_local_network()
    try:
        supabase("start", "--network-id", DOCKER_NETWORK)
        check_local_ports()
        status = json.loads(supabase("status", "--output", "json"))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        raise SystemExit("Falha no Supabase local. Confira Docker e os logs do projeto futuranext.") from None

    api_url = take(status, "API_URL").rstrip("/")
    ensure_local_url(api_url)
    owner_url = take(status, "DB_URL")
    owner = ensure_local_url(owner_url)
    owner_url = urlunsplit(owner._replace(scheme="postgresql+psycopg"))
    password = read_local_env().get("FN_APP_DB_PASSWORD") if ENV_FILE.exists() else None
    password = password or secrets.token_urlsafe(32)
    public_key = take(status, "PUBLISHABLE_KEY", "ANON_KEY")
    admin_key = take(status, "SECRET_KEY", "SERVICE_ROLE_KEY")
    values = {
        "FN_ENVIRONMENT": "local",
        "FN_DATABASE_URL": app_database_url(owner_url, password),
        "FN_MIGRATION_DATABASE_URL": owner_url,
        "FN_APP_DB_PASSWORD": password,
        "FN_SUPABASE_URL": api_url,
        "FN_SUPABASE_ISSUER": f"{api_url}/auth/v1",
        "FN_SUPABASE_PUBLISHABLE_KEY": public_key,
        "FN_SUPABASE_ADMIN_KEY": admin_key,
        "FN_AUTH_MODE": "remote",
        "FN_FRONTEND_URL": FRONTEND_URL,
        "FN_CORS_ORIGINS": json.dumps([FRONTEND_URL]),
        "FN_OPENAPI_ENABLED": "true",
    }
    save_local_env(values)
    migrate()
    build_frontend()
    print("Pilha local configurada. Inicie a API com python scripts/local.py serve.")


def build_frontend():
    values = read_local_env()
    run(["npm", "ci"])
    run(["npm", "run", "build"], env={**os.environ,
        "PUBLIC_SUPABASE_URL": take(values, "FN_SUPABASE_URL"),
        "PUBLIC_SUPABASE_PUBLISHABLE_KEY": take(values, "FN_SUPABASE_PUBLISHABLE_KEY"),
        "PUBLIC_API_BASE_URL": "",
    })


def admin_environment():
    values = read_local_env()
    url = take(values, "FN_MIGRATION_DATABASE_URL")
    ensure_local_url(url)
    return {**os.environ, "FN_MIGRATION_DATABASE_URL": url,
            "FN_APP_DB_PASSWORD": take(values, "FN_APP_DB_PASSWORD")}


def migrate():
    env = admin_environment()
    run([sys.executable, "-m", "app.db_admin", "setup-role"], cwd=ROOT / "backend", env=env)
    run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "backend", env=env)


def bootstrap(user_id):
    run([sys.executable, "-m", "app.db_admin", "bootstrap-admin", str(user_id)],
        cwd=ROOT / "backend", env=admin_environment())


def runtime_environment():
    values = read_local_env()
    ensure_local_url(take(values, "FN_DATABASE_URL"))
    ensure_local_url(take(values, "FN_SUPABASE_URL"))
    env = {**os.environ, **values}
    env.pop("FN_MIGRATION_DATABASE_URL", None)
    env.pop("FN_APP_DB_PASSWORD", None)
    return env


def serve():
    env = runtime_environment()
    env["FN_STATIC_DIR"] = str(ROOT / "dist")
    run([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", "8088", "--no-access-log"], env=env)


def docker_up():
    env = runtime_environment()
    env["FN_API_IMAGE"] = "futuranext-api:local"
    db_url = urlsplit(env["FN_DATABASE_URL"])
    credentials = db_url.netloc.rsplit("@", 1)[0]
    env["FN_DATABASE_URL"] = urlunsplit(db_url._replace(
        netloc=f"{credentials}@supabase_db_futuranext:5432",
    ))
    supabase_url = urlsplit(env["FN_SUPABASE_URL"])
    env["FN_SUPABASE_URL"] = urlunsplit(supabase_url._replace(
        netloc="supabase_kong_futuranext:8000",
    ))
    run(["docker", "compose", "-p", DOCKER_NETWORK, "-f", "compose.yaml",
         "-f", "infra/compose.local.yaml", "up", "-d", "--build", "--wait"], env=env)


def docker_down():
    run(["docker", "compose", "-p", DOCKER_NETWORK, "-f", "compose.yaml",
         "-f", "infra/compose.local.yaml", "down"], env=runtime_environment())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("setup", "build", "migrate", "serve", "docker-up", "docker-down"):
        actions.add_parser(action)
    actions.add_parser("bootstrap-admin").add_argument("user_id", type=UUID)
    args = parser.parse_args()
    try:
        if args.action == "bootstrap-admin":
            bootstrap(args.user_id)
        else:
            {"setup": setup, "build": build_frontend, "migrate": migrate, "serve": serve,
             "docker-up": docker_up, "docker-down": docker_down}[args.action]()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
