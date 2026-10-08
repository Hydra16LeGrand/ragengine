# `playground/`

Playground de recherche et de chat (Schéma B) : interrogation d'une base, réglage des
paramètres de retrieval (top_k, filtres, rerank), inspection des chunks retournés et de leurs
scores, puis réponse générée avec citations.

Route `/knowledge-bases/:kbId/playground`, sous garde `RequireAuth` : interroger un corpus
mobilise le moteur et, en mode chat, un fournisseur LLM facturé.

Consomme `POST /api/v1/retrieval/search`, `POST /api/v1/retrieval/chat` (flux SSE) et
`GET /api/v1/providers`.

## Fichiers

| Fichier                 | Rôle                                                        |
| ----------------------- | ----------------------------------------------------------- |
| `PlaygroundPage.tsx`    | Écran, bascule de mode, état partagé des réglages           |
| `SearchPanel.tsx`       | Mode Recherche : requête → passages bruts                   |
| `ChatPanel.tsx`         | Mode Chat : flux SSE, sources, citations reliées            |
| `RetrievalSettings.tsx` | Formulaire des réglages                                     |
| `settings.ts`           | Contrat et valeurs par défaut des réglages (sans composant) |
| `PassageCard.tsx`       | Un passage et la raison de sa présence (trois scores)       |

## Décisions structurantes

**Les réglages sont partagés par les deux modes.** Le chat n'est pas un autre moteur : il exécute
la même recherche hybride, avec les mêmes champs de requête, puis fait rédiger le résultat. Les
dupliquer laisserait croire à deux comportements distincts et obligerait à recaler deux fois le
même `top_k` — alors que tout l'intérêt du Playground est de régler en mode Recherche, où l'on
voit les passages bruts, puis de basculer en Chat pour juger la réponse produite **à partir de
ces passages-là**.

**Les trois scores sont montrés ensemble.** Le score combiné seul ne dit pas _pourquoi_ un
passage est remonté. Voir qu'un extrait doit sa place au vectoriel (proximité de sens) et non aux
termes (mots exacts absents) est exactement ce qui permet de juger si le corpus répond bien.

**La recherche est une mutation, pas une query.** Elle est déclenchée par une intention explicite
et ne doit ni se recharger au montage, ni se rejouer en arrière-plan : une liste de passages ne
doit pas changer sous les yeux de qui est en train de l'évaluer.

**Deux natures d'erreur en chat, deux écrans.** Un refus **avant le premier octet** (droits,
`llm_provider_id` inconnu, 422) est une réponse HTTP classique : il invite à corriger la demande.
Une panne **pendant** le flux arrive en évènement `error` et signale que la réponse affichée est
incomplète. `api/retrieval.ts` sépare les deux ; `ChatPanel` les intitule différemment.

**Les sources sont bornées en hauteur.** Elles précèdent la réponse (elles arrivent avant le
premier token) ; sans plafond, une dizaine de passages repousserait sous la ligne de flottaison
le texte que l'utilisateur est venu lire. Elles gardent leur propre défilement, et l'arrivée du
premier token ramène la vue sur la réponse.

**Les citations `[n]` sont reliées aux sources** par survol, focus et clic (la carte de même rang
est mise en évidence et amenée à l'écran). Un numéro hors bornes reste du texte brut : fabriquer
un lien mort serait pire que de montrer la citation telle que le modèle l'a produite.
