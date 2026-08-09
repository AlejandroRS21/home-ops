"""Regression tests for the hardened systemd unit (SYS-002).

Locks the directive set and, critically, their *section placement*:
systemd >= v230 reads StartLimit* keys only from [Unit] — a misplaced
guard in [Service] is silently ignored and the crash-loop protection
vanishes. These tests fail loudly if that ever regresses.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UNIT_PATH = REPO_ROOT / "systemd" / "homeops.service"


def _sections() -> dict[str, list[str]]:
    """Parse the unit file into {section_name: [directive lines]}."""
    text = UNIT_PATH.read_text()
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped.strip("[]")
            sections[current] = []
        elif current is not None and stripped and not stripped.startswith("#"):
            sections[current].append(stripped)
    return sections


def _directives(block: list[str]) -> dict[str, str]:
    return {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in block
        if "=" in line
    }


def test_unit_exists() -> None:
    assert UNIT_PATH.is_file(), "systemd/homeops.service is missing"


def test_start_limit_guard_lives_in_unit_section() -> None:
    sections = _sections()
    unit = _directives(sections["Unit"])
    assert unit["StartLimitIntervalSec"] == "10min"
    assert unit["StartLimitBurst"] == "5"


def test_start_limit_guard_not_misplaced_in_service_section() -> None:
    sections = _sections()
    service = _directives(sections["Service"])
    assert "StartLimitIntervalSec" not in service
    assert "StartLimitBurst" not in service


def test_network_online_ordering_in_unit_section() -> None:
    sections = _sections()
    unit = _directives(sections["Unit"])
    assert unit["After"] == "network-online.target"
    assert unit["Wants"] == "network-online.target"


def test_service_runs_as_static_system_user() -> None:
    sections = _sections()
    service = _directives(sections["Service"])
    assert service["User"] == "homeops"
    assert service["Group"] == "homeops"


def test_lockdown_directives_present_in_service_section() -> None:
    sections = _sections()
    service = _directives(sections["Service"])
    required = {
        "ProtectSystem": "strict",
        "ReadWritePaths": "/opt/home-ops/data",
        "NoNewPrivileges": "yes",
        "ProtectHome": "yes",
        "CapabilityBoundingSet": "",
        "PrivateTmp": "yes",
        "ProtectKernelTunables": "yes",
        "ProtectKernelModules": "yes",
        "ProtectControlGroups": "yes",
        "RestrictSUIDSGID": "yes",
        "LockPersonality": "yes",
    }
    for directive, value in required.items():
        assert service.get(directive) == value, (
            f"{directive}={value!r} missing from [Service]"
        )


def test_restart_policy_in_service_section() -> None:
    sections = _sections()
    service = _directives(sections["Service"])
    assert service["Restart"] == "on-failure"


def test_no_environment_file_anywhere() -> None:
    assert "EnvironmentFile=" not in UNIT_PATH.read_text()


def test_install_section_wanted_by_multi_user() -> None:
    sections = _sections()
    install = _directives(sections["Install"])
    assert install["WantedBy"] == "multi-user.target"
