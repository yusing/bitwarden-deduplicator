# Bitwarden JSON Export Merger and Deduplicator

Merge one or more unencrypted personal Bitwarden JSON exports into a new JSON file,
removing duplicate folders and entries. Uses only the Python 3 standard library.

## Usage

```bash
python3 deduplicate_bitwarden.py first_export.json second_export.json -o merged.json
python3 deduplicate_bitwarden.py single_export.json
```

- Pass one or more input files in order. Earlier entries supply the retained name and folder.
- `-o, --output`: new output path. Defaults to the first input's stem plus
  `_merged_deduplicated.json` for multiple files or `_deduplicated.json` for one.
- `-s, --summary`: optional new Markdown report containing counts and file paths, not vault values.
- `-q, --quiet`: suppress progress and counts. Errors still appear on stderr.
- Existing output files and inputs are never overwritten. Output permissions are owner-only.

For the two exports in this directory:

```bash
python3 deduplicate_bitwarden.py bitwarden_export_20260907102015.json bitwarden_export_20260907102713.json -o merged_deduplicated.json
```

## Duplicate rules

- Folder names and any extra folder properties must match. All original folder IDs
  are mapped to retained folders, separately for each source file.
- Logins match when they share at least one full URL (including its match setting)
  and all other saved details agree, except the display name and bookkeeping fields.
  Every URL is indexed, including URLs discovered through another duplicate.
- Passwords and usernames are case-sensitive. Different passwords, notes, custom
  fields, TOTP secrets, passkeys, organization ownership, or other saved details keep
  entries separate. Missing and empty optional fields compare equally.
- For URLs without an explicit match mode, web scheme/host case and empty root paths
  are normalized for comparison. Explicit match modes are compared verbatim. Subdomains,
  ports, paths, query strings, and HTTP versus HTTPS remain distinct. Other URI formats
  are compared exactly. This avoids combining unrelated sites or accounts.
- Cards, identities, secure notes, and logins without URLs match by saved content,
  including their name, not just their item type.
- IDs, folder placement, and creation/revision timestamps do not determine duplicates.
  The first entry supplies these values. All distinct URLs and password-history records
  are retained; favorites and the stronger reprompt setting are preserved. A later
  folder fills an empty earlier folder. Conflicting IDs on retained items are replaced.
- Conflicting top-level metadata, encrypted exports, and missing folder references are
  rejected rather than silently discarded.

This intentionally retains conflicting versions rather than guessing which secret is
correct. The merged file does not modify your live vault. Importing into an already
populated vault can create duplicates again; review your import destination first.

## Security

Exports and the merged output contain plaintext secrets. Keep them local and secure.
Normal progress output contains only counts, never entry details. Do not commit vault
exports or generated output to version control.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests use synthetic vaults only.

## License

MIT. See LICENSE.
