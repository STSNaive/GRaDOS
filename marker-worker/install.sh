#!/usr/bin/env bash
set -euo pipefail

DEVICE="auto"
CUDA_BACKEND="auto"
SKIP_PREWARM=0
RECREATE_VENV=0
DEVICE_SET=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --device|--torch)
      if [[ $# -lt 2 ]]; then
        echo "[install-marker] Missing value for $1" >&2
        exit 1
      fi
      DEVICE="$2"
      DEVICE_SET=1
      shift 2
      ;;
    --cuda-backend)
      if [[ $# -lt 2 ]]; then
        echo "[install-marker] Missing value for $1" >&2
        exit 1
      fi
      CUDA_BACKEND="$2"
      shift 2
      ;;
    --skip-prewarm)
      SKIP_PREWARM=1
      shift
      ;;
    --recreate-venv)
      RECREATE_VENV=1
      shift
      ;;
    -h|--help)
      cat <<'EOF'
Usage: ./install.sh [options]

Options:
  --device auto|cpu|cuda   Device preference (default: auto)
  --cuda-backend VALUE     CUDA backend for uv (default: auto)
  --skip-prewarm           Skip model prewarm
  --recreate-venv          Remove and recreate .venv before install
EOF
      exit 0
      ;;
    *)
      echo "[install-marker] Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ "$DEVICE" != "auto" && "$DEVICE" != "cpu" && "$DEVICE" != "cuda" ]]; then
  echo "[install-marker] Unsupported device: $DEVICE" >&2
  exit 1
fi

MARKER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE_ROOT="$MARKER_ROOT/.cache"
UV_CACHE_DIR="$CACHE_ROOT/uv"
LOCAL_TOOLS_DIR="$MARKER_ROOT/.tools"
LOCAL_UV_DIR="$LOCAL_TOOLS_DIR/uv"
LOCAL_UV_BIN="$LOCAL_UV_DIR/uv"
LOCAL_ENV_PATH="$MARKER_ROOT/local.env"
VENV_PYTHON="$MARKER_ROOT/.venv/bin/python"
PREWARM_SCRIPT="$MARKER_ROOT/prewarm.py"
VERIFY_SCRIPT="$MARKER_ROOT/verify.py"

write_step() {
  printf '[install-marker] %s\n' "$1"
}

ensure_dir() {
  mkdir -p "$1"
}

has_nvidia() {
  command -v nvidia-smi >/dev/null 2>&1 || return 1
  nvidia-smi --query-gpu=name --format=csv,noheader >/dev/null 2>&1
}

is_interactive() {
  [[ -t 0 && -t 1 && -z "${CI:-}" ]]
}

resolve_uv() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return
  fi

  if [[ -x "$LOCAL_UV_BIN" ]]; then
    printf '%s\n' "$LOCAL_UV_BIN"
    return
  fi

  ensure_dir "$LOCAL_UV_DIR"
  write_step "uv not found; installing a local copy into $LOCAL_UV_DIR"

  UV_UNMANAGED_INSTALL="$LOCAL_UV_DIR" curl -LsSf https://astral.sh/uv/install.sh | sh

  if [[ ! -x "$LOCAL_UV_BIN" ]]; then
    echo "[install-marker] uv installation failed. Install uv manually and rerun this script." >&2
    exit 1
  fi

  printf '%s\n' "$LOCAL_UV_BIN"
}

write_local_env() {
  local python_relative_path="$1"
  local torch_device="${2:-}"

  {
    printf 'MARKER_PYTHON=%s\n' "$python_relative_path"
    if [[ -n "$torch_device" ]]; then
      printf 'TORCH_DEVICE=%s\n' "$torch_device"
    fi
  } > "$LOCAL_ENV_PATH"
}

sync_environment() {
  write_step "Syncing marker-worker environment"
  "$UV_EXE" sync --project "$MARKER_ROOT" --link-mode copy
}

install_cuda_torch() {
  write_step "Installing CUDA-enabled PyTorch backend: $CUDA_BACKEND"
  "$UV_EXE" pip install --python "$VENV_PYTHON" --link-mode copy --reinstall-package torch --torch-backend "$CUDA_BACKEND" torch
}

run_validation() {
  if [[ "$SKIP_PREWARM" -eq 0 ]]; then
    write_step "Prewarming Marker models into $CACHE_ROOT"
    "$VENV_PYTHON" "$PREWARM_SCRIPT"
  else
    write_step "Skipping prewarm. Models will download on first parse."
  fi

  write_step "Verifying Marker worker handshake"
  "$VENV_PYTHON" "$VERIFY_SCRIPT"
}

if [[ "$DEVICE_SET" -eq 0 && "$(uname -s)" == "Linux" ]] && has_nvidia && is_interactive; then
  printf 'Detected NVIDIA GPU. Install CUDA-enabled PyTorch for Marker? [y/N] '
  read -r answer || true
  case "$answer" in
    [Yy]|[Yy][Ee][Ss])
      DEVICE="cuda"
      ;;
    *)
      DEVICE="auto"
      ;;
  esac
fi

if [[ "$DEVICE" == "cuda" && "$(uname -s)" == "Darwin" ]]; then
  echo "[install-marker] CUDA is not available on macOS. Use --device auto or --device cpu." >&2
  exit 1
fi

ensure_dir "$CACHE_ROOT"
ensure_dir "$UV_CACHE_DIR"

export UV_CACHE_DIR
export UV_LINK_MODE=copy

UV_EXE="$(resolve_uv)"
write_step "Using uv: $UV_EXE"

if [[ "$RECREATE_VENV" -eq 1 && -d "$MARKER_ROOT/.venv" ]]; then
  write_step "Removing existing marker virtualenv"
  rm -rf "$MARKER_ROOT/.venv"
fi

sync_environment

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "[install-marker] Marker virtualenv was not created at $VENV_PYTHON" >&2
  exit 1
fi

TORCH_DEVICE_OVERRIDE=""
case "$DEVICE" in
  cpu)
    TORCH_DEVICE_OVERRIDE="cpu"
    ;;
  cuda)
    TORCH_DEVICE_OVERRIDE="cuda"
    ;;
esac

write_local_env ".venv/bin/python" "$TORCH_DEVICE_OVERRIDE"

if [[ "$DEVICE" == "cuda" ]]; then
  if ! install_cuda_torch; then
    write_step "CUDA installation failed. Falling back to CPU compatibility mode."
    TORCH_DEVICE_OVERRIDE="cpu"
    write_local_env ".venv/bin/python" "$TORCH_DEVICE_OVERRIDE"
  fi
fi

if ! run_validation; then
  if [[ "$TORCH_DEVICE_OVERRIDE" != "cpu" ]]; then
    MODE_LABEL="${TORCH_DEVICE_OVERRIDE:-auto}"
    write_step "Marker validation failed in $MODE_LABEL mode. Retrying with CPU compatibility mode."
    TORCH_DEVICE_OVERRIDE="cpu"
    write_local_env ".venv/bin/python" "$TORCH_DEVICE_OVERRIDE"
    run_validation
  else
    exit 1
  fi
fi

write_step "Done. Marker env: $VENV_PYTHON"
write_step "Marker metadata: $LOCAL_ENV_PATH"
write_step "Model cache: $CACHE_ROOT/models"
