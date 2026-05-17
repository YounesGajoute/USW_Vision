#!/bin/bash

################################################################################
# Vision Inspection System - Nginx Installation & Configuration Script
################################################################################
#
# This script installs and configures Nginx as a reverse proxy for the
# Vision Inspection System.
#
# Usage:
#   sudo ./install_nginx.sh [options]
#
# Options:
#   --ssl-type [self-signed|letsencrypt]  SSL certificate type (default: self-signed)
#   --domain DOMAIN                       Domain name for Let's Encrypt
#   --email EMAIL                         Email for Let's Encrypt
#   --skip-install                        Skip nginx installation
#   --help                                Show this help
#
################################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Default settings
SSL_TYPE="self-signed"
DOMAIN="vision.local"
EMAIL=""
SKIP_INSTALL=false

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --ssl-type)
            SSL_TYPE="$2"
            shift 2
            ;;
        --domain)
            DOMAIN="$2"
            shift 2
            ;;
        --email)
            EMAIL="$2"
            shift 2
            ;;
        --skip-install)
            SKIP_INSTALL=true
            shift
            ;;
        --help)
            head -n 20 "$0" | tail -n +3 | sed 's/^# //'
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}✗ This script must be run as root${NC}"
   echo -e "${YELLOW}  Please run: sudo $0${NC}"
   exit 1
fi

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Vision Inspection System - Nginx Setup${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo

# ==================== INSTALL NGINX ====================

if [ "$SKIP_INSTALL" = false ]; then
    echo -e "${BLUE}→ Installing Nginx...${NC}"
    
    # Update package list
    apt-get update -qq
    
    # Install Nginx
    apt-get install -y nginx
    
    # Check installation
    if command -v nginx &> /dev/null; then
        NGINX_VERSION=$(nginx -v 2>&1 | cut -d'/' -f2)
        echo -e "${GREEN}✓ Nginx $NGINX_VERSION installed successfully${NC}"
    else
        echo -e "${RED}✗ Nginx installation failed${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}⊙ Skipping nginx installation${NC}"
fi

# ==================== CREATE SSL CERTIFICATES ====================

echo -e "${BLUE}→ Configuring SSL certificates...${NC}"

# Create SSL directory
mkdir -p /etc/nginx/ssl

if [ "$SSL_TYPE" = "self-signed" ]; then
    echo -e "${YELLOW}  Generating self-signed certificate...${NC}"
    
    # Generate self-signed certificate
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout /etc/nginx/ssl/vision-inspection.key \
        -out /etc/nginx/ssl/vision-inspection.crt \
        -subj "/C=US/ST=State/L=City/O=Organization/CN=$DOMAIN" \
        2>/dev/null
    
    if [ -f /etc/nginx/ssl/vision-inspection.crt ]; then
        echo -e "${GREEN}✓ Self-signed certificate created${NC}"
        echo -e "${YELLOW}  Warning: Self-signed certificates should only be used for development${NC}"
    else
        echo -e "${RED}✗ Failed to create certificate${NC}"
        exit 1
    fi
    
elif [ "$SSL_TYPE" = "letsencrypt" ]; then
    echo -e "${YELLOW}  Setting up Let's Encrypt...${NC}"
    
    # Check if domain is valid
    if [ "$DOMAIN" = "vision.local" ] || [ "$DOMAIN" = "localhost" ]; then
        echo -e "${RED}✗ Cannot use Let's Encrypt with local domain${NC}"
        echo -e "${YELLOW}  Please use a public domain or --ssl-type self-signed${NC}"
        exit 1
    fi
    
    # Check if email provided
    if [ -z "$EMAIL" ]; then
        echo -e "${RED}✗ Email required for Let's Encrypt${NC}"
        echo -e "${YELLOW}  Use: --email your@email.com${NC}"
        exit 1
    fi
    
    # Install certbot
    apt-get install -y certbot python3-certbot-nginx
    
    # Get certificate
    certbot certonly --nginx \
        -d "$DOMAIN" \
        --email "$EMAIL" \
        --agree-tos \
        --non-interactive
    
    # Update nginx config to use Let's Encrypt certificates
    sed -i "s|/etc/nginx/ssl/vision-inspection.crt|/etc/letsencrypt/live/$DOMAIN/fullchain.pem|g" \
        "$SCRIPT_DIR/vision-inspection.conf"
    sed -i "s|/etc/nginx/ssl/vision-inspection.key|/etc/letsencrypt/live/$DOMAIN/privkey.pem|g" \
        "$SCRIPT_DIR/vision-inspection.conf"
    
    echo -e "${GREEN}✓ Let's Encrypt certificate installed${NC}"
    
    # Setup auto-renewal
    systemctl enable certbot.timer
    systemctl start certbot.timer
    echo -e "${GREEN}✓ Auto-renewal configured${NC}"
fi

# ==================== INSTALL NGINX CONFIGURATION ====================

echo -e "${BLUE}→ Installing nginx configuration...${NC}"

# Copy configuration files
cp "$SCRIPT_DIR/vision-inspection.conf" /etc/nginx/sites-available/
cp "$SCRIPT_DIR/vision-inspection-common.conf" /etc/nginx/sites-available/

# Update domain name in config
sed -i "s/server_name vision.local localhost;/server_name $DOMAIN;/g" \
    /etc/nginx/sites-available/vision-inspection.conf

# Enable site
if [ ! -L /etc/nginx/sites-enabled/vision-inspection.conf ]; then
    ln -s /etc/nginx/sites-available/vision-inspection.conf \
        /etc/nginx/sites-enabled/vision-inspection.conf
    echo -e "${GREEN}✓ Site enabled${NC}"
fi

# Remove default site (optional)
if [ -L /etc/nginx/sites-enabled/default ]; then
    echo -e "${YELLOW}  Removing default site...${NC}"
    rm -f /etc/nginx/sites-enabled/default
fi

# ==================== TEST CONFIGURATION ====================

echo -e "${BLUE}→ Testing nginx configuration...${NC}"

if nginx -t 2>&1 | grep -q "successful"; then
    echo -e "${GREEN}✓ Nginx configuration is valid${NC}"
else
    echo -e "${RED}✗ Nginx configuration test failed${NC}"
    nginx -t
    exit 1
fi

# ==================== START/RELOAD NGINX ====================

echo -e "${BLUE}→ Starting/reloading nginx...${NC}"

if systemctl is-active --quiet nginx; then
    systemctl reload nginx
    echo -e "${GREEN}✓ Nginx reloaded${NC}"
else
    systemctl start nginx
    systemctl enable nginx
    echo -e "${GREEN}✓ Nginx started and enabled${NC}"
fi

# ==================== FIREWALL CONFIGURATION ====================

echo -e "${BLUE}→ Configuring firewall...${NC}"

if command -v ufw &> /dev/null; then
    # Enable UFW firewall
    ufw allow 'Nginx Full'
    ufw allow 'Nginx HTTP'
    ufw allow 'Nginx HTTPS'
    echo -e "${GREEN}✓ Firewall rules added${NC}"
else
    echo -e "${YELLOW}⊙ UFW not installed, skipping firewall configuration${NC}"
fi

# ==================== COMPLETION ====================

echo
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ✓ Nginx Installation Complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo
echo -e "${BLUE}Configuration Summary:${NC}"
echo -e "  Domain:      ${YELLOW}$DOMAIN${NC}"
echo -e "  SSL Type:    ${YELLOW}$SSL_TYPE${NC}"
echo -e "  Backend:     ${YELLOW}http://127.0.0.1:5000${NC}"
echo -e "  Frontend:    ${YELLOW}http://127.0.0.1:3000${NC}"
echo
echo -e "${BLUE}Access URLs:${NC}"
echo -e "  HTTP:        ${YELLOW}http://$DOMAIN${NC}"
echo -e "  HTTPS:       ${YELLOW}https://$DOMAIN${NC}"
echo
echo -e "${BLUE}Nginx Commands:${NC}"
echo -e "  Status:      ${YELLOW}sudo systemctl status nginx${NC}"
echo -e "  Reload:      ${YELLOW}sudo systemctl reload nginx${NC}"
echo -e "  Restart:     ${YELLOW}sudo systemctl restart nginx${NC}"
echo -e "  Logs:        ${YELLOW}sudo tail -f /var/log/nginx/vision-inspection-access.log${NC}"
echo
echo -e "${BLUE}Next Steps:${NC}"
echo -e "  1. Ensure backend is running:  ${YELLOW}cd $PROJECT_ROOT/backend && ./start_production.sh &${NC}"
echo -e "  2. Ensure frontend is running: ${YELLOW}cd $PROJECT_ROOT && npm run dev &${NC}"
echo -e "  3. Open application:           ${YELLOW}https://$DOMAIN${NC}"
echo
echo -e "${GREEN}✓ Setup complete!${NC}"
echo

