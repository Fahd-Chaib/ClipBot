# ClipBot — Shorts automatiques à partir de créateurs partenaires

Le dimanche tu colles des liens, le bot fait le reste et programme tout sur la semaine suivante.

```
liens YouTube (GitHub Actions → Run workflow)
  ──► règles de crédit du créateur (fiche config.yaml si elle existe, sinon crédit automatique)
  ──► audio seul ──► Whisper (Groq, gratuit) ──► transcription mot à mot
  ──► LLM gratuit (gpt-oss-120b) : note chaque moment fort, garde ceux ≥ score minimum (≤ 58 s)
  ──► téléchargement des SEULES sections retenues (jamais la vidéo entière)
  ──► montage 9:16 : suivi du visage / du locuteur, silences coupés, zooms, sous-titres mot par mot + emojis,
      titre accrocheur, ton @ en filigrane
  ──► titre / description / hashtags (ton clickbait mais fidèle) + crédit exigé par le créateur
  ──► planning réparti sur 7 jours (lundi → dimanche suivant)
  ──► YouTube (programmé) · TikTok · Instagram Reels (publiés par le bot à l'heure prévue)
Toutes les heures : publish.yml envoie / publie ce qui est dû.
```

## Quels liens le bot accepte

Par défaut (`access.require_whitelist: false`) il accepte **n'importe quel lien YouTube** que tu lui donnes, et crédite
automatiquement la chaîne d'origine (nom, @, lien de la vidéo) dans la description.

Si un programme de clipping impose un crédit précis (mention, hashtag, suffixe de titre…), ajoute une fiche dans
`config.yaml → creators` : le bot l'utilise dès que le lien vient de cette chaîne.

```bash
python -m clipbot check-creator https://www.youtube.com/watch?v=XXXX   # affiche le bloc à coller
```

Mode strict : `access.require_whitelist: true` → le bot refuse tout créateur absent de la liste (et sans `permission`).
La responsabilité de n'utiliser que des vidéos dont le clipping est autorisé reste la tienne : les vidéos reprises sans
accord s'exposent à des réclamations de droits d'auteur et à la suppression de la chaîne.

## Installation (≈ 45 min, une seule fois)

### 1. Le dépôt
1. Dépôt GitHub **public** (minutes Actions illimitées), envoie-y ce dossier.
2. **Settings → Actions → General → Workflow permissions** → *Read and write permissions*.
3. Édite `config.yaml` : `channel.handle` (ton @), `creators`, `schedule`.

### 2. Secrets (Settings → Secrets and variables → Actions)

| Secret | Où |
|---|---|
| `GROQ_API_KEY` | console.groq.com (gratuit) — transcription + LLM |
| `CEREBRAS_API_KEY` | cloud.cerebras.ai (gratuit, relais conseillé) |
| `STATE_KEY` | `python -m clipbot keygen` |
| `YT_COOKIES_B64` | étape 3 |
| `YOUTUBE_TOKEN_JSON` | étape 4 |
| `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_TOKEN_JSON` | étape 6 |
| `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID` | étape 5 |

Pour les commandes locales : `pip install -r requirements.txt` puis `cp .env.example .env` et remplis-le.

### 3. Cookies YouTube (obligatoire en pratique)
YouTube bloque presque toujours les téléchargements depuis les serveurs GitHub (« confirm you're not a bot »).
1. Avec une extension type *Get cookies.txt LOCALLY*, exporte les cookies de youtube.com (de préférence d'un
   **compte Google secondaire** dédié au bot, pas ton compte principal).
2. Le plus simple : colle le contenu du fichier tel quel dans le secret `YT_COOKIES_TXT` (cookies de youtube.com seulement).
   Si le fichier dépasse 48 000 caractères : `python -m clipbot cookies pack cookies.txt` → secret `YT_COOKIES_B64`.
3. Les cookies expirent : si les téléchargements échouent un jour, refais cette étape.

### 4. YouTube (publication)
1. console.cloud.google.com → nouveau projet → active **YouTube Data API v3**.
2. Écran de consentement OAuth (Externe) → **publie-le en Production** (sinon le jeton expire après 7 jours).
3. Identifiants → ID client OAuth → *Application de bureau* → télécharge le JSON dans `secrets/youtube_client_secret.json`.
4. `python -m clipbot auth youtube` → connecte la chaîne → copie le JSON affiché dans `YOUTUBE_TOKEN_JSON`.
5. ⚠ **Audit obligatoire** : tant que ton projet Google n'a pas passé l'audit « YouTube API Services – Audit and
   Quota Extension », YouTube force les vidéos envoyées par API en **privé**. Remplis le formulaire dès les
   premiers tests. Le même formulaire permet d'augmenter le quota (par défaut ≈ 6 uploads/jour : le bot s'y adapte
   en envoyant 5 clips/jour maximum, les suivants partent les jours d'après, toujours programmés à la bonne heure).

### 5. Instagram (compte Professionnel ou Créateur)
developers.facebook.com → app → produit Instagram → *API avec connexion Instagram* → ajoute ton compte →
génère un jeton avec `instagram_business_content_publish`. Mets le jeton et l'ID du compte dans les secrets.
Le bot renouvelle le jeton tout seul (stocké chiffré).

### 6. TikTok
1. developers.tiktok.com → app → **Login Kit** + **Content Posting API** (Direct Post).
2. Mets `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI` dans `.env` (le redirect : n'importe quelle page à toi).
3. `python -m clipbot auth tiktok` → autorise → colle l'URL de retour → copie le JSON dans `TIKTOK_TOKEN_JSON`.
4. ⚠ Tant que TikTok n'a pas **audité** l'app, tout ce qui est posté par l'API reste **privé**.

### 7. Premiers tests (Actions → ClipBot → Run workflow)
1. `mode = demo` : rend 2 vidéos de test (anglais + arabe), sans rien publier → télécharge-les dans *Artifacts*.
2. `mode = analyze` + un lien : choisit les moments forts et écrit un rapport (`analysis_*.md`) avec les liens
   horodatés — idéal pour régler `selection.min_score`. Rien n'est téléchargé en vidéo ni publié.
3. `mode = preview` + un lien : monte vraiment les clips et te les donne en *Artifacts* (avec titres et descriptions dans
   `preview_clips.md`), sans rien mettre en file ni publier. C'est le test à faire avant le premier vrai passage.
4. `mode = run` : le vrai passage, tout est programmé automatiquement. Les premières semaines, regarde les clips
   produits (Artifacts de l'exécution, 3 jours) et mets `platforms.tiktok/instagram.enabled: false` le temps de
   te faire confiance. Tu peux aussi désactiver YouTube de la même façon pour un test à blanc.

## Ton dimanche (depuis ton PC — recommandé)

YouTube bloque souvent les téléchargements depuis les serveurs GitHub ; depuis ta connexion habituelle ça passe.
Le PC télécharge et monte les clips, puis GitHub programme et publie tout seul (ton PC peut s'éteindre).

Installation sur ton PC (une seule fois) :
1. `git clone https://github.com/Fahd-Chaib/ClipBot` puis `cd ClipBot` et `pip install -r requirements.txt`
2. ffmpeg : `winget install ffmpeg` (Windows) ou `brew install ffmpeg` (Mac)
3. GitHub CLI : `winget install GitHub.cli` puis `gh auth login`
4. `copy .env.example .env` et remplis `GROQ_API_KEY` (et `CEREBRAS_API_KEY` si tu en as une)

Chaque dimanche : `python -m clipbot go LIEN1 LIEN2 ...`
Options : `--preview` (monte les clips dans `out/clips` sans rien envoyer) · `--analyze-only` · `--force`.

## Ton dimanche (depuis GitHub, si le téléchargement passe)

Actions → **ClipBot** → *Run workflow* → colle les liens (un par ligne) → *mode = run*. C'est tout.
`python -m clipbot status` (en local, ou regarde les logs) montre la file d'attente.

## Réglages utiles (`config.yaml`)

- `selection.min_score` : plus haut = plus sélectif. Pas de quota fixe : 3 clips ou 12 selon la vidéo.
- `selection.max_seconds` : 58 (plafond Shorts) · `max_clips_per_video` : garde-fou.
- `schedule` : `start` (next_monday / tomorrow), `max_per_day`, `window` (heures locales), `timezone`.
- `render.zoom`, `render.captions`, `render.title`, `render.watermark` : look du montage.
- `platforms.<x>.enabled` : coupe une plateforme sans toucher au reste.

## Limites à connaître

- **Darija** : Whisper transcrit l'arabe correctement mais le darija reste approximatif ; relis les premiers clips.
- **Suivi du visage** : détection frontale (OpenCV). Visage de profil / très petit / caché → la caméra garde sa
  dernière position. Les plans larges à plusieurs personnes éloignées sont suivis au locuteur.
- **Titres clickbait** : le LLM a l'ordre de ne rien inventer, mais relis les premiers : un titre trompeur est
  sanctionné par YouTube et TikTok.
- **Crédit** : respecte les règles de chaque programme (`creators[].credit`). Certains exigent un tag précis.
- GitHub met en pause les tâches planifiées d'un dépôt public après 60 jours sans activité (un clic pour relancer).
- Les horaires GitHub sont « au mieux » : TikTok / Instagram peuvent partir avec quelques minutes de retard.

## Arborescence

```
clipbot/
  download.py     yt-dlp (audio, puis sections)   transcribe.py  Whisper mot à mot
  highlights.py   moments forts (LLM)             meta.py        titres / descriptions / crédit
  llm.py          Groq → Cerebras                 creators.py    liste blanche
  framing.py      visage + locuteur + caméra      captions.py    sous-titres, emojis, titre (FR/EN/AR)
  render.py       montage 9:16                    scheduler.py   planning de la semaine
  queue.py        file d'attente                  storage.py     clips en attente (release GitHub)
  publisher.py    passage horaire                 publish/       youtube · tiktok · instagram
  vault.py        jetons chiffrés                 pipeline.py    un passage complet
.github/workflows/  clipbot.yml (tu lances) · publish.yml (toutes les heures)
deploy/state.sh     sauvegarde / restauration de la mémoire du bot (branche `state`)
```
