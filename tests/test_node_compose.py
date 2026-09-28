"""Add Node wizard snippets (node_compose.py): nodes run the published image of the server's exact version."""

from __future__ import annotations

from typing import get_args

import pytest
import yaml

from frameforge_server.services.node_compose import NODE_IMAGE, Hardware, compose_snippet, docker_run_snippet
from frameforge_shared import __version__

EXPECTED_IMAGE = f"ghcr.io/issaci22/frameforge:{__version__}"
SERVER = "http://192.168.1.20:8686"
TOKEN = "ffn_test-token"


def test_node_image_is_the_server_version() -> None:
    assert NODE_IMAGE == EXPECTED_IMAGE


@pytest.mark.parametrize("hardware", get_args(Hardware))
def test_compose_snippet_is_valid_yaml_pinned_to_server_version(hardware: Hardware) -> None:
    text = compose_snippet(SERVER, TOKEN, hardware, ["/media/vods"])
    service = yaml.safe_load(text)["services"]["frameforge-node"]
    assert service["image"] == EXPECTED_IMAGE
    assert f"FF_SERVER_URL={SERVER}" in service["environment"]
    assert f"FF_NODE_TOKEN={TOKEN}" in service["environment"]
    assert "/media/vods:/media/vods" in service["volumes"]


@pytest.mark.parametrize("hardware", get_args(Hardware))
def test_docker_run_snippet_uses_server_version(hardware: Hardware) -> None:
    text = docker_run_snippet(SERVER, TOKEN, hardware, ["/media/vods"])
    assert text.splitlines()[-1].strip() == EXPECTED_IMAGE


@pytest.mark.parametrize("hardware", get_args(Hardware))
def test_snippets_no_longer_point_at_a_local_build(hardware: Hardware) -> None:
    for text in (compose_snippet(SERVER, TOKEN, hardware, []), docker_run_snippet(SERVER, TOKEN, hardware, [])):
        assert "frameforge:latest" not in text
        assert ":main" not in text
        assert "build it" not in text
