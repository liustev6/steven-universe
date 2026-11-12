"""The egress proxy's config (R12): one upstream, a stamp the sandbox can't change, streaming, TLS."""

import pytest

from app.egress import proxy_config


def test_stamps_and_forwards_only_to_the_gateway():
    conf = proxy_config("sandbox-7", "http://172.31.0.2:8080")
    assert 'proxy_set_header X-Sandbox-Id "sandbox-7";' in conf
    assert "proxy_pass http://172.31.0.2:8080;" in conf and conf.count("proxy_pass") == 1
    assert "proxy_buffering off;" in conf and "proxy_request_buffering off;" in conf
    assert "listen 8080;" in conf and "proxy_ssl" not in conf


def test_https_upstream_is_verified():
    conf = proxy_config("sandbox-1", "https://agent.lan.liustev6.ca:8443/")
    assert "proxy_pass https://agent.lan.liustev6.ca:8443;" in conf
    assert "proxy_ssl_verify on;" in conf and "proxy_ssl_name agent.lan.liustev6.ca;" in conf
    assert "proxy_set_header Host agent.lan.liustev6.ca:8443;" in conf


@pytest.mark.parametrize("sandbox_id", ["", "Sandbox-1", "s;", 'a"b', "x" * 64, "-a", "a b"])
def test_refuses_bad_sandbox_ids(sandbox_id):
    with pytest.raises(ValueError):
        proxy_config(sandbox_id, "http://gw:8080")


@pytest.mark.parametrize("upstream", ["ftp://gw", "http://", "http://gw/path", "http://gw?x=1", "http://u:p@gw",
                                      "gw:8080", "http://g;w"])
def test_refuses_anything_but_a_plain_upstream(upstream):
    with pytest.raises(ValueError):
        proxy_config("sandbox-1", upstream)
