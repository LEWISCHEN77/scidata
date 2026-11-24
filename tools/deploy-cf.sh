#!/usr/bin/env bash
# 把這個資料夾部署到 Cloudflare Pages，並確保 Cloudflare Access（Email 驗證）已套用。
# Token 來源：環境變數 CLOUDFLARE_API_TOKEN 或 ~/.cf-scidata-token
set -euo pipefail
cd "$(dirname "$0")/.."

API="https://api.cloudflare.com/client/v4"
ACCOUNT="${CF_ACCOUNT_ID:-4482d3c66db64ea23602c8a2d6437fd4}"
PROJECT="${CF_PROJECT:-scidata-refs}"
EMAIL="${ACCESS_EMAIL:-freud7788@gmail.com}"
TEAM="${CF_TEAM:-mzjh-scidata}"

TOKEN="${CLOUDFLARE_API_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -f "$HOME/.cf-scidata-token" ]; then
  TOKEN="$(tr -d ' \t\r\n' < "$HOME/.cf-scidata-token")"
fi
if [ -z "$TOKEN" ]; then
  echo "✗ 找不到 token：請設定 CLOUDFLARE_API_TOKEN 或建立 ~/.cf-scidata-token" >&2
  exit 1
fi

cf() { # cf METHOD PATH [DATA]
  local method="$1" path="$2" data="${3:-}"
  if [ -n "$data" ]; then
    curl -sS --fail-with-body -X "$method" "$API$path" \
      -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
      --data "$data"
  else
    curl -sS --fail-with-body -X "$method" "$API$path" \
      -H "Authorization: Bearer $TOKEN"
  fi
}

jget() { python3 -c 'import json,sys;d=json.load(sys.stdin);
for k in sys.argv[1].split("."): d = d[int(k)] if k.isdigit() else (d or {}).get(k)
print("" if d is None else d)' "$1"; }

echo "→ 檢查 Pages 專案 $PROJECT"
if cf GET "/accounts/$ACCOUNT/pages/projects/$PROJECT" >/dev/null 2>&1; then
  echo "  已存在"
else
  cf POST "/accounts/$ACCOUNT/pages/projects" \
    "{\"name\":\"$PROJECT\",\"production_branch\":\"main\"}" >/dev/null
  echo "  已建立"
fi

echo "→ 收集檔案"
FILES=()
while IFS= read -r f; do
  case "$f" in
    README.md|.gitignore|.nojekyll|tools/*) continue ;;
  esac
  FILES+=("$f")
done < <(git ls-files)

MANIFEST="$(python3 - "${FILES[@]}" <<'PY'
import hashlib, json, sys
m = {}
for path in sys.argv[1:]:
    with open(path, "rb") as fh:
        m[path] = hashlib.sha256(fh.read()).hexdigest()
print(json.dumps(m, separators=(",", ":")))
PY
)"

echo "→ 上傳 ${#FILES[@]} 個檔案"
FORM=(-F "branch=main" -F "commit_message=deploy $(date '+%Y-%m-%d %H:%M')" -F "manifest=$MANIFEST")
for f in "${FILES[@]}"; do FORM+=(-F "$f=@$f"); done

DEPLOY="$(curl -sS --fail-with-body -X POST \
  "$API/accounts/$ACCOUNT/pages/projects/$PROJECT/deployments" \
  -H "Authorization: Bearer $TOKEN" "${FORM[@]}")"

URL="$(printf '%s' "$DEPLOY" | jget result.url)"
echo "  部署完成：$URL"

echo "→ 檢查零信任組織"
ORG="$(cf GET "/accounts/$ACCOUNT/access/organizations" 2>/dev/null || true)"
if [ -z "$ORG" ] || ! printf '%s' "$ORG" | grep -q '"auth_domain"'; then
  cf POST "/accounts/$ACCOUNT/access/organizations" \
    "{\"name\":\"SCIDATA\",\"auth_domain\":\"$TEAM\",\"session_duration\":\"24h\"}" >/dev/null
  echo "  已建立（team: $TEAM.cloudflareaccess.com）"
else
  TEAM="$(printf '%s' "$ORG" | jget result.auth_domain | sed 's/\.cloudflareaccess\.com$//')"
  echo "  已存在（team: $TEAM.cloudflareaccess.com）"
fi

HOST="$PROJECT.pages.dev"
echo "→ 檢查 Access 應用 $HOST"
APPS="$(cf GET "/accounts/$ACCOUNT/access/apps?per_page=100")"
APP_ID="$(printf '%s' "$APPS" | python3 -c '
import json,sys
host=sys.argv[1]
for a in json.load(sys.stdin)["result"]:
    if a.get("domain")==host: print(a["id"]); break
' "$HOST")"

if [ -z "$APP_ID" ]; then
  CREATED="$(cf POST "/accounts/$ACCOUNT/access/apps" \
    "{\"name\":\"參考資料（$PROJECT）\",\"domain\":\"$HOST\",\"type\":\"self_hosted\",\"session_duration\":\"24h\",\"app_launcher_visible\":false,\"auto_redirect_to_identity\":true}")"
  APP_ID="$(printf '%s' "$CREATED" | jget result.id)"
  echo "  已建立應用 $APP_ID"
else
  echo "  已存在應用 $APP_ID"
fi

echo "→ 確保 $EMAIL 在允許名單"
POLICIES="$(cf GET "/accounts/$ACCOUNT/access/apps/$APP_ID/policies?per_page=100")"
HAS="$(printf '%s' "$POLICIES" | python3 -c '
import json,sys
email=sys.argv[1]
for p in json.load(sys.stdin).get("result") or []:
    for inc in p.get("include") or []:
        if (inc.get("email") or {}).get("email")==email: print("yes"); break
' "$EMAIL")"

if [ "$HAS" = "yes" ]; then
  echo "  已有規則"
else
  cf POST "/accounts/$ACCOUNT/access/apps/$APP_ID/policies" \
    "{\"name\":\"owner\",\"decision\":\"allow\",\"include\":[{\"email\":{\"email\":\"$EMAIL\"}}]}" >/dev/null
  echo "  已加入"
fi

echo
echo "完成。"
echo "  網站：https://$HOST"
echo "  登入：https://$TEAM.cloudflareaccess.com  （Email 一次性驗證碼）"
