from __future__ import annotations

import jinja2
import pytest
import yaml

from sandbed.dev.errors import InputError
from sandbed.dev.labs import CIDR_POOL
from sandbed.dev.manifest import render_dev_manifest, write_dev_manifest
from sandbed.dev.record import DEFAULT_CONFIG

# Keys verified against snap-openstack main (sunbeam/core/manifest.py: CoreConfig, _User,
# _BootstrapConfig, _ExternalNetwork). A revision adapter re-checks them against its own pinned
# SHA (gate Q13).
CORE_CONFIG_KEYS = {"region", "bootstrap", "external-networks", "user"}
USER_KEYS = {"run_demo_setup", "security_group_rules", "remote_access_location", "physnet"}
EXTERNAL_KEYS = {"cidr", "gateway", "range", "network_type"}


def render(**kw) -> dict:
    args = {"region": "RegionOne", "mgmt_cidr": "10.213.0.0/24", "floating_cidr": "172.16.2.0/24"}
    return yaml.safe_load(render_dev_manifest(**{**args, **kw}))


def test_manifest_is_valid_yaml_with_only_known_keys() -> None:
    data = render()
    assert set(data["core"]["config"]) == CORE_CONFIG_KEYS
    assert set(data["core"]["config"]["user"]) == USER_KEYS
    assert data["core"]["config"]["bootstrap"] == {"management_cidr": "10.213.0.0/24"}
    assert data["storage"]["microceph"]["osd-devices"] == ["/dev/sdb"]


def test_floating_network_is_pinned_explicitly() -> None:
    data = render()["core"]["config"]
    physnet = data["external-networks"]["physnet1"]
    assert set(physnet) == EXTERNAL_KEYS
    assert physnet == {"cidr": "172.16.2.0/24", "gateway": "172.16.2.1",
                       "range": "172.16.2.2-172.16.2.254", "network_type": "flat"}
    assert data["user"]["physnet"] == "physnet1"


def test_local_access_means_no_external_nic_is_configured() -> None:
    config = render()["core"]["config"]
    assert config["user"]["remote_access_location"] == "local"
    assert "nics" not in config["external-networks"]["physnet1"]


def test_a_floating_network_that_overlaps_the_lab_network_is_refused() -> None:
    with pytest.raises(InputError, match="overlaps"):
        render(floating_cidr="10.213.0.128/25")


def test_the_default_floating_network_never_collides_with_the_lab_pool() -> None:
    import ipaddress

    assert not ipaddress.ip_network(DEFAULT_CONFIG["floating_cidr"]).overlaps(CIDR_POOL)


def test_missing_variable_is_an_error_not_an_empty_string() -> None:
    with pytest.raises(jinja2.UndefinedError):
        jinja2.Environment(undefined=jinja2.StrictUndefined).from_string("{{ x }}").render()


def test_written_manifest_is_private_and_parses(tmp_path) -> None:
    path = write_dev_manifest(tmp_path / "m" / "manifest.yaml", region="R",
                              mgmt_cidr="10.1.0.0/24", floating_cidr="172.16.2.0/24")
    assert path.stat().st_mode & 0o777 == 0o600
    assert yaml.safe_load(path.read_text())["core"]["config"]["region"] == "R"
