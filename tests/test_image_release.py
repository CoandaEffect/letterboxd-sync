import base64
from contextlib import redirect_stdout
import io
import http.client
import json
import os
import unittest
from unittest.mock import Mock, patch
import urllib.error

from tools.check_image_release import MAX_RESPONSE_BYTES, NoRedirect, ReleaseCheckError, check_release, main


IMAGE = "coandaeffect/letterboxd-sync-green"
VERSION = "0.2.0"
ENVIRONMENT = {"GITHUB_ACTOR": "CoandaEffect", "GHCR_TOKEN": "private_credential"}


def response(status=200, body=b'{"token":"registry-token"}'):
    value = Mock(status=status)
    value.read.return_value = body
    value.__enter__ = Mock(return_value=value)
    value.__exit__ = Mock(return_value=False)
    return value


def http_error(code):
    return urllib.error.HTTPError("https://ghcr.io/secret", code, "private_credential", {}, None)


class ReleaseCheckTests(unittest.TestCase):
    def run_check(self, results, image=IMAGE, version=VERSION):
        opener = Mock()
        opener.open.side_effect = results
        with patch.dict(os.environ, ENVIRONMENT, clear=True), \
                patch("tools.check_image_release.urllib.request.build_opener", return_value=opener):
            check_release(image, version)
        return opener

    def test_only_authenticated_manifest_404_allows_release(self):
        opener = self.run_check([response(), http_error(404)])
        token, manifest = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual(token.get_method(), "GET")
        self.assertEqual(token.full_url,
            "https://ghcr.io/token?service=ghcr.io&scope=repository%3Acoandaeffect%2Fletterboxd-sync-green%3Apull")
        self.assertEqual(token.get_header("Authorization"), "Basic " +
            base64.b64encode(b"CoandaEffect:private_credential").decode())
        self.assertEqual(manifest.get_method(), "HEAD")
        self.assertEqual(manifest.full_url, f"https://ghcr.io/v2/{IMAGE}/manifests/{VERSION}")
        self.assertEqual(manifest.get_header("Authorization"), "Bearer registry-token")
        self.assertIn("application/vnd.oci.image.index.v1+json", manifest.get_header("Accept"))
        self.assertTrue(all(call.kwargs["timeout"] == 10 for call in opener.open.call_args_list))

    def test_existing_version_rejected(self):
        with self.assertRaisesRegex(ReleaseCheckError, "version_exists"):
            self.run_check([response(), response(status=200, body=b"")])

    def test_manifest_auth_server_and_redirect_errors_rejected(self):
        for code in (301, 302, 307, 401, 403, 429, 500):
            with self.subTest(code=code), self.assertRaises(ReleaseCheckError) as raised:
                self.run_check([response(), http_error(code)])
            self.assertNotIn("private_credential", str(raised.exception))
            self.assertNotIn("registry-token", str(raised.exception))

    def test_token_404_is_not_permission_to_publish(self):
        with self.assertRaises(ReleaseCheckError):
            self.run_check([http_error(404)])

    def test_network_errors_are_rejected_at_either_stage_without_leakage(self):
        for results in ([OSError("private_credential")],
                        [response(), urllib.error.URLError("registry-token")],
                        [http.client.BadStatusLine("private_credential")]):
            with self.subTest(stage=len(results)), self.assertRaises(ReleaseCheckError) as raised:
                self.run_check(results)
            self.assertNotIn("private_credential", str(raised.exception))
            self.assertNotIn("registry-token", str(raised.exception))

    def test_truncated_token_read_is_rejected_without_partial_body_leakage(self):
        truncated = response()
        truncated.read.side_effect = http.client.IncompleteRead(b"private_credential")
        with self.assertRaises(ReleaseCheckError) as raised:
            self.run_check([truncated])
        self.assertNotIn("private_credential", str(raised.exception))

    def test_malformed_empty_oversized_or_unsafe_token_is_rejected(self):
        for body in (b"not json", b"{}", b"[]", b'{"token":""}',
                     b'{"token":42}', b'{"token":"header\\ninjection"}',
                     b'{"token":"one","access_token":"different"}',
                     b"x" * (MAX_RESPONSE_BYTES + 1)):
            with self.subTest(body=body[:60]), self.assertRaises(ReleaseCheckError):
                self.run_check([response(body=body)])

    def test_alternate_access_token_field_and_ghcr_prefix_are_supported(self):
        self.run_check([response(body=b'{"access_token":"valid.token"}'), http_error(404)],
                       image="ghcr.io/" + IMAGE)

    def test_invalid_paths_tags_and_missing_credentials_do_not_make_requests(self):
        for image, version in [("other.example/owner/image", VERSION), ("owner/../image", VERSION),
                               ("OWNER/image", VERSION), (IMAGE, "bad/tag"), (IMAGE, "v\nHeader")]:
            with self.subTest(image=image, version=version), self.assertRaises(ReleaseCheckError):
                self.run_check([], image=image, version=version)
        with patch.dict(os.environ, {}, clear=True), \
                patch("tools.check_image_release.urllib.request.build_opener") as build, \
                self.assertRaisesRegex(ReleaseCheckError, "credentials_missing"):
            check_release(IMAGE, VERSION)
        build.assert_not_called()

    def test_redirect_handler_does_not_forward_credentials(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid"))

    def test_cli_output_is_safe_and_exit_status_is_unambiguous(self):
        for result, expected in [(None, 0), (ReleaseCheckError("registry_unreachable"), 1)]:
            stream = io.StringIO()
            with patch("tools.check_image_release.check_release", side_effect=result), redirect_stdout(stream):
                code = main([IMAGE, VERSION])
            self.assertEqual(code, expected)
            self.assertEqual(json.loads(stream.getvalue())["release_allowed"], expected == 0)


if __name__ == "__main__":
    unittest.main()
