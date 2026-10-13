#!/usr/bin/env python3
"""Generate an allowlisted Firebase public config asset, never provider secrets."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile

DEFAULT_MODEL = 'gemini-3.5-flash-lite'
SENSITIVE_FIELDS = {
    'private_key', 'private_key_id', 'client_secret', 'client_email',
    'access_token', 'refresh_token', 'gemini_api_key', 'provider_api_key',
    'openai_api_key', 'anthropic_api_key', 'service_account',
}


class ConfigError(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError('duplicate JSON field')
        result[key] = value
    return result


def reject_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in SENSITIVE_FIELDS:
                raise ConfigError('provider or private credentials are not accepted')
            if key == 'type' and child == 'service_account':
                raise ConfigError('service account documents are not accepted')
            reject_secrets(child)
    elif isinstance(value, list):
        for child in value:
            reject_secrets(child)
    elif isinstance(value, str) and 'PRIVATE KEY-----' in value:
        raise ConfigError('private credentials are not accepted')


def matching_string(value, pattern, error):
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise ConfigError(error)
    return value


def cloud_config(source, package, model):
    matching_string(model, r'gemini-[a-z0-9][a-z0-9.-]{0,99}', 'model must be a Gemini model identifier')
    config = {'enabled': False, 'project_id': '', 'api_key': '', 'app_id': '', 'model': model}
    if not source.exists():
        return config
    if source.stat().st_size > 1_000_000:
        raise ConfigError('configuration file is too large')
    document = json.loads(source.read_text(encoding='utf-8'), object_pairs_hook=unique_object)
    reject_secrets(document)
    if not isinstance(document, dict):
        raise ConfigError('expected a Firebase Android configuration object')
    project = document.get('project_info')
    clients = document.get('client')
    if not isinstance(project, dict) or not isinstance(clients, list):
        raise ConfigError('missing Firebase project or Android clients')
    matches = []
    for client in clients:
        if not isinstance(client, dict):
            raise ConfigError('invalid Android client')
        info = client.get('client_info', {})
        if not isinstance(info, dict):
            raise ConfigError('invalid Android client information')
        android = info.get('android_client_info', {})
        if not isinstance(android, dict):
            raise ConfigError('invalid Android package information')
        if android.get('package_name') == package:
            matches.append(client)
    if len(matches) != 1:
        raise ConfigError('expected exactly one Android client for the application package')
    project_id = matching_string(project.get('project_id'), r'[a-z][a-z0-9-]{4,28}[a-z0-9]',
                                 'invalid Firebase project ID')
    project_number = matching_string(project.get('project_number'), r'[1-9][0-9]{0,29}',
                                     'invalid Firebase project number')
    if int(project_number) > 2 ** 63 - 1:
        raise ConfigError('Firebase project number exceeds the supported signed long range')
    client = matches[0]
    app_id = matching_string(client['client_info'].get('mobilesdk_app_id'),
                             r'1:[1-9][0-9]{0,29}:android:[a-fA-F0-9]{16,64}',
                             'invalid Firebase Android application ID')
    if app_id.split(':')[1] != project_number:
        raise ConfigError('Firebase application ID does not match the project number')
    keys = client.get('api_key')
    if not isinstance(keys, list) or len(keys) != 1 or not isinstance(keys[0], dict):
        raise ConfigError('expected one Firebase public API key for the Android client')
    api_key = matching_string(keys[0].get('current_key'), r'AIza[A-Za-z0-9_-]{35}',
                              'invalid Firebase public API key')
    config.update(enabled=True, project_id=project_id, api_key=api_key, app_id=app_id)
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--package', default='com.majlis.app')
    parser.add_argument('--model', default=DEFAULT_MODEL)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error('input and output must be different files')
    temporary = None
    try:
        args.output.unlink(missing_ok=True)
        config = cloud_config(args.input, args.package, args.model)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent,
                                         prefix='.cloud-config-', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(config, handle, sort_keys=True, separators=(',', ':'))
            handle.write('\n')
        os.replace(temporary, args.output)
        temporary = None
        print('Cloud configuration: enabled' if config['enabled'] else 'Cloud configuration: not configured')
        return 0
    except ConfigError as error:
        print('Cloud configuration rejected: ' + str(error), file=sys.stderr)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        print('Cloud configuration rejected: unreadable or malformed JSON', file=sys.stderr)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return 1


if __name__ == '__main__':
    sys.exit(main())
