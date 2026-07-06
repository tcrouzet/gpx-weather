# GPX Weather

GPX Weather génère des cartes météo interactives pour tous les parcours GPX
placés dans `gpx/`. Les pages utilisent Open-Meteo, les ensembles ECMWF,
Leaflet et OpenStreetMap. Elles peuvent être installées comme webapp mobile.

Le workflow `.github/workflows/pages.yml` reconstruit et republie le site sur
GitHub Pages à chaque push sur `main` et toutes les trois heures.

## Prérequis

- Python 3.12 ou plus récent ;
- Git ;
- un compte GitHub ;
- GitHub CLI (`gh`) si l'on veut publier avec `python app.py` depuis son poste.

Aucune clé Open-Meteo ni aucun secret GitHub supplémentaire ne sont requis.

## Installation locale depuis le dépôt existant

```bash
git clone https://github.com/tcrouzet/gpx-weather.git
cd gpx-weather
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-pages.txt
```

Sous Windows PowerShell, l'activation de l'environnement devient :

```powershell
.\venv\Scripts\Activate.ps1
```

Pour générer le site localement sans déclencher de publication GitHub :

```bash
GITHUB_ACTIONS=true python app.py
```

Les fichiers sont créés dans `_output/`. Pour les consulter :

```bash
python -m http.server 8000 --directory _output
```

Ouvrir ensuite <http://localhost:8000/>. Le dossier `_output/` contient les
caches météo et les pages produites ; il est ignoré par Git.

## Ajouter un parcours

1. Copier le fichier dans `gpx/`, par exemple `gpx/mon-parcours.gpx`.
2. Lancer une génération locale :

   ```bash
   GITHUB_ACTIONS=true python app.py
   ```

3. Le programme crée `gpx/mon-parcours.villes.csv`. Ce fichier doit être
   versionné avec le GPX : les villes sont stables et ne doivent pas être
   recalculées par GitHub Actions à chaque cron.
4. Vérifier la carte locale à l'adresse
   `http://localhost:8000/mon-parcours/`.

Le nom du fichier détermine l'URL. Les espaces et accents sont automatiquement
convertis en slug. Par exemple `Gravel Across Switzerland.gpx` produit
`/gravel-across-switzerland/`.

## Créer son propre hébergement GitHub Pages

### 1. Cloner le projet et créer un nouveau dépôt

Se connecter d'abord avec GitHub CLI :

```bash
gh auth login
```

Puis cloner ce projet et créer un dépôt public vide dans son propre compte :

```bash
git clone https://github.com/tcrouzet/gpx-weather.git mon-gpx-weather
cd mon-gpx-weather
git remote rename origin upstream
gh repo create MON_COMPTE/MON_DEPOT --public --source=. --remote=origin
```

Remplacer `MON_COMPTE` par le nom du compte GitHub et `MON_DEPOT` par le nom du
nouveau dépôt, par exemple `gpx-weather`.

Sans GitHub CLI, créer un dépôt vide depuis l'interface GitHub, puis exécuter :

```bash
git remote rename origin upstream
git remote add origin https://github.com/MON_COMPTE/MON_DEPOT.git
```

Ne pas pousser immédiatement : configurer d'abord les URLs décrites ci-dessous.

### 2. Configurer les URLs du nouvel hébergement

Dans `config.py`, remplacer :

```python
github_repository = "MON_COMPTE/MON_DEPOT"
github_pages_base_url = "https://MON_COMPTE.github.io/MON_DEPOT"
```

Dans `webapp/manifest.webmanifest`, adapter les deux chemins :

```json
"start_url": "/MON_DEPOT/",
"scope": "/MON_DEPOT/"
```

Dans `webapp/sw.js`, adapter également :

```javascript
const ROOT = '/MON_DEPOT/';
```

Si le dépôt s'appelle exactement `MON_COMPTE.github.io`, le site est publié à
la racine du domaine. Utiliser alors `https://MON_COMPTE.github.io` dans
`config.py` et `/` pour `start_url`, `scope` et `ROOT`.

### 3. Installer et générer les villes localement

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-pages.txt
```

Remplacer les exemples présents dans `gpx/` par ses propres GPX si nécessaire,
puis effectuer une première génération sans publication :

```bash
GITHUB_ACTIONS=true python app.py
```

Ajouter au dépôt les GPX, les CSV de villes et les changements de
configuration. Ne pas ajouter `_output/` :

```bash
git add config.py webapp/ gpx/
git commit -m "Configurer mes parcours météo"
git push -u origin main
```

### 4. Activer GitHub Pages

Dans le nouveau dépôt GitHub :

1. ouvrir **Settings > Pages** ;
2. dans **Build and deployment > Source**, choisir **GitHub Actions** ;
3. ouvrir l'onglet **Actions** et vérifier le workflow
   **Actualiser la carte météo**.

Le push sur `main` lance automatiquement la première publication. Le site est
ensuite disponible à :

```text
https://MON_COMPTE.github.io/MON_DEPOT/
https://MON_COMPTE.github.io/MON_DEPOT/mon-parcours/
```

Le cron défini dans `.github/workflows/pages.yml` utilise l'heure UTC :

```yaml
- cron: "17 */3 * * *"
```

Il actualise les prévisions toutes les trois heures. GitHub peut décaler de
quelques minutes le démarrage d'un cron.

## Générer et publier depuis son poste

Après avoir configuré `github_repository`, une exécution normale :

```bash
python app.py
```

génère les cartes localement, puis lance le workflow GitHub avec `gh`. Cela ne
fait pas de commit et n'envoie pas les caches de `_output/`. Le code et les GPX
doivent déjà avoir été poussés sur GitHub.

Pour lancer uniquement la publication distante :

```bash
gh workflow run pages.yml --repo MON_COMPTE/MON_DEPOT
```

## Installer la webapp sur mobile

- iPhone/iPad : ouvrir le site dans Safari, bouton **Partager**, puis
  **Sur l'écran d'accueil** ;
- Android : ouvrir le site dans Chrome, menu, puis **Installer l'application**
  ou **Ajouter à l'écran d'accueil**.

La webapp est configurée pour un affichage vertical. Les pages déjà consultées
peuvent être rouvertes depuis le cache ; les fonds de carte et les nouvelles
prévisions nécessitent une connexion réseau.

## Dépannage

- **Aucun GPX trouvé** : vérifier qu'au moins un fichier `.gpx` est présent
  dans `gpx/`.
- **Le workflow recalcule les villes** : générer puis versionner le fichier
  `gpx/<slug>.villes.csv`.
- **Erreur de publication locale** : vérifier `gh auth status` et la valeur de
  `github_repository` dans `config.py`.
- **Webapp ou liens vers un mauvais dépôt** : vérifier
  `github_pages_base_url`, `start_url`, `scope` et `ROOT`.
- **GitHub Pages n'est pas publié** : vérifier que la source Pages est bien
  **GitHub Actions** et consulter les logs du workflow dans l'onglet Actions.
