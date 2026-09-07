from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from deduplicate_bitwarden import merge_exports, write_private


def login(identifier, uris, **extra):
    result = {'id': identifier, 'type': 1, 'name': identifier,
              'login': {'username': 'Alice', 'password': 'secret',
                        'uris': [{'uri': uri, 'match': None} for uri in uris]}}
    result.update(extra)
    return result


def export(items, folders=None):
    return {'encrypted': False, 'folders': folders or [], 'items': items}


class MergeTests(unittest.TestCase):
    def test_bridge_and_all_urls(self):
        a, b, c = 'https://a.test', 'https://b.test', 'https://c.test'
        data = merge_exports([export([login('a', [a]), login('b', [b, c])]),
                              export([login('bridge', [a, b]), login('last', [c])])])
        self.assertEqual(len(data['items']), 1)
        self.assertEqual(len(data['items'][0]['login']['uris']), 3)

    def test_conflicting_secrets_preserved(self):
        original = login('a', ['https://a.test'])
        variants = [original]
        for key, value in [('password', 'different'), ('username', 'alice'),
                           ('totp', 'different'), ('fido2Credentials', [{'keyValue': 'key'}])]:
            variant = deepcopy(original)
            variant['login'][key] = value
            variants.append(variant)
        for key, value in [('notes', 'note'), ('fields', [{'value': 'custom'}])]:
            variant = deepcopy(original)
            variant[key] = value
            variants.append(variant)
        result = merge_exports([export(variants)])
        self.assertEqual(len(result['items']), len(variants))
        self.assertEqual(len({i['id'] for i in result['items']}), len(variants))

    def test_full_site_identity(self):
        uris = ['https://a.example.co.uk', 'https://b.example.co.uk',
                'https://a.example.co.uk/Path', 'https://a.example.co.uk/path',
                'http://a.example.co.uk', 'https://a.example.co.uk:8443']
        result = merge_exports([export([login(str(n), [u]) for n, u in enumerate(uris)])])
        self.assertEqual(len(result['items']), len(uris))
        self.assertEqual(len(merge_exports([export([login('a', ['HTTPS://A.TEST']),
                                                  login('b', ['https://a.test/'])])])['items']), 1)

    def test_non_login_and_no_uri(self):
        items = [{'id': 'n1', 'type': 2, 'name': 'note', 'notes': 'one'},
                 {'id': 'n2', 'type': 2, 'name': 'note', 'notes': 'two'},
                 {'id': 'c1', 'type': 3, 'name': 'card', 'card': {'number': '111'}},
                 {'id': 'c2', 'type': 3, 'name': 'card', 'card': {'number': '222'}},
                 login('no-uri-one', []), login('no-uri-two', [])]
        self.assertEqual(len(merge_exports([export(items + deepcopy(items))])['items']), len(items))

    def test_folders_are_source_scoped(self):
        result = merge_exports([
            export([login('a', ['a'], folderId='second')],
                   [{'id': 'first', 'name': 'Work'}, {'id': 'second', 'name': 'Work'}]),
            export([login('b', ['b'], folderId='first')], [{'id': 'first', 'name': 'Home'}])])
        self.assertEqual(len(result['folders']), 2)
        names = {f['id']: f['name'] for f in result['folders']}
        self.assertEqual([names[i['folderId']] for i in result['items']], ['Work', 'Home'])

    def test_metadata_and_history(self):
        a = login('a', ['a'], passwordHistory=[{'password': 'old'}])
        b = login('b', ['a'], favorite=True, reprompt=1,
                  passwordHistory=[{'password': 'older'}], revisionDate='later', notes='')
        sources = [export([a, b])]
        snapshot = deepcopy(sources)
        result = merge_exports(sources)
        self.assertEqual(sources, snapshot)
        self.assertEqual(len(result['items']), 1)
        item = result['items'][0]
        self.assertTrue(item['favorite'])
        self.assertEqual(item['reprompt'], 1)
        self.assertEqual(len(item['passwordHistory']), 2)
        self.assertEqual(merge_exports([result]), result)

    def test_reject_invalid_exports(self):
        for data in [{'encrypted': True, 'items': [], 'folders': []},
                     export([login('a', [], folderId='missing')])]:
            with self.assertRaises(ValueError):
                merge_exports([data])

    def test_explicit_uri_modes_are_verbatim(self):
        a = login('a', [r'https://\D.test'])
        b = login('b', [r'https://\d.test'])
        for item in (a, b):
            item['login']['uris'][0]['match'] = 4
        self.assertEqual(len(merge_exports([export([a, b])])['items']), 2)

    def test_bridge_uses_first_available_folder(self):
        items = [login('a', ['a']), login('b', ['b'], folderId='B'),
                 login('c', ['a'], folderId='C'), login('bridge', ['a', 'b'])]
        folders = [{'id': key, 'name': key} for key in ('B', 'C')]
        result = merge_exports([export(items, folders)])
        self.assertEqual(result['items'][0]['folderId'], 'B')

    def test_failed_write_removes_only_new_file(self):
        import os
        real_fdopen = os.fdopen

        class FailingWriter:
            def __init__(self, fd, *args, **kwargs):
                self.stream = real_fdopen(fd, *args, **kwargs)
            def __enter__(self):
                return self
            def write(self, content):
                self.stream.write(content[:5])
                raise OSError('synthetic disk failure')
            def __exit__(self, *args):
                self.stream.close()

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'new.json'
            with patch('deduplicate_bitwarden.os.fdopen', FailingWriter):
                with self.assertRaises(OSError):
                    write_private(output, 'synthetic content')
            self.assertFalse(output.exists())
            output.write_text('keep')
            with self.assertRaises(FileExistsError):
                write_private(output, 'replacement')
            self.assertEqual(output.read_text(), 'keep')

    def test_cli_and_no_overwrite(self):
        script = Path(__file__).resolve().parents[1] / 'deduplicate_bitwarden.py'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = [root / 'a.json', root / 'b.json']
            for path in inputs:
                path.write_text(json.dumps(export([login('a', ['https://a.test'])])))
            output = root / 'merged.json'
            summary = root / 'summary.md'
            command = [sys.executable, str(script), *map(str, inputs), '-o', str(output),
                       '-s', str(summary), '-q']
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')
            self.assertEqual(len(json.loads(output.read_text())['items']), 1)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertTrue(summary.exists())
            before = output.read_bytes()
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(output.read_bytes(), before)
            self.assertNotEqual(subprocess.run([sys.executable, str(script), str(inputs[0]),
                                               '-o', str(inputs[0])], capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
