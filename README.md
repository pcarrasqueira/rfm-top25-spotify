# Radios PT -> Spotify

[![RFM Top 25 -> Spotify](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/rfm-to-spotify.yml/badge.svg)](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/rfm-to-spotify.yml)
[![Ultimas no Ar -> Spotify](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/rfm-live-to-spotify.yml/badge.svg)](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/rfm-live-to-spotify.yml)
[![Comercial TNT Top 20 -> Spotify](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/comercial-to-spotify.yml/badge.svg)](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/comercial-to-spotify.yml)
[![Comercial Live -> Spotify](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/comercial-live-to-spotify.yml/badge.svg)](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/comercial-live-to-spotify.yml)

Quatro workflows que sincronizam conteudo de radios portuguesas com playlists Spotify.

| Workflow | Fonte | Playlist | Frequencia |
|---|---|---|---|
| **RFM Top 25** | rfm.pt/top25rfm | Os 25 mais tocados | Domingos 23:30 e Segundas 10:00 (Lisboa) |
| **Ultimas no Ar** | rfm.pt/que-musica-era | Rolling das ultimas 300 musicas | Hora a hora |
| **Comercial TNT Top 20** | radiocomercial.pt/programas/tnt | Os 20 mais votados | Domingos 23:30 e Segundas 10:00 (Lisboa) |
| **Comercial Live** | radiocomercial.pt/passou | Rolling das ultimas 300 musicas | Hora a hora |

---

## Setup

### 1. Criar app Spotify

1. Vai a [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Cria uma nova app
3. Adiciona `http://127.0.0.1:8888/callback` como **Redirect URI**
4. Em **User Management**, adiciona o email da tua conta Spotify
5. Copia o **Client ID** e **Client Secret**

> **Nota**: Em Development Mode a Spotify exige que a conta esteja em User Management. Sem isso, todos os pedidos de escrita dao 403.

### 2. Obter o Refresh Token

```bash
pip install requests
python get_refresh_token.py
```

O script abre um URL no browser para autorizacao. Confirma que o URL contem:
```
scope=playlist-modify-public+playlist-modify-private
```

No final imprime o Refresh Token -- copia-o para o passo seguinte.

### 3. Criar as playlists no Spotify

Cria as playlists vazias no Spotify (publicas ou privadas, a tua escolha):
- **RFM Top 25** -- substituida 2x por semana
- **Ultimas no Ar** -- rolling das ultimas 300 musicas tocadas na RFM
- **Comercial TNT Top 20** -- substituida 2x por semana
- **Comercial Live** -- rolling das ultimas 300 musicas tocadas na Comercial

O ID de cada playlist esta no URL: `https://open.spotify.com/playlist/**ID_AQUI**`

### 4. Adicionar GitHub Secrets

Em **Settings -> Secrets and variables -> Actions** adiciona:

| Secret | Valor |
|---|---|
| `SPOTIFY_CLIENT_ID` | Client ID da app Spotify |
| `SPOTIFY_CLIENT_SECRET` | Client Secret da app Spotify |
| `SPOTIFY_REFRESH_TOKEN` | Refresh token obtido no passo 2 |
| `SPOTIFY_PLAYLIST_ID` | ID da playlist RFM Top 25 |
| `SPOTIFY_LIVE_PLAYLIST_ID` | ID da playlist Ultimas no Ar (RFM) |
| `SPOTIFY_COMERCIAL_PLAYLIST_ID` | ID da playlist Comercial TNT Top 20 |
| `SPOTIFY_COMERCIAL_LIVE_PLAYLIST_ID` | ID da playlist Comercial Live |

### 5. Testar

Vai a **Actions** e corre cada workflow manualmente com **Run workflow**.

Os logs e o job summary mostram cada track encontrado ou nao encontrado.

---

## Correr localmente

```bash
pip install -r requirements.txt

export SPOTIFY_CLIENT_ID=xxx
export SPOTIFY_CLIENT_SECRET=xxx
export SPOTIFY_REFRESH_TOKEN=xxx

# RFM Top 25
export SPOTIFY_PLAYLIST_ID=xxx
python rfm_to_spotify.py

# Ultimas no Ar (RFM)
export SPOTIFY_LIVE_PLAYLIST_ID=xxx
python rfm_live_to_spotify.py

# Comercial TNT Top 20
export SPOTIFY_COMERCIAL_PLAYLIST_ID=xxx
python comercial_to_spotify.py

# Comercial Live
export SPOTIFY_COMERCIAL_LIVE_PLAYLIST_ID=xxx
python comercial_live_to_spotify.py
```

---

## Notas tecnicas

- **API Spotify (2026)**: os endpoints `/playlists/{id}/tracks` foram removidos e substituidos por `/playlists/{id}/items`. Todos os scripts usam os endpoints novos.
- **Scraping RFM**: ambas as paginas (`/top25rfm` e `/que-musica-era`) devolvem HTML estatico.
- **Scraping Comercial TNT**: a pagina `/programas/tnt-todos-no-top` devolve HTML estatico com o top semanal.
- **Scraping Comercial Live**: a pagina `/passou` e renderizada via JS; o scraper extrai texto puro e identifica grupos hora+titulo+artista por regex.
- **Refresh Token**: nao expira, a nao ser que revogues o acesso em [spotify.com/account/apps](https://www.spotify.com/account/apps).
- **GitHub Actions (plano gratuito)**: 2000 min/mes. Os dois workflows hora-a-hora usam ~720 min/mes, dentro do limite.

---

## Estrutura

```
.github/workflows/
  rfm-to-spotify.yml               # RFM Top 25 -- Dom 22:30 UTC + Seg 09:00 UTC
  rfm-live-to-spotify.yml          # Ultimas no Ar (RFM) -- hora a hora
  comercial-to-spotify.yml         # Comercial TNT Top 20 -- Dom 22:30 UTC + Seg 09:00 UTC
  comercial-live-to-spotify.yml    # Comercial Live -- hora a hora
rfm_to_spotify.py                  # script RFM Top 25
rfm_live_to_spotify.py             # script Ultimas no Ar
comercial_to_spotify.py            # script Comercial TNT Top 20
comercial_live_to_spotify.py       # script Comercial Live
get_refresh_token.py               # helper para gerar o refresh token
requirements.txt                   # dependencias Python
```
