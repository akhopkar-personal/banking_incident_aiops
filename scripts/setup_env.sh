#!/usr/bin/env bash
# Prepare the Python environment for a Linux session (Vocareum or Docker).
#
# Run at the start of every session, from anywhere:
#     source scripts/setup_env.sh            # set up and activate
#     source scripts/setup_env.sh --smoke    # also run the smoke test
#
# Safe to repeat. It creates .venv only if missing or broken, and reinstalls
# libraries only when deployment/requirements.txt has changed since the last
# install. Use "source" so the environment stays active in your terminal;
# "bash scripts/setup_env.sh" also works but does not keep it activated.

_setup_env_main() {
    local repo_root req venv stamp current_hash
    repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    req="$repo_root/deployment/requirements.txt"
    venv="$repo_root/.venv"
    stamp="$venv/.requirements.sha256"

    if ! command -v python3 >/dev/null 2>&1; then
        echo "ERROR: python3 not found." >&2
        return 1
    fi
    local pyver
    pyver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    if [ "$pyver" != "3.10" ]; then
        echo "WARNING: system Python is $pyver; the project targets 3.10." >&2
    fi

    # Create .venv if it is missing or its Python no longer runs
    # (for example after the system Python was updated).
    if [ ! -x "$venv/bin/python" ] || ! "$venv/bin/python" -c "import sys" >/dev/null 2>&1; then
        echo "Creating virtual environment in .venv ..."
        rm -rf "$venv"
        if ! python3 -m venv "$venv"; then
            echo "ERROR: could not create .venv. If the message mentions ensurepip," >&2
            echo "       the python3-venv package is missing; ask for the workaround." >&2
            return 1
        fi
    fi

    # shellcheck disable=SC1091
    source "$venv/bin/activate"

    current_hash="$(sha256sum "$req" | cut -d' ' -f1)"
    if [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$current_hash" ]; then
        echo "Installing libraries from deployment/requirements.txt (first run takes several minutes) ..."
        python -m pip install --upgrade pip --quiet || return 1
        python -m pip install -r "$req" || return 1
        python -m pip check || return 1
        echo "$current_hash" > "$stamp"
    else
        echo "Libraries are up to date with deployment/requirements.txt."
    fi

    # Check .env without printing any values.
    if [ ! -f "$repo_root/.env" ]; then
        cp "$repo_root/.env.example" "$repo_root/.env"
        echo "Created .env from .env.example. Fill in the keys before running the app."
    else
        local name missing=""
        for name in OPENAI_API_KEY OPENAI_BASE_URL LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY; do
            if ! grep -Eq "^${name}=.+" "$repo_root/.env"; then
                missing="$missing $name"
            fi
        done
        if [ -n "$missing" ]; then
            echo "WARNING: empty in .env:$missing"
        fi
    fi

    echo "Ready: $(python --version) in $venv"

    if [ "${1:-}" = "--smoke" ]; then
        (cd "$repo_root" && python scripts/smoke_test.py)
    fi
}

_setup_env_main "$@"
_setup_env_status=$?
unset -f _setup_env_main
# Return when sourced, exit when run with bash.
return "$_setup_env_status" 2>/dev/null || exit "$_setup_env_status"
