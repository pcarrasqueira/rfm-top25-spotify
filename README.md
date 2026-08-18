# Radios PT -> Spotify

[![RFM Top 25 -> Spotify](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/rfm-to-spotify.yml/badge.svg)](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/rfm-to-spotify.yml)
[![Ultimas no Ar -> Spotify](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/rfm-live-to-spotify.yml/badge.svg)](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/rfm-live-to-spotify.yml)
[![Comercial TNT Top 20 -> Spotify](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/comercial-to-spotify.yml/badge.svg)](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/comercial-to-spotify.yml)
[![Comercial Live -> Spotify](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/comercial-live-to-spotify.yml/badge.svg)](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/comercial-live-to-spotify.yml)
[![Antena 3 Live -> Spotify](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/antena3-live-to-spotify.yml/badge.svg)](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/antena3-live-to-spotify.yml)
[![M80 Live -> Spotify](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/m80-live-to-spotify.yml/badge.svg)](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/m80-live-to-spotify.yml)
[![Batida FM Live -> Spotify](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/batida-live-to-spotify.yml/badge.svg)](https://github.com/pcarrasqueira/rfm-top25-spotify/actions/workflows/batida-live-to-spotify.yml)

Sete workflows que sincronizam conteudo de radios portuguesas com playlists Spotify.

| Workflow | Fonte | Playlist | Frequencia |
|---|---|---|---|
| **RFM Top 25** | rfm.pt/top25rfm | Os 25 mais tocados | Domingos 23:30 e Segundas 10:00 (Lisboa no verao) |
| **Ultimas no Ar** | rfm.pt/que-musica-era | Rolling das ultimas 300 musicas | Hora a hora |
| **Comercial TNT Top 20** | radiocomercial.pt/programas/tnt | Os 20 mais votados | Domingos 23:54 e Segundas 10:00 (Lisboa no verao) |
| **Comercial Live** | radiocomercial.pt/passou | Rolling das ultimas 300 musicas | Hora a hora |
| **Antena 3 Live** | antena3.rtp.pt | Rolling das ultimas 300 musicas | Hora a hora |
| **M80 Live** | m80.pt | Rolling das ultimas 300 musicas | Hora a hora |
| **Batida FM Live** | listenapi.planetradio.co.uk (bfm) | Rolling das ultimas 300 musicas | Hora a hora |

Os horarios agendados no GitHub Actions sao definidos em UTC; a hora apresentada acima corresponde ao horario de verao de Lisboa.

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
- **Antena 3 Live** -- rolling das ultimas 300 musicas tocadas na Antena 3
- **M80 Live** -- rolling das ultimas 300 musicas tocadas na M80
- **Batida FM Live** -- rolling das ultimas 300 musicas tocadas na Batida FM

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
| `SPOTIFY_ANTENA3_LIVE_PLAYLIST_ID` | ID da playlist Antena 3 Live |
| `SPOTIFY_M80_LIVE_PLAYLIST_ID` | ID da playlist M80 Live |
| `SPOTIFY_BATIDA_LIVE_PLAYLIST_ID` | ID da playlist Batida FM Live |

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

# Antena 3 Live
export SPOTIFY_ANTENA3_LIVE_PLAYLIST_ID=xxx
python antena3_live_to_spotify.py

# M80 Live
export SPOTIFY_M80_LIVE_PLAYLIST_ID=xxx
python m80_live_to_spotify.py

# Batida FM Live
export SPOTIFY_BATIDA_LIVE_PLAYLIST_ID=xxx
python batida_live_to_spotify.py
```

---

## Notas tecnicas

- **API Spotify (2026)**: os endpoints `/playlists/{id}/tracks` foram removidos e substituidos por `/playlists/{id}/items`. Todos os scripts usam os endpoints novos.
- **Scraping RFM**: ambas as paginas (`/top25rfm` e `/que-musica-era`) devolvem HTML estatico.
- **Scraping Comercial TNT**: a pagina `/programas/tnt-todos-no-top` devolve HTML estatico com o top semanal.
- **Scraping Comercial Live**: a pagina `/passou` e renderizada via JS; o scraper extrai texto puro e identifica grupos hora+titulo+artista por regex.
- **Antena 3 Live**: usa o EPG publico da RTP (`programas.rtp.pt`) para mapear programas e a pagina de cada programa para extrair as musicas tocadas.
- **M80 Live**: usa o historico JSON publico da M80 para extrair as musicas tocadas na ultima hora.
- **Batida FM Live**: usa a API `listenapi.planetradio.co.uk` com o codigo de estacao `bfm`, devolvendo os eventos das ultimas N horas em JSON.
- **Spotify rate limits**: todos os workflows partilham um grupo de concorrencia e um cliente comum com tratamento de `Retry-After` e `QUOTA_EXCEEDED`.
- **Refresh Token**: nao expira, a nao ser que revogues o acesso em [spotify.com/account/apps](https://www.spotify.com/account/apps).
- **GitHub Actions (plano gratuito)**: 2000 min/mes. Os cinco workflows hora-a-hora sao escalonados e serializados para evitar bursts na API Spotify.

---

## Estrutura

```
.github/workflows/
  rfm-to-spotify.yml               # RFM Top 25 -- Dom 22:30 UTC + Seg 09:00 UTC
  rfm-live-to-spotify.yml          # Ultimas no Ar (RFM) -- hora a hora
  comercial-to-spotify.yml         # Comercial TNT Top 20 -- Dom 22:54 UTC + Seg 09:00 UTC
  comercial-live-to-spotify.yml    # Comercial Live -- hora a hora
  antena3-live-to-spotify.yml      # Antena 3 Live -- hora a hora
  m80-live-to-spotify.yml           # M80 Live -- hora a hora
  batida-live-to-spotify.yml       # Batida FM Live -- hora a hora
rfm_to_spotify.py                  # script RFM Top 25
rfm_live_to_spotify.py             # script Ultimas no Ar
comercial_to_spotify.py            # script Comercial TNT Top 20
comercial_live_to_spotify.py       # script Comercial Live
antena3_live_to_spotify.py         # script Antena 3 Live
m80_live_to_spotify.py              # script M80 Live
batida_live_to_spotify.py          # script Batida FM Live
spotify_client.py                  # cliente Spotify comum e rate-limit handling
tests/                              # testes unitarios
get_refresh_token.py               # helper para gerar o refresh token
requirements.txt                   # dependencias Python
```
