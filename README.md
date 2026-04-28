# RFM → Spotify

Dois workflows que sincronizam conteúdo da RFM com playlists Spotify.

| Workflow | Playlist | Frequência |
|---|---|---|
| **RFM Top 25** | Os 25 mais tocados do momento | 1x/dia (09:00 UTC) |
| **Últimas no Ar** | Rolling das últimas 100 músicas tocadas | Hora a hora |

---

## Setup

### 1. Criar app Spotify

1. Vai a [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Cria uma nova app
3. Adiciona `http://127.0.0.1:8888/callback` como **Redirect URI**
4. Em **User Management**, adiciona o email da tua conta Spotify
5. Copia o **Client ID** e **Client Secret**

> **Nota**: Em Development Mode a Spotify exige que a conta esteja em User Management. Sem isso, todos os pedidos de escrita dão 403.

### 2. Obter o Refresh Token

```bash
pip install requests
python get_refresh_token.py
```

O script abre um URL no browser para autorização. Confirma que o URL contém:
```
scope=playlist-modify-public+playlist-modify-private
```

No final imprime o Refresh Token — copia-o para o passo seguinte.

### 3. Criar as playlists no Spotify

Cria duas playlists vazias no Spotify (pública ou privada, à tua escolha):
- **RFM Top 25** — será substituída diariamente
- **Últimas no Ar** — rolling das últimas 100 músicas tocadas na rádio

O ID de cada playlist está no URL: `https://open.spotify.com/playlist/**ID_AQUI**`

### 4. Adicionar GitHub Secrets

Em **Settings → Secrets and variables → Actions** adiciona:

| Secret | Valor |
|---|---|
| `SPOTIFY_CLIENT_ID` | Client ID da app Spotify |
| `SPOTIFY_CLIENT_SECRET` | Client Secret da app Spotify |
| `SPOTIFY_REFRESH_TOKEN` | Refresh token obtido no passo 2 |
| `SPOTIFY_PLAYLIST_ID` | ID da playlist RFM Top 25 |
| `SPOTIFY_LIVE_PLAYLIST_ID` | ID da playlist Últimas no Ar |

### 5. Testar

Vai a **Actions** e corre cada workflow manualmente com **Run workflow**.

Os logs mostram cada track encontrado (✓), já existente (=) ou não encontrado (✗).

---

## Correr localmente

```bash
pip install -r requirements.txt

export SPOTIFY_CLIENT_ID=xxx
export SPOTIFY_CLIENT_SECRET=xxx
export SPOTIFY_REFRESH_TOKEN=xxx

# Top 25
export SPOTIFY_PLAYLIST_ID=xxx
python rfm_to_spotify.py

# Últimas no Ar
export SPOTIFY_LIVE_PLAYLIST_ID=xxx
python rfm_live_to_spotify.py
```

---

## Notas técnicas

- **API Spotify (Fev 2026)**: os endpoints `/playlists/{id}/tracks` foram removidos e substituídos por `/playlists/{id}/items`. Ambos os scripts usam os endpoints novos.
- **Scraping RFM**: ambas as páginas (`/top25rfm` e `/que-musica-era`) devolvem HTML estático — sem necessidade de headless browser. O `/que-musica-era` ignora parâmetros de hora via URL (os dropdowns são JS), por isso o script recolhe sempre a hora actual.
- **Refresh Token**: não expira, a não ser que revogues o acesso em [spotify.com/account/apps](https://www.spotify.com/account/apps) ou mudes a password.
- **GitHub Actions (plano gratuito)**: 2000 min/mês. O workflow hora-a-hora usa ~360 min/mês, bem dentro do limite.

---

## Estrutura

```
.github/workflows/
  rfm-to-spotify.yml        # Top 25 — diário 09:00 UTC
  rfm-live-to-spotify.yml   # Últimas no Ar — hora a hora
rfm_to_spotify.py           # script Top 25
rfm_live_to_spotify.py      # script Últimas no Ar
get_refresh_token.py        # helper para gerar o refresh token
requirements.txt            # dependências Python
```
