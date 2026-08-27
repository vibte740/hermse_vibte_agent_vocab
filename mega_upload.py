#!/usr/bin/env python3
"""
Standalone MEGA uploader using MEGA API directly.
No external dependencies beyond requests + pycryptodome.
"""
import hashlib
import json
import os
import re
import struct
import sys
import time
from base64 import b64encode, b64decode
from Cryptodome.Cipher import AES
import requests

API_URL = "https://g.api.mega.co.nz/cs"
DOWNLOAD_URL = "https://g.api.mega.co.nz/dl/"


def base64_to_a32(b):
    if isinstance(b, str):
        b = b.encode()
    return struct.unpack(">{}I".format(len(b) // 4), b)


def a32_to_base64(a):
    return b64encode(struct.pack(">{}I".format(len(a)), *a)).decode()


def str_to_a32(s):
    if isinstance(s, str):
        s = s.encode("latin-1")
    return struct.unpack(">{}I".format(len(s) // 4 * 4), s.ljust(len(s) // 4 * 4 + 4, b"\0")[:len(s) // 4 * 4 + 4])


def encrypt_key(a, key):
    return sum(
        [(a[i] ^ key[i % len(key)]) for i in range(len(a))],
        ()
    )


def decrypt_key(enc, key):
    return encrypt_key(enc, key)


def decrypt_attr(attr, key):
    aes = AES.new(a32_to_str(key), AES.MODE_ECB)
    attr = aes.decrypt(a32_to_str(attr))
    attr = attr.decode("utf-8", errors="replace")
    return json.loads(attr.split("\x00")[0])


def a32_to_str(a):
    return struct.pack(">{}I".format(len(a)), *a)


class MEGAUploader:
    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.session = requests.Session()
        self.root_id = None
        self.fsid = None
        self.sequence_number = 1
        self.sort_keys = {}
        self蛇_id = None
        self.s = None
        self.c = None
        self.k = None

    def api_request(self, data):
        params = {"id": self.fsid}
        if isinstance(data, list):
            data = json.dumps(data)
        resp = self.session.post(
            API_URL,
            params=params,
            data=data.encode(),
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def login(self):
        uh = hashlib.sha1(self.email.lower().encode()).hexdigest()[:8]
        rnd_aes_key = bytes([int.from_bytes(os.urandom(1), "big") ^ 42 for _ in range(16)])
        enc_key = AES.new(rnd_aes_key, AES.MODE_ECB).encrypt(self.password.encode("utf-8"))

        resp = self.session.post(
            "https://g.api.mega.co.nz/login",
            data={
                "log": self.email,
                "log_hash": b64encode(enc_key).decode(),
                "log_id": uh,
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()

        if isinstance(result, list):
            result = result[0]

        if "e" in result and result["e"] != 0:
            raise Exception(f"MEGA login failed: {result}")

        self.master_key = result.get("k", "")
        self.fsid = result.get("s", "")
        self.sequence_number = result.get("sn", 1)
        self.root_id = result.get("h", "")
        return True

    def create_folder(self, name):
        enc_name = AES.new(self.master_key[:16], AES.MODE_ECB).encrypt(
            name.encode("utf-8").ljust(16, b"\0")
        )
        return self.api_request([{
            "a": "p",
            "t": self.root_id,
            "n": [{"h": "!", "t": 1, "a": a32_to_base64(str_to_a32(enc_name))}],
        }])

    def upload(self, file_path, dest_dir="/Root"):
        filename = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)

        # Generate random keys
        file_key = bytes(int.from_bytes(os.urandom(4), "big") ^ int.from_bytes(os.urandom(4), "big") for _ in range(4))

        # Generate completion token
        upload_url = f"https://g.api.mega.co.nz/upload/0"

        with open(file_path, "rb") as f:
            file_data = f.read()

        # Split into chunks and encrypt
        chunk_size = 131072
        encrypted_chunks = []
        for i in range(0, len(file_data), chunk_size):
            chunk = file_data[i:i + chunk_size]
            padded = chunk.ljust(chunk_size + 16, b"\0")[:chunk_size + 16]
            aes = AES.new(a32_to_str(file_key), AES.MODE_CBC, iv=b"\0" * 16)
            encrypted_chunks.append(aes.encrypt(padded))

        # Upload via requests
        resp = self.session.post(
            upload_url,
            data=b"".join(encrypted_chunks),
            timeout=300,
        )
        resp.raise_for_status()
        completion = resp.json()

        # Create file node
        enc_file_name = AES.new(self.master_key[:16], AES.MODE_ECB).encrypt(
            filename.encode("utf-8").ljust(16, b"\0")
        )
        enc_file_key = encrypt_key(file_key, str_to_a32(self.master_key[:16]))

        node = {
            "a": "p",
            "t": self.root_id,
            "n": [{
                "h": completion,
                "t": 0,
                "s": file_size,
                "a": a32_to_base64(str_to_a32(str_to_a32(enc_file_name)[0:4])),
                "k": a32_to_base64(enc_file_key),
            }],
        }

        resp = self.api_request([node])
        if isinstance(resp, dict) and "e" in resp:
            raise Exception(f"Upload failed: {resp}")

        return completion


def mega_upload(file_path, email, password):
    uploader = MEGAUploader(email, password)
    uploader.login()
    result = uploader.upload(file_path)
    return f"https://mega.nz/file/{result}"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <file>")
        sys.exit(1)

    from dotenv import load_dotenv
    load_dotenv()

    file_path = sys.argv[1]
    email = os.environ.get("MEGA_EMAIL")
    password = os.environ.get("MEGA_PASSWORD")

    if not email or not password:
        print("MEGA_EMAIL and MEGA_PASSWORD must be set in .env")
        sys.exit(1)

    print(f"Uploading {file_path}...")
    link = mega_upload(file_path, email, password)
    print(f"Done! Link: {link}")
