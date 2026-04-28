# RFM Top 25 → Spotify

Workflow que scrapa o [RFM Top 25](https://rfm.pt/top25rfm) diariamente e atualiza automaticamente uma playlist Spotify.

## Como funciona

1. O GitHub Actions corre todos os dias às 09:00 UTC (10:00 Lisboa hora de inverno / 10:00 hora de verão)
2. Scrapa os 25 tracks em rfm.pt/top25rfm
3. Pesquisa cada track na API do Spotify
4. Substitui todos os tracks da playlist pelos encontrados

## Setup

### 1. Criar app Spotify

1. Vai a [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Cria uma nova app
3. Adiciona `http://localhost:8888/callback` como **Redirect URI**
4. Copia o **Client ID** e **Client Secret**

### 2. Obter o Refresh Token

```bash
pip install requests
python get_refresh_token.py
```

Segue as instruções no terminal. No final terás os 3 valores necessários.

### 3. Adicionar GitHub Secrets

No teu repo, vai a **Settings → Secrets and variables → Actions** e adiciona:

| Secret | Valor |
|---|---|
| `SPOTIFY_CLIENT_ID` | Client ID da app Spotify |
| `SPOTIFY_CLIENT_SECRET` | Client Secret da app Spotify |
| `SPOTIFY_REFRESH_TOKEN` | Refresh token obtido no passo 2 |

> A `SPOTIFY_PLAYLIST_ID` já está hardcoded no workflow (`5Bgp9ddbbmwNkbzAFy5SSC`).
> Se quiseres mudar a playlist, edita `.github/workflows/rfm-to-spotify.yml`.

### 4. Correr manualmente

Vai a **Actions → RFM Top 25 → Spotify → Run workflow** para testar antes do agendamento.

## Correr localmente

```bash
pip install -r requirements.txt

export SPOTIFY_CLIENT_ID=xxx
export SPOTIFY_CLIENT_SECRET=xxx
export SPOTIFY_REFRESH_TOKEN=xxx
export SPOTIFY_PLAYLIST_ID=5Bgp9ddbbmwNkbzAFy5SSC

python rfm_to_spotify.py
```

## Estrutura

```
.github/workflows/rfm-to-spotify.yml  # workflow agendado
rfm_to_spotify.py                     # script principal
get_refresh_token.py                  # helper para obter o refresh token
requirements.txt                      # dependências Python
```
