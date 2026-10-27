"""Static guards on the development roots; `terraform validate` itself runs in CI."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TERRAFORM = Path(__file__).resolve().parents[1] / "terraform"
ROOTS = ("dev-network", "dev-manual")


def hcl(root: str) -> str:
    return "\n".join(p.read_text() for p in sorted((TERRAFORM / root).glob("*.tf")))


@pytest.mark.parametrize("root", ROOTS)
def test_lab_roots_never_manage_the_shared_storage_pool(root) -> None:
    assert not re.search(r'resource\s+"lxd_storage_pool"', hcl(root))


@pytest.mark.parametrize("root", ROOTS)
def test_lab_roots_do_not_touch_legacy_state_or_the_maas_roots(root) -> None:
    text = hcl(root)
    assert "terraform_remote_state" not in text
    assert "../" not in text and "maas" not in text.lower()
    assert "tfstate" not in text


def test_every_lab_scoped_resource_in_dev_manual_names_its_project() -> None:
    text = hcl("dev-manual")
    resources = re.findall(r'resource\s+"(lxd_\w+)"\s+"\w+"\s+\{(.*?)\n\}\n', text, re.S)
    assert {r[0] for r in resources} == {"lxd_profile", "lxd_storage_volume", "lxd_instance"}
    for kind, body in resources:
        assert "project" in body, f"{kind} is not project-scoped"


def test_networks_are_prefixed_and_short_enough_for_an_interface_name() -> None:
    text = hcl("dev-network")
    assert re.search(r'name\s*=\s*"\$\{var\.project\}-m"', text)
    assert "lxd_storage_pool" not in text
    assert '"features.networks" = "false"' in text  # bridges cannot be project-scoped
    # Copying the image into the project needs LXD listening on the network, which a plain
    # unix-socket host does not do: the lab must use the shared default-project image.
    assert re.search(r'"features\.images"\s*=\s*"false"', text)
    assert "lxd_image" not in text


def test_the_pool_is_only_referenced_by_variable() -> None:
    text = hcl("dev-manual")
    assert 'variable "storage_pool"' in text and text.count("var.storage_pool") >= 3
    assert 'pool = "' not in text  # never hard-coded to a legacy pool
