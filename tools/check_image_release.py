#!/usr/bin/env python3
"""Permit a new GHCR version only after an authenticated manifest 404.

Registry contracts: https://docs.docker.com/reference/api/registry/auth/
and https://distribution.github.io/distribution/spec/api/#existing-manifests
"""
import argparse
import base64
import http.client
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

MAX_RESPONSE_BYTES = 64 * 1024
TIMEOUT_SECONDS = 10
MANIFEST_ACCEPT = ", ".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))


class ReleaseCheckError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        return None


def checked_response(opener, request, stage):
    try:
        return opener.open(request, timeout=TIMEOUT_SECONDS)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        if stage == "manifest" and code == 404:
            return None
        raise ReleaseCheckError(f"{stage}_http_{code}") from None
    except (OSError, urllib.error.URLError, http.client.HTTPException):
        raise ReleaseCheckError("registry_unreachable") from None


def check_release(image, version):
    image = image.removeprefix("ghcr.io/")
    component = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
    if (len(image) > 255 or not re.fullmatch(component + "/" + component, image)
            or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", version)):
        raise ReleaseCheckError("invalid_image_or_version")
    actor, credential = os.environ.get("GITHUB_ACTOR", ""), os.environ.get("GHCR_TOKEN", "")
    if not actor or not credential:
        raise ReleaseCheckError("credentials_missing")
    if (not re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,100}", actor) or len(credential) > 4096
            or not all(33 <= ord(character) <= 126 for character in credential)):
        raise ReleaseCheckError("credentials_invalid")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    query = urllib.parse.urlencode({"service": "ghcr.io", "scope": f"repository:{image}:pull"})
    basic = base64.b64encode(f"{actor}:{credential}".encode()).decode()
    token_request = urllib.request.Request("https://ghcr.io/token?" + query,
        headers={"Authorization": "Basic " + basic, "Accept": "application/json"})
    with checked_response(opener, token_request, "token") as response:
        if response.status != 200:
            raise ReleaseCheckError("token_response_rejected")
        try:
            content = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, urllib.error.URLError, http.client.HTTPException):
            raise ReleaseCheckError("registry_unreachable") from None
    if len(content) > MAX_RESPONSE_BYTES:
        raise ReleaseCheckError("token_response_too_large")
    try:
        payload = json.loads(content)
    except (ValueError, UnicodeError):
        raise ReleaseCheckError("token_response_invalid") from None
    if not isinstance(payload, dict):
        raise ReleaseCheckError("token_response_invalid")
    token = payload.get("token", payload.get("access_token"))
    if (not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,16384}", token)
            or "token" in payload and "access_token" in payload and payload["token"] != payload["access_token"]):
        raise ReleaseCheckError("token_response_invalid")
    manifest_request = urllib.request.Request(f"https://ghcr.io/v2/{image}/manifests/{version}",
        method="HEAD", headers={"Authorization": "Bearer " + token, "Accept": MANIFEST_ACCEPT})
    response = checked_response(opener, manifest_request, "manifest")
    if response is None:
        return
    with response:
        if response.status == 404:
            return
        if response.status == 200:
            raise ReleaseCheckError("version_exists")
        raise ReleaseCheckError("manifest_response_rejected")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("version")
    arguments = parser.parse_args(argv)
    try:
        check_release(arguments.image, arguments.version)
    except ReleaseCheckError as error:
        print(json.dumps({"release_allowed": False, "reason": str(error)}))
        return 1
    print(json.dumps({"release_allowed": True, "reason": "authenticated_manifest_not_found"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
