# RFM Top 25 → Spotify

Workflow que scrapa o [RFM Top 25](https://rfm.pt/top25rfm) diariamente e atualiza automaticamente uma playlist Spotify.

## Como funciona

1. O GitHub Actions corre todos os dias às 09:00 UTC (10:00 Lisboa)
2. Scrapa os 25 tracks de rfm.pt/top25rfm (HTML estático, sem JS)
3. Pesquisa cada track na API do Spotify
4. Remove os tracks actuais da playlist e adiciona os novos por ordem

## Setup

### 1. Criar app Spotify

1. Vai a [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Cria uma nova app
3. Adiciona `http://127.0.0.1:8888/callback` como **Redirect URI**
4. Em **User Management**, adiciona o email da tua conta Spotify
5. Copia o **Client ID** e **Client Secret**

> **Nota**: Em Development Mode a Spotify exige que a conta que vai usar a app esteja adicionada em User Management. Sem isso, todos os pedidos de escrita dão 403.

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

### 3. Adicionar GitHub Secrets

Em **Settings → Secrets and variables → Actions** adiciona:

| Secret | Valor |
|---|---|
| `SPOTIFY_CLIENT_ID` | Client ID da app Spotify |
| `SPOTIFY_CLIENT_SECRET` | Client Secret da app Spotify |
| `SPOTIFY_REFRESH_TOKEN` | Refresh token obtido no passo 2 |
| `SPOTIFY_PLAYLIST_ID` | ID da playlist a atualizar |

> O ID da playlist está no URL do Spotify: `https://open.spotify.com/playlist/**ID_AQUI**`

### 4. Testar

Vai a **Actions → RFM Top 25 → Spotify → Run workflow** para correr manualmente.

O log mostra cada track encontrado (✓) ou não encontrado (✗) no Spotify.

## Correr localmente

```bash
pip install -r requirements.txt

export SPOTIFY_CLIENT_ID=xxx
export SPOTIFY_CLIENT_SECRET=xxx
export SPOTIFY_REFRESH_TOKEN=xxx
export SPOTIFY_PLAYLIST_ID=xxx

python rfm_to_spotify.py
```

## Notas técnicas

- **API Spotify (Fev 2026)**: os endpoints `/playlists/{id}/tracks` foram removidos e substituídos por `/playlists/{id}/items`. O script usa os endpoints novos.
- **Refresh Token**: não expira, a não ser que revogues o acesso em [spotify.com/account/apps](https://www.spotify.com/account/apps) ou mudes a password.
- **Scraping**: os 25 tracks estão no HTML estático da página, não é necessário headless browser.

## Estrutura

```
.github/workflows/rfm-to-spotify.yml  # workflow agendado (09:00 UTC diário)
rfm_to_spotify.py                     # script principal
get_refresh_token.py                  # helper para gerar o refresh token
requirements.txt                      # dependências Python
```
