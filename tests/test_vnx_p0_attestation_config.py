"""VNX-1 P0 AS7-CONTINUITY-CLOSE: attestation settings and assembly primitives."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config.settings import MinecraftAttestationConfig, MinecraftConfig
from app.integrations.minecraft.session_attestation import SessionAttestationClient


def test_attestation_defaults_are_disabled_and_bounded() -> None:
    config = MinecraftConfig()
    att = config.attestation
    assert att.enabled is False
    assert att.endpoint.startswith("http://127.0.0.1:")
    assert att.expected_server_scope == ""
    assert att.refresh_interval_seconds < att.proof_max_age_seconds


def test_attestation_enabled_config_requires_explicit_values() -> None:
    att = MinecraftAttestationConfig(
        enabled=True,
        endpoint="http://127.0.0.1:19133",
        expected_server_scope="mc.test:25565",
        refresh_interval_seconds=10,
        proof_max_age_seconds=60,
    )
    assert att.enabled is True
    assert att.refresh_interval_seconds < att.proof_max_age_seconds


def test_attestation_endpoint_must_be_loopback() -> None:
    with pytest.raises(ValueError):
        SessionAttestationClient(base_url="http://example.com:19132", token="t", server_scope="s")


def test_refresh_interval_must_be_less_than_proof_max_age() -> None:
    with pytest.raises(ValidationError):
        MinecraftAttestationConfig(refresh_interval_seconds=300, proof_max_age_seconds=300)
    with pytest.raises(ValidationError):
        MinecraftAttestationConfig(refresh_interval_seconds=400, proof_max_age_seconds=300)
