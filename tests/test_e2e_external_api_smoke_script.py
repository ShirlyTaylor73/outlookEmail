import importlib.util
import re
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "e2e_external_api_smoke.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("e2e_external_api_smoke", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_group_ids_accepts_comma_separated_values():
    module = load_script_module()

    assert module.parse_group_ids("1, 49,50") == [1, 49, 50]


def test_mask_email_keeps_domain_and_hides_local_part():
    module = load_script_module()

    assert module.mask_email("SampleUser@hotmail.com") == "Sa***@hotmail.com"


def test_script_does_not_embed_real_api_key():
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert not re.search(r"(?<![A-Za-z0-9])[a-f0-9]{32}(?![A-Za-z0-9])", source)


def test_resolve_api_key_reads_external_api_key_from_env_file(tmp_path, monkeypatch):
    module = load_script_module()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OTHER=value\nEXTERNAL_API_KEY=0123456789abcdef0123456789abcdef\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OUTLOOK_EXTERNAL_API_KEY", raising=False)
    monkeypatch.delenv("EXTERNAL_API_KEY", raising=False)

    assert module.resolve_api_key("", env_file) == "0123456789abcdef0123456789abcdef"


def test_resolve_api_key_keeps_real_environment_precedence(tmp_path, monkeypatch):
    module = load_script_module()
    env_file = tmp_path / ".env"
    env_file.write_text("EXTERNAL_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("EXTERNAL_API_KEY", "from-env")

    assert module.resolve_api_key("", env_file) == "from-env"
