#!/usr/bin/env python3
"""Merge and safely deduplicate unencrypted personal Bitwarden JSON exports."""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4


def canonical(value):
    """Compare optional empty fields consistently without altering saved data."""
    if isinstance(value, dict):
        value = {k: canonical(v) for k, v in value.items()}
        return {k: v for k, v in value.items() if v not in (None, '', [], {})}
    if isinstance(value, list):
        return [canonical(v) for v in value]
    return value


def fingerprint(value):
    return json.dumps(canonical(value), sort_keys=True, ensure_ascii=False)


def normalize_uri(uri):
    """Normalize web host case and an empty root path, not site identity."""
    try:
        parts = urlsplit(uri)
        if parts.scheme.lower() not in ('http', 'https') or not parts.netloc:
            return uri
        userinfo, separator, host = parts.netloc.rpartition('@')
        netloc = (userinfo + separator if separator else '') + host.lower()
        return urlunsplit((parts.scheme.lower(), netloc, parts.path or '/',
                           parts.query, parts.fragment))
    except ValueError:
        return uri


def item_keys(item):
    payload = deepcopy(item)
    for key in ('id', 'folderId', 'creationDate', 'revisionDate',
                'passwordHistory', 'favorite', 'reprompt'):
        payload.pop(key, None)
    login = payload.get('login')
    uris = login.get('uris') if isinstance(login, dict) else None
    if item.get('type') == 1 and uris:
        # A shared full URL and identical saved details establish a duplicate.
        # Names may differ across imports; URI-less items retain their names.
        payload.pop('name', None)
        login.pop('uris', None)
        sites = []
        for uri in uris:
            normalized = deepcopy(uri)
            raw_uri = uri.get('uri') or ''
            # Explicit matching rules may interpret case or syntax literally.
            normalized['uri'] = (normalize_uri(raw_uri)
                                 if uri.get('match') is None else raw_uri)
            if normalized['uri']:
                sites.append(fingerprint(normalized))
        if sites:
            base = fingerprint(payload)
            return [(base, site) for site in sites]
        payload = deepcopy(item)
        for key in ('id', 'folderId', 'creationDate', 'revisionDate',
                    'passwordHistory', 'favorite', 'reprompt'):
            payload.pop(key, None)
    return [(fingerprint(payload), None)]


def merge_details(target, source):
    """Retain all URLs/history and the stronger favorite/reprompt settings."""
    target['favorite'] = bool(target.get('favorite') or source.get('favorite'))
    target['reprompt'] = max(target.get('reprompt') or 0, source.get('reprompt') or 0)
    if not target.get('folderId') and source.get('folderId'):
        target['folderId'] = source['folderId']
    pairs = [(target, source, 'passwordHistory')]
    if isinstance(target.get('login'), dict) and isinstance(source.get('login'), dict):
        pairs.append((target['login'], source['login'], 'uris'))
    for dest, incoming, key in pairs:
        if incoming.get(key):
            values = dest.setdefault(key, []) or []
            dest[key] = values
            seen = {fingerprint(v) for v in values}
            for value in incoming[key]:
                identity = fingerprint(value)
                if identity not in seen:
                    values.append(deepcopy(value))
                    seen.add(identity)


def merge_exports(exports):
    """Merge source-scoped folders and connected groups of duplicate items."""
    output = {'encrypted': False, 'folders': [], 'items': []}
    folders = {}
    folder_ids = set()
    items = []
    for data in exports:
        if (not isinstance(data, dict) or data.get('encrypted') is not False
                or not isinstance(data.get('items'), list)
                or not isinstance(data.get('folders'), list)):
            raise ValueError('Expected an unencrypted personal export with folders and items arrays.')
        # Preserve top-level extensions only when exports agree.
        for key, value in data.items():
            if key in ('folders', 'items', 'encrypted'):
                continue
            if key not in output:
                output[key] = deepcopy(value)
            elif output[key] != value:
                raise ValueError('Exports have conflicting top-level metadata.')
        mapping = {}
        for folder in data['folders']:
            if not isinstance(folder, dict) or not folder.get('id') or not isinstance(folder.get('name'), str):
                raise ValueError('Invalid folder structure.')
            old_id = folder['id']
            identity = fingerprint({k: v for k, v in folder.items() if k != 'id'})
            if identity not in folders:
                saved = deepcopy(folder)
                if saved['id'] in folder_ids:
                    saved['id'] = str(uuid4())
                folder_ids.add(saved['id'])
                folders[identity] = saved
                output['folders'].append(saved)
            new_id = folders[identity]['id']
            if old_id in mapping and mapping[old_id] != new_id:
                raise ValueError('An export contains conflicting folder IDs.')
            mapping[old_id] = new_id
        for original in data['items']:
            if not isinstance(original, dict) or not isinstance(original.get('type'), int):
                raise ValueError('Invalid item structure.')
            item = deepcopy(original)
            if item.get('folderId'):
                if item['folderId'] not in mapping:
                    raise ValueError('An item references a missing folder.')
                item['folderId'] = mapping[item['folderId']]
            items.append(item)

    index = {}
    groups = {}
    for position, item in enumerate(items):
        keys = set(item_keys(item))
        matches = {index[key] for key in keys if key in index}
        if not matches:
            groups[position] = ([(position, item)], keys)
            survivor = position
        else:
            survivor = min(matches)
            members, existing_keys = groups[survivor]
            for other in sorted(matches - {survivor}):
                duplicate_members, duplicate_keys = groups.pop(other)
                members.extend(duplicate_members)
                existing_keys.update(duplicate_keys)
            members.append((position, item))
            existing_keys.update(keys)
            keys = existing_keys
        for key in keys:
            index[key] = survivor
    used_ids = set()
    for members, _ in groups.values():
        members.sort(key=lambda member: member[0])
        item = members[0][1]
        for _, duplicate in members[1:]:
            merge_details(item, duplicate)
        if not item.get('id') or item['id'] in used_ids:
            item['id'] = str(uuid4())
        used_ids.add(item['id'])
        output['items'].append(item)
    return output


def write_private(path, content):
    """Never overwrite an existing file; vault outputs are owner-readable only."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
    except BaseException:
        # Only remove the new file this call exclusively created.
        os.unlink(path)
        raise


def deduplicate_bitwarden_export(input_files, output_file=None, summary_file=None, quiet=False):
    if isinstance(input_files, (str, os.PathLike)):
        input_files = [input_files]
    paths = [Path(p) for p in input_files]
    if not paths:
        raise ValueError('At least one input file is required.')
    output = Path(output_file) if output_file else paths[0].with_name(
        paths[0].stem + ('_merged_deduplicated.json' if len(paths) > 1 else '_deduplicated.json'))
    destinations = [output] + ([Path(summary_file)] if summary_file else [])
    resolved = [p.resolve() for p in paths + destinations]
    if len(set(resolved)) != len(resolved):
        raise ValueError('Inputs, output, and summary must use distinct paths.')
    if any(p.exists() for p in destinations):
        raise ValueError('Output or summary already exists; choose a new path.')
    exports = []
    for number, path in enumerate(paths, 1):
        with path.open(encoding='utf-8-sig') as stream:
            exports.append(json.load(stream))
        if not quiet:
            print(f'Loaded export {number}/{len(paths)}.')
    data = merge_exports(exports)
    summary = {
        'original_files': [str(p) for p in paths],
        'original_folders': sum(len(d['folders']) for d in exports),
        'original_items': sum(len(d['items']) for d in exports),
        'deduplicated_file': str(output),
        'deduplicated_folders': len(data['folders']),
        'deduplicated_items': len(data['items']),
    }
    for kind in ('folders', 'items'):
        summary['removed_' + kind] = summary['original_' + kind] - summary['deduplicated_' + kind]
    write_private(output, json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    if not quiet:
        print(f"Items: {summary['original_items']} -> {summary['deduplicated_items']}; "
              f"folders: {summary['original_folders']} -> {summary['deduplicated_folders']}.")
    if summary_file:
        write_private(summary_file, '# Bitwarden Merge and Deduplication Summary\n\n' +
                      '\n'.join(f'- {key}: {value}' for key, value in summary.items()) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input_files', nargs='+', help='One or more unencrypted JSON exports')
    parser.add_argument('-o', '--output', help='New output JSON path')
    parser.add_argument('-s', '--summary', help='New Markdown summary path')
    parser.add_argument('-q', '--quiet', action='store_true', help='Suppress progress output')
    args = parser.parse_args()
    try:
        deduplicate_bitwarden_export(args.input_files, args.output, args.summary, args.quiet)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # Exception text may include vault values. Never echo it or a traceback.
        print('Error: cannot merge exports. Check input structure, folder references, '
              'matching metadata, and that output paths are new and writable.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
