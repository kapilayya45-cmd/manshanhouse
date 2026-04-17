# Setting Up an Australian Proxy on Oracle Cloud Free Tier

This guide walks you through setting up a lightweight proxy server in Oracle Cloud's Sydney region to bypass geo-blocking for Sportsbet scraping.

## Step 1: Create Oracle Cloud Account

1. Go to [Oracle Cloud Free Tier Signup](https://signup.oraclecloud.com/)
2. Choose **Australia Southeast (Sydney)** as your home region - this is critical!
3. Complete signup with your details
4. You'll need a credit card for verification, but won't be charged for Always Free resources

> **Note**: Oracle is strict about duplicate accounts. Use your real information.

## Step 2: Create a VM Instance

1. Log into [Oracle Cloud Console](https://cloud.oracle.com/)
2. Click **Create a VM instance** (or go to Compute → Instances → Create Instance)

3. Configure the instance:
   - **Name**: `au-proxy` (or whatever you like)
   - **Placement**: Should default to Sydney
   - **Image**: Oracle Linux 8 (default) or Ubuntu 22.04
   - **Shape**: Click "Change Shape"
     - Select **Ampere** (ARM-based)
     - Choose **VM.Standard.A1.Flex**
     - Set to **1 OCPU** and **6 GB RAM** (well within free tier)

4. **Networking**:
   - Create new VCN or use default
   - Ensure "Assign public IPv4 address" is selected

5. **SSH Keys**:
   - Choose "Generate a key pair for me"
   - **Download both keys** (private and public) - you'll need these!
   - Or paste your own public key if you have one

6. Click **Create**

7. Wait for instance to show "Running" (takes 1-2 minutes)

8. Note the **Public IP Address** - you'll need this!

## Step 3: Open Firewall Port

Oracle has two firewalls - the instance's iptables AND the VCN security list.

### A) Open VCN Security List (Oracle's firewall)

1. Click on your instance name
2. Click on the **Subnet** link (under "Primary VNIC")
3. Click on the **Security List** (usually "Default Security List for...")
4. Click **Add Ingress Rules**
5. Add this rule:
   - **Source CIDR**: `0.0.0.0/0`
   - **Destination Port Range**: `8080`
   - **Description**: Proxy server
6. Click "Add Ingress Rules"

### B) Open OS Firewall (after SSH'ing in - see Step 5)

## Step 4: Connect via SSH

```bash
# Make your private key readable only by you
chmod 600 ~/Downloads/ssh-key-*.key

# Connect to your instance
ssh -i ~/Downloads/ssh-key-*.key opc@YOUR_PUBLIC_IP

# If using Ubuntu image, use 'ubuntu' instead of 'opc':
ssh -i ~/Downloads/ssh-key-*.key ubuntu@YOUR_PUBLIC_IP
```

## Step 5: Set Up the Proxy Server

Once connected via SSH, run these commands:

```bash
# Update system
sudo dnf update -y   # Oracle Linux
# OR
sudo apt update && sudo apt upgrade -y   # Ubuntu

# Install Python and pip
sudo dnf install python3 python3-pip -y   # Oracle Linux
# OR
sudo apt install python3 python3-pip -y   # Ubuntu

# Install Flask and requests
sudo pip3 install flask requests gunicorn

# Open firewall port (OS-level)
sudo firewall-cmd --permanent --add-port=8080/tcp
sudo firewall-cmd --reload
# OR for Ubuntu:
sudo ufw allow 8080/tcp

# Create the proxy app
mkdir ~/proxy && cd ~/proxy

cat > app.py << 'EOF'
from flask import Flask, request, Response
import requests
import os

app = Flask(__name__)

# Simple auth token (set via environment variable)
AUTH_TOKEN = os.environ.get('PROXY_TOKEN', 'change-me-in-production')

@app.route('/health')
def health():
    return 'OK', 200

@app.route('/proxy')
def proxy():
    # Check auth
    token = request.args.get('token') or request.headers.get('Authorization', '').replace('Bearer ', '')
    if token != AUTH_TOKEN:
        return {'error': 'Unauthorized'}, 401

    # Get URL to proxy
    url = request.args.get('url')
    if not url:
        return {'error': 'Missing url parameter'}, 400

    # Only allow sportsbet URLs
    if 'sportsbet.com.au' not in url:
        return {'error': 'Only sportsbet.com.au URLs allowed'}, 403

    try:
        resp = requests.get(url, headers={
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }, timeout=15)

        return Response(resp.text, status=resp.status_code, mimetype='text/html')
    except Exception as e:
        return {'error': str(e)}, 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
EOF

# Create systemd service for auto-start
sudo cat > /etc/systemd/system/proxy.service << 'EOF'
[Unit]
Description=Australian Proxy Server
After=network.target

[Service]
User=opc
WorkingDirectory=/home/opc/proxy
Environment="PROXY_TOKEN=your-secret-token-here"
ExecStart=/usr/local/bin/gunicorn -w 2 -b 0.0.0.0:8080 app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# IMPORTANT: Edit the service file to set your secret token!
sudo nano /etc/systemd/system/proxy.service
# Change "your-secret-token-here" to a random string

# Start the service
sudo systemctl daemon-reload
sudo systemctl enable proxy
sudo systemctl start proxy

# Check it's running
sudo systemctl status proxy
```

## Step 6: Test the Proxy

From your local machine:

```bash
# Test health endpoint (no auth needed)
curl http://YOUR_PUBLIC_IP:8080/health

# Test proxy (replace YOUR_TOKEN)
curl "http://YOUR_PUBLIC_IP:8080/proxy?token=YOUR_TOKEN&url=https://www.sportsbet.com.au/betting/cricket/test-matches"
```

## Step 7: Update Your Scraper

Add the proxy URL to your environment variables on Render:

```
SPORTSBET_PROXY_URL=http://YOUR_PUBLIC_IP:8080/proxy
SPORTSBET_PROXY_TOKEN=your-secret-token-here
```

Then update `scraper.py` to use the proxy (see the code changes below).

## Troubleshooting

### Can't connect to port 8080
1. Check VCN Security List has the ingress rule
2. Check OS firewall: `sudo firewall-cmd --list-all`
3. Check service is running: `sudo systemctl status proxy`
4. Check logs: `sudo journalctl -u proxy -f`

### Instance won't create (out of capacity)
Sydney sometimes runs out of Always Free capacity. Try:
- Different availability domain (AD1, AD2, AD3)
- Try again later (often frees up overnight)
- Use the smaller AMD shape instead

### SSH connection refused
- Wait a minute for instance to fully boot
- Verify you're using the correct username (`opc` for Oracle Linux, `ubuntu` for Ubuntu)
- Check your private key permissions: `chmod 600 your-key.key`
