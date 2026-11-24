#!/usr/bin/env python3
"""seal.py — 管理加密的參考資料站（PDF / 連結）

用法：
  python3 tools/seal.py add-pdf "path/to/file.pdf" --title "標題" [--note "備註"]
  python3 tools/seal.py add-link --title "名稱" --url "https://..." [--note "備註"]
  python3 tools/seal.py list
  python3 tools/seal.py rm <id>
  python3 tools/seal.py passwd            # 換密碼（重新加密全部內容）

密碼來源：環境變數 SD_PASSWORD，否則互動輸入。
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "data" / "vault.json"
CATALOG = ROOT / "data" / "catalog.sd"
FILES = ROOT / "files"

ITERATIONS = 310_000
MAGIC = b"SD1"
INFO = b"sdata-v1"


# ---------- crypto ----------

def root_key(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS, 32)


def sub_key(master: bytes, label: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=label.encode(), info=INFO
    ).derive(master)


def seal_bytes(master: bytes, label: str, data: bytes) -> bytes:
    iv = secrets.token_bytes(12)
    return MAGIC + iv + AESGCM(sub_key(master, label)).encrypt(iv, data, None)


def unseal_bytes(master: bytes, label: str, blob: bytes) -> bytes:
    if blob[:3] != MAGIC:
        sys.exit("檔案格式錯誤（不是本工具產生的加密檔）")
    iv, ct = blob[3:15], blob[15:]
    try:
        return AESGCM(sub_key(master, label)).decrypt(iv, ct, None)
    except Exception:
        sys.exit("解密失敗：密碼不正確或檔案損毀")


# ---------- vault / catalog ----------

def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_vault() -> dict | None:
    if VAULT.exists():
        return json.loads(VAULT.read_text(encoding="utf-8"))
    return None


def prompt_password(prompt: str = "密碼: ", confirm: bool = False) -> str:
    env = os.environ.get("SD_PASSWORD")
    if env:
        return env
    if not sys.stdin.isatty():
        sys.exit("非互動環境請用 SD_PASSWORD 環境變數提供密碼")
    pw = getpass.getpass(prompt)
    if confirm:
        if getpass.getpass("再輸入一次: ") != pw:
            sys.exit("兩次輸入不一致")
        if len(pw) < 10:
            sys.exit("密碼至少 10 個字元")
    return pw


def create_vault() -> tuple[dict, bytes]:
    print("首次使用，請設定一組密碼（至少 10 個字元）。")
    pw = prompt_password("設定密碼: ", confirm=True)
    salt = secrets.token_bytes(16)
    vault = {
        "v": 1,
        "kdf": "PBKDF2-SHA256",
        "iterations": ITERATIONS,
        "salt": base64.b64encode(salt).decode(),
        "catalog": "data/catalog.sd",
    }
    VAULT.parent.mkdir(parents=True, exist_ok=True)
    VAULT.write_text(json.dumps(vault, indent=2) + "\n", encoding="utf-8")
    master = root_key(pw, salt)
    save_catalog(master, {"version": 1, "updated": now(), "items": []})
    return vault, master


def open_vault() -> tuple[dict, bytes]:
    vault = read_vault()
    if vault is None:
        return create_vault()
    master = root_key(prompt_password(), base64.b64decode(vault["salt"]))
    load_catalog(master)  # 驗證密碼
    return vault, master


def load_catalog(master: bytes) -> dict:
    if not CATALOG.exists():
        return {"version": 1, "updated": now(), "items": []}
    return json.loads(unseal_bytes(master, "catalog", CATALOG.read_bytes()).decode())


def save_catalog(master: bytes, catalog: dict) -> None:
    catalog["updated"] = now()
    payload = json.dumps(catalog, ensure_ascii=False, indent=2).encode()
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    CATALOG.write_bytes(seal_bytes(master, "catalog", payload))


# ---------- helpers ----------

def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "item"


def unique_id(catalog: dict, base: str) -> str:
    taken = {i["id"] for i in catalog["items"]}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def find(catalog: dict, item_id: str) -> dict:
    for item in catalog["items"]:
        if item["id"] == item_id:
            return item
    sys.exit(f"找不到 id：{item_id}")


# ---------- commands ----------

def cmd_add_pdf(args) -> None:
    src = Path(args.path).expanduser()
    if not src.is_file():
        sys.exit(f"找不到檔案：{src}")
    if src.suffix.lower() != ".pdf":
        sys.exit("目前只支援 PDF")
    data = src.read_bytes()
    if not data.startswith(b"%PDF"):
        sys.exit("這不是有效的 PDF 檔")

    _, master = open_vault()
    catalog = load_catalog(master)
    title = args.title or src.stem
    item_id = unique_id(catalog, slug(args.title or src.stem))
    FILES.mkdir(parents=True, exist_ok=True)
    (FILES / f"{item_id}.sd").write_bytes(seal_bytes(master, f"file:{item_id}", data))

    catalog["items"].append({
        "type": "pdf",
        "id": item_id,
        "title": title,
        "note": args.note or "",
        "file": f"files/{item_id}.sd",
        "size": len(data),
        "added": now(),
    })
    save_catalog(master, catalog)
    print(f"✓ 已加入 PDF：{title}（id={item_id}，{len(data):,} bytes）")


def cmd_add_link(args) -> None:
    url = args.url.strip()
    if not re.match(r"^https?://", url):
        sys.exit("連結必須以 http:// 或 https:// 開頭")
    _, master = open_vault()
    catalog = load_catalog(master)
    item_id = unique_id(catalog, slug(args.title))
    catalog["items"].append({
        "type": "link",
        "id": item_id,
        "title": args.title,
        "url": url,
        "note": args.note or "",
        "added": now(),
    })
    save_catalog(master, catalog)
    print(f"✓ 已加入連結：{args.title}（id={item_id}）")


def cmd_list(args) -> None:
    _, master = open_vault()
    catalog = load_catalog(master)
    if not catalog["items"]:
        print("（目前沒有項目）")
        return
    for item in catalog["items"]:
        if item["type"] == "pdf":
            size = item.get("size", 0)
            print(f"[pdf ] {item['id']:<28} {item['title']}  ({size:,} bytes)")
        else:
            print(f"[link] {item['id']:<28} {item['title']}  {item['url']}")


def cmd_rm(args) -> None:
    _, master = open_vault()
    catalog = load_catalog(master)
    item = find(catalog, args.id)
    catalog["items"].remove(item)
    save_catalog(master, catalog)
    if item["type"] == "pdf":
        path = ROOT / item["file"]
        if path.exists():
            path.unlink()
    print(f"✓ 已移除：{item['title']}")


def cmd_passwd(args) -> None:
    vault, master = open_vault()
    catalog = load_catalog(master)
    payloads = {}
    for item in catalog["items"]:
        if item["type"] == "pdf":
            payloads[item["id"]] = unseal_bytes(master, f"file:{item['id']}", (ROOT / item["file"]).read_bytes())

    print("請設定新密碼。")
    new_pw = prompt_password("新密碼: ", confirm=True)
    new_salt = secrets.token_bytes(16)
    new_master = root_key(new_pw, new_salt)

    for item_id, data in payloads.items():
        (FILES / f"{item_id}.sd").write_bytes(seal_bytes(new_master, f"file:{item_id}", data))
    save_catalog(new_master, catalog)
    vault["salt"] = base64.b64encode(new_salt).decode()
    vault["iterations"] = ITERATIONS
    VAULT.write_text(json.dumps(vault, indent=2) + "\n", encoding="utf-8")
    print(f"✓ 密碼已更新，重新加密 {len(payloads)} 個檔案")


def main() -> None:
    parser = argparse.ArgumentParser(description="加密參考資料站的管理工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add-pdf", help="加入 PDF")
    p.add_argument("path")
    p.add_argument("--title")
    p.add_argument("--note")
    p.set_defaults(func=cmd_add_pdf)

    p = sub.add_parser("add-link", help="加入連結")
    p.add_argument("--title", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--note")
    p.set_defaults(func=cmd_add_link)

    p = sub.add_parser("list", help="列出所有項目")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("rm", help="移除項目")
    p.add_argument("id")
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser("passwd", help="變更密碼")
    p.set_defaults(func=cmd_passwd)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
