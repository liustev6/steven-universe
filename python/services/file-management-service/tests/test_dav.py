"""The read-only WebDAV view (app/api/dav.py): what `rclone mount` needs, on the same grants as /objects."""

import xml.etree.ElementTree as ET

import pytest

NS = {"D": "DAV:"}


def put(client, auth, key, body=b"x"):
    assert client.put(f"/objects/private/{key}", content=body, headers=auth).status_code == 200


def propfind(client, auth, path, depth="1"):
    headers = dict(auth)
    if depth is not None:
        headers["Depth"] = depth
    return client.request("PROPFIND", path, headers=headers)


def listing(r):
    """{href: (is_folder, size)} from a 207 multistatus."""
    assert r.status_code == 207, r.text
    assert r.headers["content-type"].startswith("application/xml")
    out = {}
    for resp in ET.fromstring(r.content).findall("D:response", NS):
        href = resp.find("D:href", NS).text
        folder = resp.find(".//D:resourcetype/D:collection", NS) is not None
        size = resp.find(".//D:getcontentlength", NS)
        out[href] = (folder, int(size.text) if size is not None else None)
    return out


@pytest.fixture
def tree(client, pi):
    put(client, pi, "pi-server/notes/todo.md", b"# todo\n- milk\n")
    put(client, pi, "pi-server/notes/2026/nov.md", b"november")
    put(client, pi, "pi-server/notes/with space é.txt", b"hi")
    put(client, pi, "pi-server/reports/r1.md", b"report")


def test_a_folder_lists_its_immediate_children(client, pi, tree):
    got = listing(propfind(client, pi, "/dav/private/pi-server/notes/"))
    assert got == {
        "/dav/private/pi-server/notes/": (True, None),                    # itself first
        "/dav/private/pi-server/notes/2026/": (True, None),               # a subfolder, not its files
        "/dav/private/pi-server/notes/todo.md": (False, 14),
        "/dav/private/pi-server/notes/with%20space%20%C3%A9.txt": (False, 2),
    }


def test_depth_0_is_the_folder_or_the_file_alone(client, pi, tree):
    assert listing(propfind(client, pi, "/dav/private/pi-server/notes/", "0")) == {
        "/dav/private/pi-server/notes/": (True, None)}
    one = propfind(client, pi, "/dav/private/pi-server/notes/todo.md", "0")
    assert listing(one) == {"/dav/private/pi-server/notes/todo.md": (False, 14)}
    assert ET.fromstring(one.content).find(".//D:getcontenttype", NS) is not None


def test_a_folder_named_without_its_slash(client, pi, tree):
    assert listing(propfind(client, pi, "/dav/private/pi-server/notes", "0")) == {
        "/dav/private/pi-server/notes/": (True, None)}


def test_the_granted_folder_itself_without_its_slash(client, issuer, tree):
    """A grant on private/pi-server/notes/ lets rclone open `…/notes` (its mount root) too."""
    notes = {"Authorization": "Bearer " + issuer.token(
        "agent-service", ["files:list", "files:read"], {"private/pi-server/notes/": ["read", "list"]},
        sub="service:pi-server", act={"sub": "service:agent-service"})}
    assert "/dav/private/pi-server/notes/todo.md" in listing(propfind(client, notes, "/dav/private/pi-server/notes"))
    assert propfind(client, notes, "/dav/private/pi-server/").status_code == 403    # not the parent
    assert propfind(client, notes, "/dav/private/pi-server/reports/").status_code == 403
    assert client.get("/dav/private/pi-server/notes/todo.md", headers=notes).content == b"# todo\n- milk\n"
    assert client.get("/dav/private/pi-server/reports/r1.md", headers=notes).status_code == 403


def test_outside_the_grant_is_forbidden_never_not_found(client, pi, tree):
    """403 whether or not something is there: a caller can't learn what's where it may not look."""
    assert propfind(client, pi, "/dav/private/").status_code == 403
    assert propfind(client, pi, "/dav/private/agent-cli/").status_code == 403
    assert propfind(client, pi, "/dav/private/agent-cli/x.md", "0").status_code == 403
    assert propfind(client, pi, "/dav/private").status_code == 403
    assert propfind(client, pi, "/dav/private/pi-server/nothing-here", "0").status_code == 404   # inside: 404


def test_an_empty_granted_folder_lists_as_itself(client, pi):
    assert listing(propfind(client, pi, "/dav/private/pi-server/")) == {"/dav/private/pi-server/": (True, None)}


def test_infinite_depth_is_refused(client, pi, tree):
    for depth in ("infinity", None):                                   # none = infinity (RFC 4918)
        r = propfind(client, pi, "/dav/private/pi-server/", depth)
        assert r.status_code == 403 and b"propfind-finite-depth" in r.content


def test_get_reads_as_objects_does_ranges_included(client, pi, tree):
    r = client.get("/dav/private/pi-server/notes/todo.md", headers=pi)
    assert r.status_code == 200 and r.content == b"# todo\n- milk\n" and r.headers["accept-ranges"] == "bytes"
    part = client.get("/dav/private/pi-server/notes/todo.md", headers={**pi, "Range": "bytes=2-5"})
    assert part.status_code == 206 and part.content == b"todo" and part.headers["content-range"] == "bytes 2-5/14"
    head = client.head("/dav/private/pi-server/notes/todo.md", headers=pi)
    assert head.status_code == 200 and head.headers["content-length"] == "14"


def test_it_is_read_only(client, pi, tree):
    for method in ("PUT", "DELETE", "MKCOL", "COPY", "MOVE", "PROPPATCH", "LOCK", "UNLOCK"):
        r = client.request(method, "/dav/private/pi-server/notes/todo.md", headers=pi)
        assert r.status_code == 405 and "PROPFIND" in r.headers["allow"], method
    assert client.get("/objects/private/pi-server/notes/todo.md", headers=pi).content == b"# todo\n- milk\n"


def test_options_says_dav(client, pi):
    r = client.request("OPTIONS", "/dav/private/pi-server/", headers=pi)
    assert r.status_code == 200 and r.headers["dav"] == "1"


def test_listing_needs_the_list_scope_reading_the_read_scope(client, issuer, tree):
    read_only = {"Authorization": "Bearer " + issuer.token(
        "pi-server", ["files:read"], {"private/pi-server/": ["read"]})}
    assert propfind(client, read_only, "/dav/private/pi-server/notes/").status_code == 403
    assert client.get("/dav/private/pi-server/notes/todo.md", headers=read_only).status_code == 200
    list_only = {"Authorization": "Bearer " + issuer.token(
        "pi-server", ["files:list"], {"private/pi-server/": ["list"]})}
    assert propfind(client, list_only, "/dav/private/pi-server/notes/").status_code == 207
    assert client.get("/dav/private/pi-server/notes/todo.md", headers=list_only).status_code == 403


def test_bad_paths_get_400(client, pi):
    """Key hygiene as /objects: `..` (sent encoded, as a client that doesn't normalize would), an
    empty segment, an unknown bucket. (A plain `../` is normalized away by the client before sending.)"""
    for path in ("/dav/private/pi-server/%2E%2E/agent-cli/", "/dav/nobucket/x/", "/dav/private/pi-server//x/"):
        assert propfind(client, pi, path).status_code == 400, path


def test_a_big_folder_is_listed_across_pages(client, pi, settings):
    settings.LIST_MAX_KEYS = 3
    for i in range(7):
        put(client, pi, f"pi-server/many/f{i}.txt")
    got = listing(propfind(client, pi, "/dav/private/pi-server/many/"))
    assert len(got) == 8                                                # the folder and its 7 files
