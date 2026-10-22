"""Local-only launcher: model, web, doctor (read-only checks), or start (both services)."""

import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
MODELS = ("qwen3:4b", "bge-m3")


def ollama_executable() -> str | None:
    bundled = ROOT / "work/ollama-runtime/ollama"
    return str(bundled) if bundled.is_file() else shutil.which("ollama")


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("端口必须是 1–65535 的整数") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口必须是 1–65535 的整数")
    return port


def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_json(port: int, path: str) -> dict:
    # Never send local diagnostic requests through an inherited corporate/public proxy.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
        payload = response.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise ValueError("本地服务响应过大")
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise ValueError("本地服务响应格式错误")
    return result


def missing_models(payload: dict) -> list[str]:
    items = payload.get("models")
    if not isinstance(items, list):
        raise ValueError("模型列表格式错误")
    names = {
        item.get("name")
        for item in items
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    return [name for name in MODELS if name not in names and f"{name}:latest" not in names]


def preflight() -> list[tuple[bool, str]]:
    """Inspect installation without creating data, keys or installing packages."""
    checks = [(sys.platform != "win32", "运行环境：macOS/Linux；Windows 请在 WSL Ubuntu 中运行")]
    checks.append(((3, 12) <= sys.version_info[:2] < (3, 15), "Python 需要 3.12–3.14"))
    python = ROOT / ".venv/bin/python"
    installed = False
    if python.is_file():
        try:
            result = subprocess.run(
                [
                    str(python),
                    "-c",
                    "import uvicorn, fastapi, sqlalchemy, alembic, "
                    "boto3, cryptography, pypdf, docx, jwt",
                ],
                capture_output=True,
                timeout=20,
                check=False,
            )
            installed = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    checks.append((installed, "Python 依赖：缺失时按 README 创建 .venv 并安装 requirements.lock"))
    checks.append(
        (
            (ROOT / "apps/web/out/index.html").is_file(),
            "网页构建：缺失时执行 pnpm install --frozen-lockfile 和 pnpm build:local-ai",
        )
    )
    executable = ollama_executable()
    checks.append(
        (bool(executable and os.access(executable, os.X_OK)), "Ollama：需要安装可执行的本地 Ollama")
    )
    directory = ROOT / "data/local/ollama"
    key = directory / "master.key"
    uploads = directory / "uploads"
    try:
        has_data = (directory / "enterprise.db").exists() or (
            uploads.is_dir() and any(uploads.iterdir())
        )
        key_ok = len(key.read_text().strip()) >= 32 if key.exists() else not has_data
    except (OSError, ValueError):
        key_ok = False
    checks.append((key_ok, "数据密钥：已有数据必须保留原 master.key；缺失时恢复备份，不要重建"))
    return checks


def doctor(model_port: int) -> int:
    checks = preflight()
    for ok, message in checks:
        print(f"[{'通过' if ok else '需处理'}] {message}", flush=True)
    try:
        missing = missing_models(local_json(model_port, "/api/tags"))
        models_ok = not missing
        print(
            "[通过] 两个模型均已下载（不代表推理性能已验证）"
            if models_ok
            else f"[需处理] 在 {model_port} 服务下载模型：{', '.join(missing)}",
            flush=True,
        )
    except (OSError, URLError, ValueError):
        models_ok = False
        print(f"[需处理] 模型服务未就绪；先运行 model --model-port {model_port}", flush=True)
    tesseract = ROOT / "work/ocr/bin/tesseract"
    if not tesseract.is_file():
        tesseract = shutil.which("tesseract")
    ocr_ok = False
    if tesseract and shutil.which("pdftoppm"):
        environment = dict(os.environ)
        if (ROOT / "work/ocr/share/tessdata").is_dir():
            environment["TESSDATA_PREFIX"] = str(ROOT / "work/ocr/share/tessdata")
        try:
            result = subprocess.run(
                [str(tesseract), "--list-langs"],
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
                check=False,
            )
            ocr_ok = result.returncode == 0 and {"eng", "chi_sim"} <= set(result.stdout.split())
        except (OSError, subprocess.TimeoutExpired):
            pass
    print(
        "[通过] OCR 工具与中英文语言包可用"
        if ocr_ok
        else "[可选] OCR 不完整：扫描/含图片 PDF 需要 Poppler、Tesseract、eng 和 chi_sim",
        flush=True,
    )
    print("诊断不会安装软件、下载模型、更改数据或调用付费 API。", flush=True)
    return 0 if all(ok for ok, _ in checks) and models_ok else 1


def wait_ready(process: subprocess.Popen, port: int, path: str, timeout: float = 45) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("服务提前退出，请查看上方服务日志。")
        try:
            result = local_json(port, path)
            if path == "/api/tags" or (
                result.get("status") == "ready"
                and result.get("service") == "enterprise-ai-api"
                and result.get("database") == "ok"
            ):
                return result
        except (OSError, URLError, ValueError):
            pass
        time.sleep(0.3)
    raise RuntimeError("等待本地服务就绪超时，请查看上方日志。")


def stop_owned_processes(processes: list[subprocess.Popen]) -> None:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def start(args: argparse.Namespace) -> int:
    failed = [message for ok, message in preflight() if not ok]
    if args.port == args.model_port:
        failed.append("网页与模型必须使用不同端口。")
    for port in (args.port, args.model_port):
        if not port_available(port):
            failed.append(
                f"端口 {port} 已占用；不会接管或关闭现有进程。请停止自己的旧服务或换端口。"
            )
    if failed:
        for message in failed:
            print(f"[需处理] {message}", flush=True)
        return 1

    command = [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/local_ai.py")]
    processes = []
    previous_handler = signal.getsignal(signal.SIGTERM)

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        print("正在启动本地模型；不会下载模型或连接云端模型。", flush=True)
        model_command = command + ["model", "--model-port", str(args.model_port)]
        if args.cpu:
            model_command.append("--cpu")
        model = subprocess.Popen(model_command, cwd=ROOT)
        processes.append(model)
        missing = missing_models(wait_ready(model, args.model_port, "/api/tags"))
        if missing:
            raise RuntimeError(
                f"尚未下载模型：{', '.join(missing)}。请按 README 单独启动 model 并下载后重试。"
            )
        web = subprocess.Popen(
            command
            + [
                "web",
                "--async-processing",
                "--port",
                str(args.port),
                "--model-port",
                str(args.model_port),
            ],
            cwd=ROOT,
        )
        processes.append(web)
        wait_ready(web, args.port, "/readyz")
        worker = subprocess.Popen(
            command + ["worker", "--port", str(args.port), "--model-port", str(args.model_port)],
            cwd=ROOT,
        )
        processes.append(worker)
        print(
            f"\n本地网页与数据库已就绪：http://127.0.0.1:{args.port}\n"
            "模型文件已在服务中确认，实际推理请用测试文档验证。按 Ctrl+C 停止本次启动的服务。",
            flush=True,
        )
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        raise RuntimeError("一个本地服务已退出，将停止本次启动的其余服务。")
    except KeyboardInterrupt:
        print("\n正在停止本次启动的服务；本地资料会保留。", flush=True)
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"启动失败：{exc}", flush=True)
        return 1
    finally:
        stop_owned_processes(processes)
        signal.signal(signal.SIGTERM, previous_handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=["model", "web", "worker", "doctor", "start"])
    parser.add_argument(
        "--async-processing",
        action="store_true",
        help="Queue uploads for a separately running worker",
    )
    parser.add_argument("--port", type=port_number, default=8201)
    parser.add_argument("--model-port", type=port_number, default=11435)
    parser.add_argument(
        "--cpu", action="store_true", help="Disable GPU use in restricted environments"
    )
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.service == "doctor":
        raise SystemExit(doctor(args.model_port))
    if args.service == "start":
        raise SystemExit(start(args))
    environment = dict(os.environ)
    if args.service == "model":
        executable = ollama_executable()
        if not executable:
            parser.error("Install Ollama first: https://ollama.com/download")
        environment.update(
            OLLAMA_NO_CLOUD="1",
            OLLAMA_HOST=f"127.0.0.1:{args.model_port}",
            OLLAMA_MODELS=str(ROOT / "work/ollama-models"),
            OLLAMA_MAX_LOADED_MODELS="1",
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
        uploads = directory / "uploads"
        if (directory / "enterprise.db").exists() or (uploads.exists() and any(uploads.iterdir())):
            parser.error(
                "Existing local AI database has lost its key. Restore master.key from backup."
            )
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write(secrets.token_urlsafe(48))
    if len(key.read_text().strip()) < 32:
        parser.error("Local master.key is empty or invalid. Restore the original key from backup.")
    environment.update(
        APP_ENV="development",
        ACCOUNT_AUTH_MODE="true",
        PUBLIC_DEMO_MODE="false",
        APP_PUBLIC_URL=f"http://127.0.0.1:{args.port}",
        REQUIRE_EMAIL_VERIFICATION="false",
        EMAIL_DELIVERY_BACKEND="disabled",
        ENCRYPTION_MASTER_KEY=key.read_text().strip(),
        DATABASE_URL=f"sqlite:///{directory / 'enterprise.db'}",
        MIGRATION_DATABASE_URL=f"sqlite:///{directory / 'enterprise.db'}",
        OBJECT_STORAGE_BACKEND="local",
        LOCAL_STORAGE_PATH=str(directory / "uploads"),
        WEB_STATIC_DIR=str(ROOT / "apps/web/out"),
        AI_PROVIDER="ollama",
        EMBEDDING_PROVIDER="ollama",
        AI_MODEL="qwen3:4b",
        EMBEDDING_MODEL="bge-m3",
        OLLAMA_BASE_URL=f"http://127.0.0.1:{args.model_port}",
        OLLAMA_TIMEOUT_SECONDS="180",
        CORS_ORIGINS=f"http://127.0.0.1:{args.port}",
        PYTHONPATH=os.pathsep.join(
            str(ROOT / part)
            for part in ("services/api/src", "packages/core/src", "services/worker/src")
        ),
        DOCUMENT_PROCESSING_MODE="async"
        if args.async_processing or args.service == "worker"
        else "sync",
    )
    if (ROOT / "work/ocr/bin/tesseract").exists():
        environment["PATH"] = f"{ROOT / 'work/ocr/bin'}:{environment.get('PATH', '')}"
        environment["TESSDATA_PREFIX"] = str(ROOT / "work/ocr/share/tessdata")
    if args.service == "worker":
        executable = str(ROOT / ".venv/bin/python")
        os.execve(executable, [executable, "-m", "enterprise_ai_worker.main"], environment)
        return
    executable = str(ROOT / ".venv/bin/uvicorn")
    print(f"Local account-mode website: http://127.0.0.1:{args.port}", flush=True)
    os.execve(
        executable,
        [
            executable,
            "enterprise_ai_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
            "--no-access-log",
        ],
        environment,
    )


if __name__ == "__main__":
    main()
