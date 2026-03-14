#!/bin/bash
# Post-install script for Architect on Linux

# Create desktop entry
DESKTOP_FILE="/usr/share/applications/architect.desktop"
if [ ! -f "$DESKTOP_FILE" ]; then
    cat > "$DESKTOP_FILE" << 'EOF'
[Desktop Entry]
Name=Architect
Comment=Architecture visualization IDE powered by Cursor AI
Exec=/opt/Architect/architect %U
Icon=architect
Type=Application
Categories=Development;IDE;
StartupWMClass=architect
MimeType=application/x-architect;
EOF
fi

# Update desktop database
if command -v update-desktop-database &> /dev/null; then
    update-desktop-database /usr/share/applications 2>/dev/null || true
fi

# Create symlink for CLI access
ln -sf /opt/Architect/architect /usr/local/bin/architect-ide 2>/dev/null || true

echo "Architect installed successfully."
echo ""
echo "Note: For AI features, install Cursor from https://cursor.com"
