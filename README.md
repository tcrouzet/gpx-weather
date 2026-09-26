# GPX Weather

GPX Weather génère des cartes météo interactives pour tous les parcours GPX
placés localement dans `_gpx/`. Les pages utilisent Open-Meteo, les ensembles
ECMWF, Leaflet, OpenStreetMap, la Base Adresse Nationale et GeoNames. Elles
peuvent être installées comme webapp mobile.

Le workflow `.github/workflows/pages.yml` reconstruit et republie le site sur
GitHub Pages à chaque push sur `main` et cinq fois par jour.

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
python serve.py 8000
```

Ouvrir ensuite <http://localhost:8000/>. Le dossier `_output/` contient les
caches météo et les pages produites ; il est ignoré par Git.

Ce serveur local gère également les URL de planning comme
`/g727-2026/forecast/20260926-8-5`. Les trois valeurs représentent la date de
départ, l'heure de départ et le nombre de jours. Le serveur HTTP standard de
Python ne sait pas résoudre ces routes dynamiques : il faut utiliser
`serve.py`.

## Utiliser la carte et les prévisions du voyage

- `/g727-2026/` ouvre la carte météo du parcours ;
- `/g727-2026/forecast/` ouvre le dernier planning enregistré localement ;
- `/g727-2026/forecast/20260926-8-5` impose un départ le 26 septembre 2026 à
  8 h pour une durée de 5 jours.

La roue crantée ouvre les prévisions du voyage. Les changements de date,
d'heure et de durée sont enregistrés dans le stockage local du navigateur.
Le bouton de partage produit toujours l'URL détaillée contenant ces trois
paramètres.

Un clic sur une prévision Matin, Midi ou Soir revient à la carte et affiche la
ville concernée comme un point météo normal. Un second clic sur son pictogramme
ouvre la fiche détaillée ; les données proviennent du point météo disponible le
plus proche de cette ville.

La page d'accueil redirige automatiquement vers la dernière carte ou le dernier
planning consulté. Le lien **Accueil et aide** du menu permet toujours de
revenir explicitement au sélecteur de parcours.

## Calcul des étapes

Le projet construit deux découpages complémentaires : les villes principales
affichées sur la carte sont déterminées pendant la génération, tandis que les
étapes quotidiennes du voyage sont recalculées dans le navigateur selon la date,
l'heure de départ et la durée choisies.

### Sélection des villes principales

1. La longueur du parcours et la position kilométrique de chaque point sont
   calculées sur le GPX avec la formule de haversine. Web Mercator n'est utilisé
   que pour dessiner la carte et jamais pour mesurer les distances.
2. Overpass fournit toutes les communes situées dans un corridor de 5 km autour
   de la trace. Les communes appartenant à une même agglomération sont
   regroupées dans un rayon de 8 km ; la plus peuplée est conservée.
3. Le départ et l'arrivée sont les communes administratives contenant les deux
   extrémités exactes du GPX, trouvées par géocodage inverse. Elles ne sont pas
   remplacées par une ville plus importante située quelques kilomètres plus
   loin.
4. Le nombre théorique de tronçons vaut `trip_days - 1`. Les positions idéales
   sont donc réparties régulièrement sur la distance totale.
5. Parmi les villes situées dans la moitié centrale du parcours, la ville la
   plus peuplée devient une ville-ancre. Cela empêche notamment une grande ville
   structurante d'être éliminée au profit d'un village placé plus exactement sur
   une position théorique. Les autres cibles sont réparties de chaque côté de
   cette ancre, avec une cible supplémentaire avant l'optimisation pour éviter
   de créer un grand vide.
6. Dans la fenêtre kilométrique de chaque cible, l'optimiseur privilégie
   d'abord la commune physiquement la plus proche de la trace, puis la
   population et enfin la proximité de la position idéale. La ville-ancre
   centrale reste choisie séparément selon la population afin de conserver les
   grandes villes structurantes du parcours.

Deux villes retenues doivent être séparées d'au moins
`distance_totale / city_spacing_divisor` **à vol d'oiseau**. Avec la valeur par
défaut `city_spacing_divisor = 18`, une boucle de 720 km impose donc environ
40 km entre deux villes, même si elles se trouvent très loin l'une de l'autre en
suivant la trace. Cette règle évite les superpositions lorsque le parcours se
replie sur lui-même.

### Découpage quotidien du voyage

Le découpage ne partage pas simplement les kilomètres. Après lissage médian des
altitudes, la trace est regroupée en tronçons réels d'environ 100 m. Leur pente
moyenne pondère l'effort, sans utiliser les minuscules segments GPX bruts qui
amplifieraient le bruit d'altitude. En montée, le
multiplicateur est :

```text
1 + 0,003 × pente_en_%³
```

En descente jusqu'à 10 %, un rabais quadratique réduit l'effort ; au-delà de
10 %, aucun gain supplémentaire n'est accordé. Cela évite de considérer les
descentes très raides comme artificiellement rapides. L'effort total est ensuite
réparti entre les jours avec une perte de vitesse de 0,25 km/h par jour : les
premiers jours sont un peu plus longs et les derniers un peu plus courts. Chaque
limite d'effort est enfin reconvertie en position kilométrique sur la trace.

Les cyclistes disposent d'une plage quotidienne de 12 heures, mais le calcul de
la vitesse affichée utilise 9 heures de roulage effectif afin de réserver un
tiers du temps aux arrêts. Cette valeur ne modifie pas la position des étapes.

Pour chaque journée :

- **Matin** correspond à la ville où la nuit précédente a été passée ;
- **Soir** correspond à la ville la plus proche de la limite d'effort du jour et
  devient obligatoirement le **Matin** du lendemain ;
- **Midi** utilise une ville intermédiaire proche de la position atteinte à
  midi, différente des villes du matin et du soir lorsque cela est possible.

Un maillage secondaire de communes, espacé par défaut d'environ 25 km le long
de la trace, fournit ces noms sans afficher toutes les communes sur la carte.
Le centre de ces communes doit se trouver à moins de 2 km du GPX, valeur réglée
par `planning_city_max_distance_to_track_km`.
Les mesures météo sont moins nombreuses : des points sont sélectionnés environ
tous les 100 km et chaque étape utilise les prévisions du point disponible le
plus proche à vol d'oiseau.

Les principaux réglages se trouvent dans `config.py` : `trip_days`,
`city_spacing_divisor`, `planning_climb_coefficient`,
`planning_climb_exponent`, `planning_descent_linear_coefficient`,
`planning_descent_quadratic_coefficient`, `planning_fatigue_speed_loss_kmh`,
`planning_daily_riding_hours`, `planning_daily_moving_hours`,
`planning_city_interval_km`, `planning_city_max_distance_to_track_km` et
`planning_weather_interval_km`.

## Ajouter un parcours

1. Copier le GPX original dans `_gpx/`, par exemple
   `_gpx/mon-parcours.gpx`. Ce dossier reste strictement local et est ignoré
   par Git.
2. Lancer une génération locale :

   ```bash
   GITHUB_ACTIONS=true python app.py
   ```

3. Le programme crée dans `webapp/gpx/` un GPX public simplifié à environ un
   point géométrique par kilomètre, complété par les altitudes minimale et
   maximale de chaque tranche de 500 m afin de préserver sommets et creux ;
   il crée aussi son profil d'effort et le CSV des villes. Ces fichiers doivent
   être versionnés : GitHub Actions les utilise lorsque les originaux privés de
   `_gpx/` sont absents.
4. Vérifier la carte locale à l'adresse
   `http://localhost:8000/mon-parcours/`.

La version allégée téléchargeable est publiée avec la page à l’adresse
`https://MON_COMPTE.github.io/MON_DEPOT/mon-parcours/trace.gpx`.

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

Placer ses GPX originaux dans `_gpx/`,
puis effectuer une première génération sans publication :

```bash
GITHUB_ACTIONS=true python app.py
```

Ajouter au dépôt les GPX publics, les CSV de villes et les changements de
configuration. Ne pas ajouter les dossiers commençant par `_`, notamment
`_gpx/` et `_output/` :

```bash
git add config.py webapp/
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
- cron: "0 4,7,10,14,20 * * *"
```

Il actualise les prévisions cinq fois par jour. Ces horaires correspondent à
6 h, 9 h, 12 h, 16 h et 22 h à Paris pendant l'heure d'été, et une heure plus
tôt pendant l'heure d'hiver. GitHub peut décaler de quelques minutes le
démarrage effectif d'un cron.

## Générer et publier depuis son poste

Après avoir configuré `github_repository`, une exécution normale :

```bash
python app.py
```

génère les cartes localement, puis lance le workflow GitHub avec `gh`. Cela ne
fait pas de commit et n'envoie pas les caches de `_output/`. Le code et les GPX
publics doivent déjà avoir été poussés sur GitHub.

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
  dans `_gpx/` pour une génération locale, ou dans `webapp/gpx/` sur GitHub ;
- **Une URL `/forecast/...` renvoie une erreur en local** : arrêter
  `python -m http.server` et lancer `python serve.py PORT` ;
- **Le workflow recalcule les villes** : générer puis versionner le fichier
  `webapp/gpx/<slug>.villes.csv`.
- **Erreur de publication locale** : vérifier `gh auth status` et la valeur de
  `github_repository` dans `config.py`.
- **Webapp ou liens vers un mauvais dépôt** : vérifier
  `github_pages_base_url`, `start_url`, `scope` et `ROOT`.
- **GitHub Pages n'est pas publié** : vérifier que la source Pages est bien
  **GitHub Actions** et consulter les logs du workflow dans l'onglet Actions.

## Licence

Ce projet est distribué sous licence Apache 2.0. Voir [LICENSE](LICENSE).
