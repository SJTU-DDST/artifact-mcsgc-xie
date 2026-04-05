#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 /absolute/or/relative/path/to/file" >&2
    exit 1
fi

input="$1"

if [[ ! -f "$input" ]]; then
    echo "Error: file not found: $input" >&2
    exit 1
fi

tmp="$(mktemp "$(dirname "$input")/.tmp.XXXXXX")"

awk '
{
    is_type1 = (index($0, "systemd-journald") &&
                index($0, "Failed to write entry") &&
                index($0, "ignoring: Cannot assign requested address"))

    is_type2 = (index($0, "systemd-journald") &&
                index($0, "Journal file corrupted, rotating"))

    if (!is_type1 && !is_type2) {
        print
    }
}
' "$input" > "$tmp"

chmod --reference="$input" "$tmp"
chown --reference="$input" "$tmp" 2>/dev/null || true
mv "$tmp" "$input"