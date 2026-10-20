#!/usr/bin/env bash
# Run the offline suite with instrumented C++ fixtures; production flags stay separate.
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$PROJECT_DIR/.lint-venv/bin:$PROJECT_DIR/.lint-tools/bin:$PATH"
export BMB_SANITIZER_CXX
BMB_SANITIZER_CXX="$(command -v c++)"

wrapper_dir="$(mktemp -d "${TMPDIR:-/tmp}/bmb-sanitizers.XXXXXX")"
trap 'rm -rf -- "$wrapper_dir"' EXIT
cat > "$wrapper_dir/c++" <<'COMPILER'
#!/usr/bin/env bash
set -euo pipefail
exec "$BMB_SANITIZER_CXX" -O1 -g -fsanitize=address,undefined \
  -fno-omit-frame-pointer -fno-sanitize-recover=all "$@"
COMPILER
chmod 0755 "$wrapper_dir/c++"
export PATH="$wrapper_dir:$PATH"
export ASAN_OPTIONS="halt_on_error=1:log_path=$wrapper_dir/address"
export UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1:log_path=$wrapper_dir/undefined"

# Do not exec: the wrapper directory must be removed when the suite exits.
result=0
bash "$PROJECT_DIR/tests/test-check.sh" || result=$?
# Negative-path tests may accept non-zero child exits and capture stderr. Still
# fail the whole run for every sanitizer diagnostic, including those children.
for report in "$wrapper_dir"/address.* "$wrapper_dir"/undefined.*; do
  if [[ -f "$report" ]]; then
    cat "$report" >&2
    result=1
  fi
done
exit "$result"
