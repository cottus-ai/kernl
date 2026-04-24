#!/bin/bash
# Install Kernl sandbox dependencies.
#
# This script:
#   1. Installs bubblewrap if not present
#   2. Creates an AppArmor profile so bwrap can create user namespaces
#   3. Verifies the sandbox works
#
# Requires: sudo

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Kernl Sandbox Setup ==="
echo ""

# 1. Check/install bwrap
if command -v bwrap &>/dev/null; then
    echo -e "${GREEN}[ok]${NC} bwrap found at $(which bwrap)"
else
    echo "Installing bubblewrap..."
    sudo apt-get install -y bubblewrap
fi

# 2. Install AppArmor profile
PROFILE_SRC="${SCRIPT_DIR}/apparmor-bwrap"
PROFILE_DST="/etc/apparmor.d/bwrap"

if [ ! -f "$PROFILE_SRC" ]; then
    echo -e "${RED}[error]${NC} AppArmor profile not found at ${PROFILE_SRC}"
    exit 1
fi

echo "Installing AppArmor profile for bwrap..."
sudo cp "$PROFILE_SRC" "$PROFILE_DST"
sudo apparmor_parser -r "$PROFILE_DST"
echo -e "${GREEN}[ok]${NC} AppArmor profile installed and loaded"

# 3. Verify
echo ""
echo "Verifying sandbox..."
if bwrap --ro-bind / / --tmpfs /tmp -- /bin/echo "sandbox works" 2>/dev/null; then
    echo -e "${GREEN}[ok]${NC} bwrap sandbox is functional"
else
    echo -e "${RED}[fail]${NC} bwrap still cannot create namespaces"
    echo "  Try: sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0"
    exit 1
fi

# 4. Test full isolation
echo ""
echo "Testing namespace isolation..."
RESULT=$(bwrap \
    --ro-bind /usr /usr \
    --ro-bind /lib /lib \
    --ro-bind-try /lib64 /lib64 \
    --ro-bind-try /lib/x86_64-linux-gnu /lib/x86_64-linux-gnu \
    --ro-bind /etc/ssl /etc/ssl \
    --symlink /usr/bin /bin \
    --proc /proc \
    --dev /dev \
    --tmpfs /tmp \
    --tmpfs /home \
    --unshare-pid \
    --unshare-ipc \
    --unshare-uts \
    --hostname kernl \
    --die-with-parent \
    --clearenv \
    --setenv PATH /usr/bin:/bin \
    -- /usr/bin/python3 -c "
import os, sys
pid = os.getpid()
hostname = os.uname().nodename
home_empty = len(os.listdir('/home')) == 0
print(f'PID={pid} hostname={hostname} /home_empty={home_empty}')
" 2>&1)

echo "  $RESULT"

if echo "$RESULT" | grep -q "PID=1.*hostname=kernl.*/home_empty=True"; then
    echo -e "${GREEN}[ok]${NC} Full namespace isolation verified"
    echo "      PID namespace:  agent sees itself as PID 1"
    echo "      UTS namespace:  hostname is 'kernl', not host"
    echo "      Mount namespace: /home is empty (host files hidden)"
else
    echo -e "${RED}[warn]${NC} Partial isolation — check output above"
fi

echo ""
echo "=== Setup complete ==="
