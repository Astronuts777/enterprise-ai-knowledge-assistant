"""Local-only launcher. Run with .venv/bin/python scripts/local_ai.py [model|web]."""

import argparse
import os
import secrets
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=["model", "web"])
    parser.add_argument("--port", type=int, default=8201)
    parser.add_argument("--model-port", type=int, default=11435)
    parser.add_argument(
        "--cpu", action="store_true", help="Disable GPU use in restricted environments"
    )
    args = parser.parse_args()
    os.chdir(ROOT)
    environment = dict(os.environ)
    if args.service == "model":
        bundled = ROOT / "work/ollama-runtime/ollama"
        executable = str(bundled) if bundled.exists() else shutil.which("ollama")
        if not executable:
            parser.error("Install Ollama first: https://ollama.com/download")
        environment.update(
            OLLAMA_NO_CLOUD="1", OLLAMA_HOST=f"127.0.0.1:{args.model_port}",
            OLLAMA_MODELS=str(ROOT / "work/ollama-models"), OLLAMA_MAX_LOADED_MODELS="1",
        )
        if args.cpu:
            environment.update(OLLAMA_LLM_LIBRARY="cpu", LLAMA_ARG_DEVICE="none")
        os.execve(executable, [executable, "serve"], environment)

    if not (ROOT / "apps/web/out/index.html").exists():
        parser.error("Build first: pnpm build:local-ai")
    # Separate database and key: never change the encryption key of an existing workspace.
    directory = ROOT / "data/local/ollama"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = directory / "master.key"
    if not key.exists():
        if (directory / "enterprise.db").exists():
            parser.error(
                "Existing local AI database has lost its key. Restore master.key from backup."
            )
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write(secrets.token_urlsafe(48))
    environment.update(
        APP_ENV="development", ACCOUNT_AUTH_MODE="true", PUBLIC_DEMO_MODE="false",
        APP_PUBLIC_URL=f"http://127.0.0.1:{args.port}", REQUIRE_EMAIL_VERIFICATION="false",
        EMAIL_DELIVERY_BACKEND="disabled", ENCRYPTION_MASTER_KEY=key.read_text().strip(),
        DATABASE_URL=f"sqlite:///{directory / 'enterprise.db'}",
        OBJECT_STORAGE_BACKEND="local", LOCAL_STORAGE_PATH=str(directory / "uploads"),
        WEB_STATIC_DIR=str(ROOT / "apps/web/out"),
        AI_PROVIDER="ollama", EMBEDDING_PROVIDER="ollama",
        AI_MODEL="qwen3:4b", EMBEDDING_MODEL="bge-m3",
        OLLAMA_BASE_URL=f"http://127.0.0.1:{args.model_port}", OLLAMA_TIMEOUT_SECONDS="180",
        CORS_ORIGINS=f"http://127.0.0.1:{args.port}",
        PYTHONPATH=f"{ROOT / 'services/api/src'}:{ROOT / 'packages/core/src'}",
    )
    if (ROOT / "work/ocr/bin/tesseract").exists():
        environment["PATH"] = f"{ROOT / 'work/ocr/bin'}:{environment.get('PATH', '')}"
        environment["TESSDATA_PREFIX"] = str(ROOT / "work/ocr/share/tessdata")
    executable = str(ROOT / ".venv/bin/uvicorn")
    print(f"Local account-mode website: http://127.0.0.1:{args.port}", flush=True)
    os.execve(executable, [executable, "enterprise_ai_api.main:app", "--host", "127.0.0.1",
                           "--port", str(args.port)], environment)


if __name__ == "__main__":
    main()
