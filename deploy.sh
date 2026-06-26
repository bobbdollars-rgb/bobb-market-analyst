#!/usr/bin/env bash
# =============================================================
# deploy.sh — Bobb Market Analyst v3.0 Auto Deploy
# Jalanin sekali: bash deploy.sh
# =============================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET} $1"; }
success() { echo -e "${GREEN}[OK]${RESET} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET} $1"; }
error()   { echo -e "${RED}[ERROR]${RESET} $1"; exit 1; }
step()    { echo -e "\n${BOLD}$1${RESET}"; }

echo -e "${BOLD}"
echo "╔══════════════════════════════════════════════════╗"
echo "║     BOBB MARKET ANALYST v3.0 — Auto Deploy      ║"
echo "╚══════════════════════════════════════════════════╝"
echo -e "${RESET}"

# ════════════════════════════════════════════════════════
# STEP 1 — Prerequisites
# ════════════════════════════════════════════════════════
step "[ 1/6 ] Checking prerequisites..."

command -v git     >/dev/null 2>&1 || error "git tidak ditemukan. Install: sudo apt install git"
command -v python3 >/dev/null 2>&1 || error "python3 tidak ditemukan."
command -v curl    >/dev/null 2>&1 || error "curl tidak ditemukan. Install: sudo apt install curl"

success "git: $(git --version)"
success "python: $(python3 --version)"

# ════════════════════════════════════════════════════════
# STEP 2 — Locate engine files
# ════════════════════════════════════════════════════════
step "[ 2/6 ] Locating engine files..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$SCRIPT_DIR/signal_engine.py" ]; then
    ENGINES_DIR="$SCRIPT_DIR"
elif [ -d "$SCRIPT_DIR/bobb_engines" ] && [ -f "$SCRIPT_DIR/bobb_engines/signal_engine.py" ]; then
    ENGINES_DIR="$SCRIPT_DIR/bobb_engines"
else
    error "Tidak ketemu signal_engine.py. Pastikan deploy.sh ada di folder yang sama dengan file .py"
fi
success "Engine files: $ENGINES_DIR"

REQUIRED_FILES=(
    signal_engine.py data_fetch.py ict_engine.py elliott_wave.py
    harmonic_patterns.py chart_patterns.py divergence.py astronacci.py
    killzone.py confluence_score.py telegram_formatter.py
    news_detector.py requirements.txt
)
for f in "${REQUIRED_FILES[@]}"; do
    [ -f "$ENGINES_DIR/$f" ] || error "File tidak ditemukan: $f"
done
success "Semua ${#REQUIRED_FILES[@]} engine files ketemu"

# ════════════════════════════════════════════════════════
# STEP 3 — Collect inputs
# ════════════════════════════════════════════════════════
step "[ 3/6 ] Setup GitHub repository..."

echo ""
read -p "  GitHub username kamu                  : " GH_USER
[ -z "$GH_USER" ] && error "Username tidak boleh kosong."

read -p "  Nama repo baru (e.g. bobb-signal-bot) : " REPO_NAME
REPO_NAME="${REPO_NAME:-bobb-signal-bot}"

echo ""
echo -e "  ${YELLOW}GitHub Personal Access Token diperlukan untuk push.${RESET}"
echo    "  Cara buat:"
echo    "    github.com → Settings → Developer settings"
echo    "    → Personal access tokens → Tokens (classic)"
echo    "    → Generate new token → centang: repo (full control)"
echo ""
read -s -p "  GitHub Token (hidden) : " GH_TOKEN
echo ""
[ -z "$GH_TOKEN" ] && error "Token tidak boleh kosong."

read -p "  Email git (untuk commit) : " GIT_EMAIL
[ -z "$GIT_EMAIL" ] && error "Email tidak boleh kosong."

echo ""
info "Repo yang akan dibuat: https://github.com/$GH_USER/$REPO_NAME (private)"
read -p "  Lanjut? (y/N): " CONFIRM
[[ "$CONFIRM" =~ ^[Yy]$ ]] || { info "Dibatalkan."; exit 0; }

# ════════════════════════════════════════════════════════
# STEP 4 — Create GitHub repo via API
# ════════════════════════════════════════════════════════
step "[ 4/6 ] Creating GitHub repository..."

HTTP_CODE=$(curl -s -o /tmp/gh_create_resp.json -w "%{http_code}" \
    -X POST \
    -H "Authorization: token $GH_TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    https://api.github.com/user/repos \
    -d "{\"name\":\"$REPO_NAME\",\"private\":true,\"description\":\"Bobb Market Analyst v3.0 Signal Bot\"}")

if [ "$HTTP_CODE" = "201" ]; then
    success "Repo '$REPO_NAME' berhasil dibuat (private)"
elif [ "$HTTP_CODE" = "422" ]; then
    warn "Repo '$REPO_NAME' sudah ada — akan pakai yang existing"
else
    cat /tmp/gh_create_resp.json
    error "Gagal buat repo. HTTP $HTTP_CODE — cek token & pastikan scope 'repo' dicentang"
fi

# ════════════════════════════════════════════════════════
# STEP 5 — Init local repo & push
# ════════════════════════════════════════════════════════
step "[ 5/6 ] Initializing local repo and pushing..."

DEPLOY_DIR="/tmp/bobb_deploy_$$"
mkdir -p "$DEPLOY_DIR"
info "Working dir: $DEPLOY_DIR"

# Copy engine files
cp "$ENGINES_DIR"/*.py      "$DEPLOY_DIR/"
cp "$ENGINES_DIR/requirements.txt" "$DEPLOY_DIR/"
[ -f "$ENGINES_DIR/README.md" ] && cp "$ENGINES_DIR/README.md" "$DEPLOY_DIR/"

# Copy or generate workflows
mkdir -p "$DEPLOY_DIR/.github/workflows"
if [ -d "$ENGINES_DIR/.github/workflows" ]; then
    cp "$ENGINES_DIR/.github/workflows/"*.yml "$DEPLOY_DIR/.github/workflows/"
    success "Workflows copied from repo"
else
    warn "Workflow dir tidak ketemu — generating inline..."
    cat > "$DEPLOY_DIR/.github/workflows/bobb_signal_engine.yml" << 'WFEOF'
name: Bobb Signal Engine (M5)
on:
  schedule:
    - cron: "*/5 * * * *"
  workflow_dispatch:
concurrency:
  group: signal-engine
  cancel-in-progress: true
jobs:
  run-signal:
    runs-on: ubuntu-latest
    timeout-minutes: 8
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: pip install requests pandas numpy
      - env:
          TWELVEDATA_API_KEY: ${{ secrets.TWELVEDATA_API_KEY }}
          BOT_TOKEN: ${{ secrets.BOT_TOKEN }}
          CHAT_ID: ${{ secrets.CHAT_ID }}
        run: python signal_engine.py
WFEOF

    cat > "$DEPLOY_DIR/.github/workflows/bobb_news_detector.yml" << 'WFEOF'
name: Bobb News Detector (1min)
on:
  schedule:
    - cron: "* * * * *"
  workflow_dispatch:
concurrency:
  group: news-detector
  cancel-in-progress: true
jobs:
  run-news:
    runs-on: ubuntu-latest
    timeout-minutes: 3
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: pip install requests
      - env:
          BOT_TOKEN: ${{ secrets.BOT_TOKEN }}
          CHAT_ID: ${{ secrets.CHAT_ID }}
          BOBB_NEWSAPI_KEY: ${{ secrets.BOBB_NEWSAPI_KEY }}
        run: python news_detector.py
WFEOF
fi

# Git init & push
cd "$DEPLOY_DIR"
git init -b main 2>/dev/null || git init && git checkout -b main 2>/dev/null || true
git config user.name  "$GH_USER"
git config user.email "$GIT_EMAIL"
git add .
git commit -m "init: bobb market analyst v3.0"

REMOTE_URL="https://$GH_TOKEN@github.com/$GH_USER/$REPO_NAME.git"
git remote add origin "$REMOTE_URL"
git push -u origin main --force

success "Push berhasil!"

# Cleanup (hapus token dari working dir)
cd /
rm -rf "$DEPLOY_DIR"

# ════════════════════════════════════════════════════════
# STEP 6 — Manual steps reminder
# ════════════════════════════════════════════════════════
step "[ 6/6 ] Done! Tinggal 2 langkah manual..."

echo ""
echo -e "${BOLD}════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}  DEPLOY SELESAI! Tinggal 2 langkah manual:${RESET}"
echo -e "${BOLD}════════════════════════════════════════════════════${RESET}"
echo ""
echo -e "  ${BOLD}A. Set GitHub Secrets${RESET}"
echo    "     Buka di browser:"
echo -e "     ${CYAN}https://github.com/$GH_USER/$REPO_NAME/settings/secrets/actions${RESET}"
echo ""
echo    "     Tambahkan secrets berikut:"
echo -e "     ${YELLOW}BOT_TOKEN${RESET}           → Token Telegram bot BARU (revoke lama dulu di @BotFather!)"
echo -e "     ${YELLOW}CHAT_ID${RESET}             → Chat ID Telegram lo"
echo -e "     ${YELLOW}TWELVEDATA_API_KEY${RESET}  → API key dari twelvedata.com"
echo -e "     ${YELLOW}BOBB_NEWSAPI_KEY${RESET}    → (Opsional) key dari newsapi.org"
echo ""
echo -e "  ${BOLD}B. Enable GitHub Actions${RESET}"
echo    "     Buka di browser:"
echo -e "     ${CYAN}https://github.com/$GH_USER/$REPO_NAME/actions${RESET}"
echo    "     Klik: 'I understand my workflows, go ahead and enable them'"
echo ""
echo -e "${BOLD}════════════════════════════════════════════════════${RESET}"
echo -e "  ${GREEN}Setelah itu bot langsung jalan otomatis!${RESET}"
echo -e "  Signal engine tiap 5 menit, news detector tiap menit."
echo -e "${BOLD}════════════════════════════════════════════════════${RESET}"
echo ""
