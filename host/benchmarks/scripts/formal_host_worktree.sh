#!/usr/bin/env bash

# Resolve the unique worktree that currently owns a formal Host branch.
resolve_formal_host_tree() {
    local repository=$1
    local expected_branch=$2
    local target_ref="refs/heads/${expected_branch}"
    local worktree_output
    local -a matches

    if [ ! -d "${repository}/.git" ]; then
        echo "ERROR: Host repository is unavailable: ${repository}" >&2
        return 1
    fi

    if ! worktree_output=$(git -C "${repository}" worktree list --porcelain); then
        echo "ERROR: failed to inspect Host worktrees in ${repository}" >&2
        return 1
    fi

    mapfile -t matches < <(
        awk -v target_ref="${target_ref}" '
            /^worktree / {
                path = substr($0, length("worktree ") + 1)
                next
            }
            /^branch / {
                branch = substr($0, length("branch ") + 1)
                if (branch == target_ref)
                    print path
            }
        ' <<< "${worktree_output}"
    )

    if [ "${#matches[@]}" -ne 1 ]; then
        echo "ERROR: expected one worktree for ${expected_branch}, found ${#matches[@]}." >&2
        echo "Action: check out the branch in the main Host repository or create a worktree for it." >&2
        return 1
    fi

    printf '%s\n' "${matches[0]}"
}
