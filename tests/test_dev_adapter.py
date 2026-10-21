from __future__ import annotations

import pytest

from sandbed.dev.adapter import SnapMeta, missing_connections, parse_connections, select_adapter
from sandbed.dev.errors import InputError

CONNECTIONS = """\
Interface        Plug                                 Slot              Notes
content[juju]    openstack:juju-bin                   juju:juju-bin     manual
personal-files   openstack:dot-local-share-juju       :personal-files   manual
personal-files   openstack:dot-config-openstack       :personal-files   -
personal-files   openstack:dot-local-share-openstack  -                 -
network          openstack:network                    :network          -
"""


def meta(**kw) -> SnapMeta:
    base = {"name": "openstack", "version": "2026.1-0123abcd+dirty", "base": "core24",
            "architectures": ("amd64",)}
    return SnapMeta(**{**base, **kw})


def test_supported_revision_is_selected() -> None:
    assert select_adapter(meta()).name == "2026.1"
    assert select_adapter(meta(version="2026.1-0123abcd")).name == "2026.1"


@pytest.mark.parametrize("kw", [
    {"version": "2026.1"},  # store build: no git suffix
    {"version": "2024.1-0123abcd"},
    {"base": "core22"},
    {"name": "other"},
    {"architectures": ("s390x",)},
])
def test_unknown_revisions_are_rejected(kw) -> None:
    with pytest.raises(InputError):
        select_adapter(meta(**kw))


def test_connection_parsing_and_missing_plugs() -> None:
    seen = parse_connections(CONNECTIONS)
    assert seen["juju-bin"] == "juju:juju-bin"
    assert seen["dot-local-share-openstack"] == "-"
    assert missing_connections(select_adapter(meta()), CONNECTIONS) == ["dot-local-share-openstack"]
    assert missing_connections(select_adapter(meta()), "Interface Plug Slot Notes\n") == [
        "juju-bin", "dot-local-share-juju", "dot-config-openstack", "dot-local-share-openstack",
    ]