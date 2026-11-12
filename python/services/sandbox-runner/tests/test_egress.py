"""The egress proxy's config (R12): one upstream, a stamp the sandbox can't change, streaming, TLS."""

import uuid

import pytest

from app.egress import new_sandbox_id, proxy_config

SID = uuid.UUID("3f2b8c1e-7a4d-4e9b-9c2a-1d5e6f708192")


def test_stamps_and_forwards_only_to_the_gateway():
    conf = proxy_config(SID, "http://172.31.0.2:8080")
    assert f'proxy_set_header X-Sandbox-Id "{SID}";' in conf
    assert "set $gateway http://172.31.0.2:8080;" in conf and "proxy_pass $gateway;" in conf
    assert conf.count("proxy_pass") == 1 and "resolver 127.0.0.11 valid=30s" in conf
    assert "proxy_buffering off;" in conf and "proxy_request_buffering off;" in conf
    assert "listen 8080;" in conf and "proxy_ssl" not in conf


def test_https_upstream_is_verified():
    conf = proxy_config(SID, "https://agent.lan.liustev6.ca:8443/")
    assert "set $gateway https://agent.lan.liustev6.ca:8443;" in conf
    assert "proxy_ssl_verify on;" in conf and "proxy_ssl_name agent.lan.liustev6.ca;" in conf
    assert "proxy_set_header Host agent.lan.liustev6.ca:8443;" in conf


@pytest.mark.parametrize("sandbox_id", [str(SID), 'x"; proxy_pass http://evil;', SID.hex, None])
def test_only_a_uuid_is_a_sandbox_id(sandbox_id):
    with pytest.raises(TypeError):                       # even a valid-looking string: ids come from the runner
        proxy_config(sandbox_id, "http://gw:8080")


def test_new_ids_are_random_uuids():
    a, b = new_sandbox_id(), new_sandbox_id()
    assert isinstance(a, uuid.UUID) and a.version == 4 and a != b


@pytest.mark.parametrize("upstream", ["ftp://gw", "http://", "http://gw/path", "http://gw?x=1", "http://u:p@gw",
                                      "gw:8080", "http://g;w"])
def test_refuses_anything_but_a_plain_upstream(upstream):
    with pytest.raises(ValueError):
        proxy_config(SID, upstream)
