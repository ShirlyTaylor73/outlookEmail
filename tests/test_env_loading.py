import json
import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def test_web_entrypoint_loads_env_file_before_bootstrap(tmp_path):
    database_path = tmp_path / "env-test.db"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join([
            "SECRET_KEY=dotenv-secret-key",
            f"DATABASE_PATH={database_path}",
            "LOGIN_PASSWORD=dotenv-pass",
        ]),
        encoding="utf-8",
    )

    env = os.environ.copy()
    for name in ("SECRET_KEY", "DATABASE_PATH", "LOGIN_PASSWORD"):
        env.pop(name, None)
    env["OUTLOOK_EMAIL_ENV_FILE"] = str(env_file)
    env["PYTHONPATH"] = str(ROOT_DIR)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, web_outlook_app; "
                "print('ENV_RESULT=' + json.dumps(["
                "web_outlook_app.app.secret_key, "
                "web_outlook_app.DATABASE, "
                "web_outlook_app.LOGIN_PASSWORD"
                "]))"
            ),
        ],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    result_line = next(
        line for line in result.stdout.splitlines()
        if line.startswith("ENV_RESULT=")
    )
    assert result_line == "ENV_RESULT=" + json.dumps([
        "dotenv-secret-key",
        str(database_path),
        "dotenv-pass",
    ])
