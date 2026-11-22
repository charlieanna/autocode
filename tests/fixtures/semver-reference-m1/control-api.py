"""Probe additional valid and invalid public JSON interchange inputs."""

import hashlib
import json
from pathlib import Path

expected = {
    "src/semver/__init__.py": "e03edce6cf0d508b28fac1ad2364e43e636f479bd642a0046405708ef1484f81",
    "src/semver/jsonio.py": "7f680a59850141ee59262c3eb11a4b5c5aa738fee115212d1fb9110f5197c537",
    "src/semver/version.py": "b873ee66dac37631f47e2b2f89995bddbdfa50dbfab4e985f4f42bc735c3ce23",
    "tests/test_json.py": (
        "15dceec0f8ef80555f805cd555312b650cdb11e1a520155abecc61a2b8d2170f",
        "9e46de969c4882d90dd2fa521bf76e76996cc6ced088db71a5c0fb8200aee699",
    ),
}
for name, digest in expected.items():
    actual = hashlib.sha256(Path(name).read_bytes()).hexdigest()
    assert actual in (digest if isinstance(digest, tuple) else (digest,)), name
import semver
from semver.jsonio import dumps_version, loads_version

value = "12.0.4-beta.3+001"
encoded = dumps_version(value)
assert encoded == '{"major":12,"minor":0,"patch":4,"prerelease":"beta.3","build":"001"}'
assert str(loads_version(encoded)) == value and str(semver.Version.from_json(encoded)) == value
assert semver.Version.parse(value).to_json() == encoded
assert dumps_version is semver.dumps_version and loads_version is semver.loads_version
assert {"dumps_version", "loads_version"} <= set(semver.__all__)
for invalid, kind in (("12.0", ValueError), (b"12.0.4", TypeError)):
    try:
        dumps_version(invalid)
    except kind:
        pass
    else:
        raise AssertionError("invalid input accepted")
try:
    loads_version('{"major":1,"minor":2,"patch":3,"unexpected":7}')
except ValueError:
    pass
else:
    raise AssertionError("unknown field accepted")
print(json.dumps({"round_trip": value, "invalid_inputs_refused": True, "public_and_direct_exports_match": True}))
