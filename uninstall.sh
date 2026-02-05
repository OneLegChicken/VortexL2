#!/bin/bash
#
# VortexL2 Uninstaller
# L2TPv3 Tunnel Manager for Ubuntu/Debian
#
# Usage: curl -fsSL https://raw.githubusercontent.com/iliya-Developer/VortexL2/main/uninstall.sh | sudo bash
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
INSTALL_DIR="/opt/vortexl2"
BIN_PATH="/usr/local/bin/vortexl2"
SYSTEMD_DIR="/etc/systemd/system"
CONFIG_DIR="/etc/vortexl2"
LOG_DIR="/var/log/vortexl2"
VORTEXL2_SERVICE="vortexl2-tunnel.service"
FORWARD_DAEMON_SERVICE="vortexl2-forward-daemon.service"
HAPROXY_SERVICE="haproxy"

echo -e "${CYAN}"
cat << 'EOF'
 __      __        _            _     ___  
 \ \    / /       | |          | |   |__ \ 
  \ \  / /__  _ __| |_ _____  _| |      ) |
   \ \/ / _ \| '__| __/ _ \ \/ / |     / / 
    \  / (_) | |  | ||  __/>  <| |____/ /_ 
     \/ \___/|_|   \__\___/_/\_\______|____|
EOF
echo -e "${NC}"
echo -e "${GREEN}VortexL2 Uninstaller${NC}"
echo -e "${CYAN}L2TPv3 Tunnel Manager for Ubuntu/Debian${NC}"
echo ""

# Check root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: Please run as root (use sudo)${NC}"
    exit 1
fi

# Stop services
echo -e "${YELLOW}Stopping VortexL2 services...${NC}"
systemctl stop "$VORTEXL2_SERVICE" || true
systemctl stop "$FORWARD_DAEMON_SERVICE" || true
systemctl stop "$HAPROXY_SERVICE" || true

# Disable services
echo -e "${YELLOW}Disabling VortexL2 services...${NC}"
systemctl disable "$VORTEXL2_SERVICE" || true
systemctl disable "$FORWARD_DAEMON_SERVICE" || true
systemctl disable "$HAPROXY_SERVICE" || true

# Remove systemd service files
echo -e "${YELLOW}Removing systemd service files...${NC}"
rm -f "$SYSTEMD_DIR/$VORTEXL2_SERVICE" "$SYSTEMD_DIR/$FORWARD_DAEMON_SERVICE" "$SYSTEMD_DIR/$HAPROXY_SERVICE"

# Remove VortexL2 installation
echo -e "${YELLOW}Removing VortexL2 installation files...${NC}"
rm -rf "$INSTALL_DIR"

# Remove binary path
echo -e "${YELLOW}Removing binary path...${NC}"
rm -f "$BIN_PATH"

# Remove configuration directories
echo -e "${YELLOW}Removing configuration directories...${NC}"
rm -rf "$CONFIG_DIR"

# Remove logs and other data directories
echo -e "${YELLOW}Removing log and data directories...${NC}"
rm -rf "$LOG_DIR"

# Remove HAProxy (if installed by the script)
echo -e "${YELLOW}Removing HAProxy...${NC}"
apt-get remove --purge -y haproxy

# Remove dependencies installed by the script
echo -e "${YELLOW}Removing installed dependencies...${NC}"
apt-get remove --purge -y python3 python3-pip python3-venv git iproute2

# Clean up any leftover packages
echo -e "${YELLOW}Cleaning up leftover packages...${NC}"
apt-get autoremove -y
apt-get clean

echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  VortexL2 Uninstallation Complete!${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo -e "${CYAN}VortexL2 has been successfully uninstalled from your system.${NC}"
