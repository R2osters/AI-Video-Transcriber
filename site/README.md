# Site vitrine — Transcriber

Site statique (HTML + une feuille de style, aucune dépendance, aucun build).
Il est volontairement séparé de `static/`, qui contient l'interface de
l'application et part dans le paquet desktop.

## Contenu

| Fichier | Rôle |
|---|---|
| `index.html` | Landing page — reprise de la maquette `Site web editorial.dc.html` |
| `fonctionnalites.html` | L'application écran par écran, captures réelles |
| `telecharger.html` | Installeur Windows, éditions Docker et sources |
| `docs.html` | Installation, configuration, API HTTP, MCP |
| `plateformes.html` | Plateformes, formats, langues, temps de traitement |
| `changelog.html` | Journal des versions (repris de l'historique git) |
| `confidentialite.html` | Trajets réseau et emplacement des données |
| `assets/site.css` | Design system : couleurs, typo, gabarits, responsive |
| `assets/img/app-*.png` | Captures de l'application réelle (2880×1800) |

## Prévisualiser

```bash
python -m http.server 8801 --bind 127.0.0.1
```

Puis <http://127.0.0.1:8801/>.

## À personnaliser avant publication

- **Dépôt GitHub** — toutes les pages pointent vers
  `https://github.com/wendy7756/AI-Video-Transcriber` (le remote `origin`
  actuel). Remplacez-le par votre fork si le site représente celui-ci.
- **Lien de téléchargement** — `telecharger.html`, section « Chemin A »
  renvoie vers la page des releases. Quand l'installeur est publié, pointez
  directement le `.exe` (un commentaire HTML marque l'endroit).
- **Année et version** — pieds de page et barre utilitaire.

## Régénérer les captures

Les captures viennent de l'application lancée en local, pas d'une maquette.
Servez le backend, ouvrez chaque vue, puis capturez en 1440×900 avec un
facteur d'échelle 2 :

```bash
chrome --headless=new --hide-scrollbars --force-device-scale-factor=2 \
  --window-size=1440,900 --screenshot=site/assets/img/app-new.png \
  http://127.0.0.1:8000/
```

Pour les vues `history` et `settings`, l'historique doit contenir des
entrées, sinon la capture montre un écran vide. Lancez le backend avec
`AVT_DATA_DIR` pointant vers un dossier jetable — cela évite de photographier
vos vraies transcriptions **et** d'afficher votre nom d'utilisateur Windows
dans le panneau « Espace disque », dont le chemin apparaît à l'écran — puis
remplissez-le :

```bash
curl -X POST http://127.0.0.1:8000/api/library/import \
  -H "Content-Type: application/json" \
  -d '{"entries":[{"title":"Exemple","created_at":1788085440,"platform":"youtube","lang_src":"en","lang_dst":"fr","script":"…","favorite":true}]}'
```

Videz aussi le `localStorage` du profil avant la capture : un ancien
`vt_history` est importé automatiquement au premier affichage et se
mélangerait aux entrées de démonstration.

Choisissez un chemin qui vous soit propre, pas un emplacement « évident »
comme `C:\Users\Public\...` : si quelqu'un d'autre — ou un autre outil —
prépare une capture au même moment, il peut vider ce dossier au démarrage
pour repartir d'un état neuf, et faire disparaître le vôtre en pleine
capture. Un chemin unique par session coûte zéro et supprime le risque.

## Déploiement

N'importe quel hébergeur de fichiers statiques convient : GitHub Pages,
Netlify, Vercel, Cloudflare Pages, ou un simple `nginx`. Le dossier `site/`
est la racine à publier.
