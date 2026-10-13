#!/usr/bin/env python3
"""Cloud configuration/build contracts; no SDK, network, or real credentials."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / 'scripts/generate-cloud-config.py'
BOOTSTRAP = ROOT / 'scripts/bootstrap-gradle.sh'
PUBLIC_KEY = 'AIza' + 'x' * 35


def firebase_config():
    return {
        'project_info': {'project_number': '123456789012', 'project_id': 'majlis-example'},
        'client': [{
            'client_info': {
                'mobilesdk_app_id': '1:123456789012:android:0123456789abcdef',
                'android_client_info': {'package_name': 'com.majlis.app'},
            },
            'api_key': [{'current_key': PUBLIC_KEY}],
            'oauth_client': [{'client_id': 'not-used-by-this-app'}],
        }],
        'configuration_version': '1',
    }


class CloudConfigContracts(unittest.TestCase):
    def setUp(self):
        self.assertTrue(GENERATOR.is_file(), 'cloud config generator is missing')
        self.temporary = tempfile.TemporaryDirectory(prefix='majlis-cloud-test-')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.source = self.folder / 'google-services.json'
        self.output = self.folder / 'generated/assets/cloud-config.json'

    def run_generator(self, config=None, model=None):
        if config is not None:
            self.source.write_text(json.dumps(config), encoding='utf-8')
        args = [sys.executable, str(GENERATOR), '--input', str(self.source),
                '--output', str(self.output), '--package', 'com.majlis.app']
        if model is not None:
            args += ['--model', model]
        return subprocess.run(args, capture_output=True, text=True)

    def assert_rejected(self, config):
        result = self.run_generator(config)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists(), 'invalid config must not leave a usable asset')
        self.assertNotIn(PUBLIC_KEY, result.stdout + result.stderr)
        return result

    def test_missing_operator_config_builds_disabled_public_asset(self):
        result = self.run_generator()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.output.read_text()), {
            'enabled': False, 'project_id': '', 'api_key': '', 'app_id': '',
            'model': 'gemini-3.5-flash-lite',
        })

    def test_selects_android_package_and_exports_only_public_configuration(self):
        config = firebase_config()
        other = copy.deepcopy(config['client'][0])
        other['client_info']['android_client_info']['package_name'] = 'com.other.app'
        other['api_key'][0]['current_key'] = 'do-not-export-this-key'
        config['client'].insert(0, other)
        result = self.run_generator(config)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.output.read_text()), {
            'enabled': True, 'project_id': 'majlis-example', 'api_key': PUBLIC_KEY,
            'app_id': '1:123456789012:android:0123456789abcdef',
            'model': 'gemini-3.5-flash-lite',
        })
        self.assertNotIn(PUBLIC_KEY, result.stdout + result.stderr)
        self.assertNotIn('do-not-export', self.output.read_text())

    def test_malformed_json_has_generic_error_and_removes_stale_output(self):
        self.assertEqual(self.run_generator(firebase_config()).returncode, 0)
        self.source.write_text('{"broken": "' + PUBLIC_KEY, encoding='utf-8')
        result = self.run_generator()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())
        self.assertNotIn(PUBLIC_KEY, result.stdout + result.stderr)

    def test_duplicate_json_keys_are_rejected(self):
        self.source.write_text('{"project_info":{},"project_info":{}}', encoding='utf-8')
        result = self.run_generator()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_missing_matching_package_is_rejected(self):
        config = firebase_config()
        config['client'][0]['client_info']['android_client_info']['package_name'] = 'com.other.app'
        self.assert_rejected(config)

    def test_ambiguous_matching_package_is_rejected(self):
        config = firebase_config()
        config['client'].append(copy.deepcopy(config['client'][0]))
        self.assert_rejected(config)

    def test_app_id_must_match_project_number(self):
        config = firebase_config()
        config['project_info']['project_number'] = '987654321098'
        self.assert_rejected(config)

    def test_project_number_must_fit_a_positive_signed_long(self):
        for project_number in ('0', '-1', str(2 ** 63), '999999999999999999999999999999'):
            with self.subTest(project_number=project_number):
                config = firebase_config()
                config['project_info']['project_number'] = project_number
                config['client'][0]['client_info']['mobilesdk_app_id'] = (
                    '1:' + project_number + ':android:0123456789abcdef')
                self.assert_rejected(config)

    def test_maximum_signed_long_project_number_remains_supported(self):
        config = firebase_config()
        config['project_info']['project_number'] = '9223372036854775807'
        config['client'][0]['client_info']['mobilesdk_app_id'] = (
            '1:9223372036854775807:android:0123456789abcdef')
        result = self.run_generator(config)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(self.output.read_text())['enabled'])

    def test_private_keys_and_provider_secrets_are_rejected(self):
        for field in ('private_key', 'gemini_api_key', 'refresh_token', 'client_secret'):
            with self.subTest(field=field):
                config = firebase_config()
                config[field] = 'sensitive-value-never-print'
                result = self.assert_rejected(config)
                self.assertNotIn('sensitive-value-never-print', result.stdout + result.stderr)

    def test_service_account_document_is_rejected(self):
        self.assert_rejected({'type': 'service_account', 'project_id': 'majlis-example'})

    def test_invalid_public_key_project_and_app_id_are_rejected(self):
        for target in ('key', 'project', 'app_id'):
            with self.subTest(target=target):
                config = firebase_config()
                if target == 'key':
                    config['client'][0]['api_key'][0]['current_key'] = 'sk-provider-secret'
                elif target == 'project':
                    config['project_info']['project_id'] = 'https://untrusted.example'
                else:
                    config['client'][0]['client_info']['mobilesdk_app_id'] = '1:123456789012:ios:abc'
                self.assert_rejected(config)

    def test_model_override_is_an_identifier_and_never_a_url(self):
        result = self.run_generator(firebase_config(), 'gemini-2.5-flash')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.output.read_text())['model'], 'gemini-2.5-flash')
        result = self.run_generator(firebase_config(), 'https://untrusted.example/model')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_malformed_structure_is_rejected_without_traceback(self):
        for value in ([], {}, {'project_info': [], 'client': {}}, {'client': [None]}):
            with self.subTest(value=value):
                result = self.assert_rejected(value)
                self.assertNotIn('Traceback', result.stderr)


class BuildContracts(unittest.TestCase):
    def test_release_metadata_uses_requested_version_and_sdk_levels(self):
        gradle = (ROOT / 'android/app/build.gradle').read_text(encoding='utf-8')
        for setting, expected in (('minSdk', '26'), ('targetSdk', '35'),
                                  ('compileSdk', '35'), ('versionCode', '6')):
            values = re.findall(r'^\s*' + setting + r'\s+(\d+)\s*$', gradle, re.MULTILINE)
            self.assertEqual(values, [expected], setting)
        self.assertEqual(re.findall(r"^\s*versionName\s+'([^']+)'\s*$", gradle, re.MULTILINE), ['0.6'])

    def test_cached_gradle_distribution_rejects_wrong_hash_before_execution(self):
        self.assertTrue(BOOTSTRAP.is_file(), 'Gradle bootstrap is missing')
        with tempfile.TemporaryDirectory(prefix='majlis-gradle-hash-test-') as directory:
            cache = Path(directory)
            (cache / 'gradle-8.9-bin.zip').write_bytes(b'corrupt-not-a-distribution')
            installed = cache / 'gradle-8.9/bin/gradle'
            installed.parent.mkdir(parents=True)
            marker = cache / 'unsafe-execution'
            installed.write_text('#!/usr/bin/env bash\ntouch "' + str(marker) + '"\n')
            installed.chmod(0o755)
            env = dict(os.environ, MAJLIS_GRADLE_CACHE_DIR=str(cache))
            result = subprocess.run(['bash', str(BOOTSTRAP)], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('SHA-256', result.stderr)
            self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
