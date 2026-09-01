#!/bin/bash
#
# Helper script to set up GitHub Secrets for Droplet deployment
# Run this AFTER creating your droplet
#

set -e

echo "🔐 Setting up GitHub Secrets for Droplet Deployment"
echo ""

# Check if gh CLI is installed
if ! command -v gh &> /dev/null; then
    echo "❌ GitHub CLI (gh) is not installed."
    echo ""
    echo "Install it with:"
    echo "  brew install gh"
    echo ""
    echo "Then authenticate:"
    echo "  gh auth login"
    echo ""
    exit 1
fi

# Check if authenticated
if ! gh auth status &> /dev/null; then
    echo "❌ Not authenticated with GitHub."
    echo ""
    echo "Run: gh auth login"
    echo ""
    exit 1
fi

echo "This script will add three secrets to your GitHub repository:"
echo "  1. METASERVER_DROPLET_IP - Your droplet's IP address"
echo "  2. DROPLET_SSH_KEY - Your SSH private key for the droplet"
echo "  3. METASERVER_SSH_KNOWN_HOSTS - The pinned droplet SSH host key"
echo ""

# Get droplet IP
echo "📋 Step 1: Droplet IP Address"
echo ""
read -p "Enter your droplet IP address: " DROPLET_IP

if [[ ! $DROPLET_IP =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "❌ Invalid IP address format"
    exit 1
fi

echo "✅ IP: $DROPLET_IP"
echo ""

# Get SSH key
echo "📋 Step 2: SSH Private Key"
echo ""
echo "Choose your SSH key location:"
echo "  1. ~/.ssh/id_rsa (default)"
echo "  2. ~/.ssh/id_ed25519 (newer format)"
echo "  3. Custom path"
echo ""
read -p "Enter choice [1-3]: " KEY_CHOICE

case $KEY_CHOICE in
    1)
        SSH_KEY_PATH="$HOME/.ssh/id_rsa"
        ;;
    2)
        SSH_KEY_PATH="$HOME/.ssh/id_ed25519"
        ;;
    3)
        read -p "Enter full path to SSH private key: " SSH_KEY_PATH
        ;;
    *)
        echo "❌ Invalid choice"
        exit 1
        ;;
esac

if [[ ! -f "$SSH_KEY_PATH" ]]; then
    echo "❌ SSH key not found at: $SSH_KEY_PATH"
    exit 1
fi

echo "✅ Using SSH key: $SSH_KEY_PATH"
echo ""

# Capture and confirm the host key before the first SSH connection.
KNOWN_HOSTS_FILE=$(mktemp)
trap 'rm -f "$KNOWN_HOSTS_FILE"' EXIT
if ! ssh-keyscan -T 10 -H "$DROPLET_IP" > "$KNOWN_HOSTS_FILE" 2>/dev/null; then
    echo "Could not read the droplet SSH host key."
    exit 1
fi

echo "SSH host-key fingerprints:"
ssh-keygen -lf "$KNOWN_HOSTS_FILE"
echo "Compare these fingerprints with the droplet console before continuing."
read -p "Do the fingerprints match? (y/N): " HOST_KEY_CONFIRMED
if [[ ! $HOST_KEY_CONFIRMED =~ ^[Yy]$ ]]; then
    echo "Host key was not confirmed. No secrets were changed."
    exit 1
fi

# Test SSH connection
echo "🧪 Testing SSH connection to droplet..."
if ssh -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$KNOWN_HOSTS_FILE" \
    -o ConnectTimeout=5 -i "$SSH_KEY_PATH" root@$DROPLET_IP "echo 'Connection successful!'" &> /dev/null; then
    echo "✅ SSH connection successful!"
else
    echo "⚠️  Warning: Could not connect to droplet via SSH"
    read -p "Continue anyway? (y/N): " CONTINUE
    if [[ ! $CONTINUE =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi
echo ""

# Set GitHub secrets
echo "🔐 Adding secrets to GitHub repository..."
echo ""

# Add IP address
gh secret set METASERVER_DROPLET_IP --body "$DROPLET_IP"
echo "✅ Added: METASERVER_DROPLET_IP"

# Add SSH key
gh secret set DROPLET_SSH_KEY < "$SSH_KEY_PATH"
echo "✅ Added: DROPLET_SSH_KEY"

gh secret set METASERVER_SSH_KNOWN_HOSTS < "$KNOWN_HOSTS_FILE"
echo "✅ Added: METASERVER_SSH_KNOWN_HOSTS"

echo ""
echo "🎉 GitHub Secrets configured successfully!"
echo ""
echo "📝 What happens now:"
echo "  1. Any push to 'main' branch with metaserver changes will trigger auto-deploy"
echo "  2. The first secure run migrates deployment away from root"
echo "  3. Later runs publish staged website and metaserver releases"
echo ""
echo "🧪 Test it:"
echo "  1. Make a small change to metaserver/metaserver.php"
echo "  2. git commit -am 'Test auto-deploy'"
echo "  3. git push origin main"
echo "  4. Watch GitHub Actions: https://github.com/VR48/dunelegacy.com/actions"
echo ""

