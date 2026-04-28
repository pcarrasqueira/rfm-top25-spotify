#!/usr/bin/env python3
"""
Script auxiliar para obter o SPOTIFY_REFRESH_TOKEN.

Uso:
  1. Cria uma app em https://developer.spotify.com/dashboard
  2. Adiciona http://localhost:8888/callback como Redirect URI
  3. Copia o Client ID e Client Secret
  4. Corre: python get_refresh_token.py
  5. Abre o URL mostrado no browser, autoriza e copia o `code` do URL de redirect
  6. Cola o code quando pedido
  7. O refresh token fica guardado em refresh_token.txt
"""

import os
import urllib.parse
import requests

CLIENT_ID = input("Client ID: ").strip()
CLIENT_SECRET = input("Client Secret: ").strip()
REDIRECT_URI = "http://localhost:8888/callback"
SCOPES = "playlist-modify-public playlist-modify-private"

auth_url = (
    "https://accounts.spotify.com/authorize?"
    + urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
    })
)

print(f"\nAbre este URL no browser:\n{auth_url}\n")
code = input("Cola o 'code' do URL de redirect: ").strip()

resp = requests.post(
    "https://accounts.spotify.com/api/token",
    data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    },
    auth=(CLIENT_ID, CLIENT_SECRET),
)
resp.raise_for_status()
data = resp.json()

print(f"\nAccess Token:  {data['access_token']}")
print(f"Refresh Token: {data['refresh_token']}")

with open("refresh_token.txt", "w") as f:
    f.write(data["refresh_token"])
print("\nRefresh token guardado em refresh_token.txt")
print("\nAdiciona os seguintes GitHub Secrets:")
print(f"  SPOTIFY_CLIENT_ID     = {CLIENT_ID}")
print(f"  SPOTIFY_CLIENT_SECRET = {CLIENT_SECRET}")
print(f"  SPOTIFY_REFRESH_TOKEN = {data['refresh_token']}")
