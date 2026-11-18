#!/usr/bin/env bash
# THE EXPORT TIER'S DECISION (.github/workflows/ci.yml, on a pull request): read the changed paths,
# one per line, on stdin and print ONE line:
#   run: <path> matches <pattern>
#   run: no changed paths were read ...           (cannot tell, so run)
#   skip: none of the <n> changed paths matches .github/export-tier-paths.txt
# The globs are export-tier-paths.txt's; `*` matches across `/`, as Python's fnmatch does in
# tests/test_ci_tiers.py, which runs this script on synthetic diffs. Paths with spaces are not
# supported (none are tracked). A missing pattern file is an error, never a skip.
set -euo pipefail

list="$(cd "$(dirname "$0")" && pwd)/export-tier-paths.txt"
[ -f "$list" ] || { echo "export-tier.sh: no $list" >&2; exit 2; }

patterns=()
while IFS= read -r line || [ -n "$line" ]; do
    line=${line%%#*}
    line=${line//[[:space:]]/}
    [ -n "$line" ] && patterns+=("$line")
done < "$list"
[ "${#patterns[@]}" -gt 0 ] || { echo "export-tier.sh: $list lists no path" >&2; exit 2; }

n=0
while IFS= read -r path || [ -n "$path" ]; do
    [ -n "$path" ] || continue
    n=$((n + 1))
    for pat in "${patterns[@]}"; do
        # Unquoted on the right: a glob, not a string.
        if [[ $path == $pat ]]; then
            echo "run: $path matches $pat"
            exit 0
        fi
    done
done

if [ "$n" -eq 0 ]; then
    echo "run: no changed paths were read, so the tier cannot be skipped safely"
else
    echo "skip: none of the $n changed paths matches .github/export-tier-paths.txt"
fi
