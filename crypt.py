#!/usr/bin/env python3
"""Symmetric encrypt/decrypt so the public repo only ever holds ciphertext.
Key = sha256(ENC_KEY env). Usage: python crypt.py enc|dec INFILE OUTFILE"""
import sys, os, hashlib
from nacl.secret import SecretBox

key = hashlib.sha256(os.environ["ENC_KEY"].encode()).digest()
box = SecretBox(key)
mode, inf, outf = sys.argv[1], sys.argv[2], sys.argv[3]
data = open(inf, "rb").read()
out = box.encrypt(data) if mode == "enc" else box.decrypt(data)
open(outf, "wb").write(out)
print(f"{mode}: {inf} -> {outf} ({len(data)}->{len(out)} bytes)")
