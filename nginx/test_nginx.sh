#!/bin/bash

################################################################################
# Vision Inspection System - Nginx Test Script
################################################################################
#
# This script tests the nginx configuration and verifies all components
# are working correctly.
#
# Usage:
#   ./test_nginx.sh
#
################################################################################

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Vision Inspection System - Nginx Test${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo

# Test 1: Nginx installed
echo -n "1. Checking if nginx is installed... "
if command -v nginx &> /dev/null; then
    NGINX_VERSION=$(nginx -v 2>&1 | cut -d'/' -f2)
    echo -e "${GREEN}✓ Nginx $NGINX_VERSION${NC}"
else
    echo -e "${RED}✗ Not installed${NC}"
    echo -e "${YELLOW}  Run: sudo ./install_nginx.sh${NC}"
    exit 1
fi

# Test 2: Nginx running
echo -n "2. Checking if nginx is running... "
if sudo systemctl is-active --quiet nginx; then
    echo -e "${GREEN}✓ Running${NC}"
else
    echo -e "${RED}✗ Not running${NC}"
    echo -e "${YELLOW}  Run: sudo systemctl start nginx${NC}"
    exit 1
fi

# Test 3: Configuration valid
echo -n "3. Testing nginx configuration... "
if sudo nginx -t 2>&1 | grep -q "successful"; then
    echo -e "${GREEN}✓ Valid${NC}"
else
    echo -e "${RED}✗ Invalid${NC}"
    sudo nginx -t
    exit 1
fi

# Test 4: Backend running
echo -n "4. Checking backend (port 5000)... "
if curl -s http://localhost:5000/ > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Running${NC}"
else
    echo -e "${RED}✗ Not running${NC}"
    echo -e "${YELLOW}  Start backend: cd backend && ./start_production.sh &${NC}"
fi

# Test 5: Frontend running
echo -n "5. Checking frontend (port 3000)... "
if curl -s http://localhost:3000/ > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Running${NC}"
else
    echo -e "${RED}✗ Not running${NC}"
    echo -e "${YELLOW}  Start frontend: npm run dev &${NC}"
fi

# Test 6: Nginx port 80
echo -n "6. Testing HTTP (port 80)... "
if curl -s http://localhost/ > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Accessible${NC}"
else
    echo -e "${RED}✗ Not accessible${NC}"
fi

# Test 7: Nginx port 443
echo -n "7. Testing HTTPS (port 443)... "
if curl -k -s https://localhost/ > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Accessible${NC}"
else
    echo -e "${RED}✗ Not accessible${NC}"
fi

# Test 8: API proxy
echo -n "8. Testing API proxy (/api)... "
RESPONSE=$(curl -s http://localhost/api/programs 2>&1)
if echo "$RESPONSE" | grep -q "programs"; then
    echo -e "${GREEN}✓ Working${NC}"
else
    echo -e "${YELLOW}⚠ Check backend${NC}"
fi

# Test 9: Frontend proxy
echo -n "9. Testing frontend proxy (/)... "
if curl -s http://localhost/ | grep -q "<!DOCTYPE html>"; then
    echo -e "${GREEN}✓ Working${NC}"
else
    echo -e "${YELLOW}⚠ Check frontend${NC}"
fi

# Test 10: Health check
echo -n "10. Testing health endpoint... "
if curl -s http://localhost/nginx-health | grep -q "healthy"; then
    echo -e "${GREEN}✓ Healthy${NC}"
else
    echo -e "${YELLOW}⚠ Check config${NC}"
fi

echo
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Test Complete!${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo
echo -e "${BLUE}Access URLs:${NC}"
echo -e "  HTTP:   ${YELLOW}http://localhost/${NC}"
echo -e "  HTTPS:  ${YELLOW}https://localhost/${NC} ${YELLOW}(may show security warning for self-signed cert)${NC}"
echo
echo -e "${BLUE}Nginx Management:${NC}"
echo -e "  Status:  ${YELLOW}sudo systemctl status nginx${NC}"
echo -e "  Reload:  ${YELLOW}sudo systemctl reload nginx${NC}"
echo -e "  Logs:    ${YELLOW}sudo tail -f /var/log/nginx/vision-inspection-access.log${NC}"
echo

