/**
 * Réglages de la recherche hybride — état partagé du Playground.
 *
 * **Partagés par les deux modes, délibérément.** Le chat n'est pas un autre
 * moteur : il exécute la même recherche, avec les mêmes champs de requête, puis
 * fait rédiger le résultat. Dupliquer les réglages laisserait croire à deux
 * comportements distincts et obligerait à recaler deux fois le même `top_k` —
 * alors que tout l'intérêt du Playground est de régler en mode Recherche, où
 * l'on voit les passages bruts, puis de basculer en Chat pour juger la réponse
 * produite **à partir de ces passages-là**.
 *
 * Ce module ne contient volontairement aucun composant : le formulaire vit dans
 * `RetrievalSettings.tsx`, et les séparer garde le rafraîchissement à chaud de
 * Vite fonctionnel sur l'écran.
 */

export interface RetrievalSettingsValue {
  topK: number;
  rerank: boolean;
  /** `null` : on laisse le moteur appliquer son propre seuil. */
  similarityThreshold: number | null;
  /** Vide : tout le corpus. */
  documentIds: string[];
}

/**
 * Valeurs de départ.
 *
 * `rerank` est actif d'emblée : c'est la qualité de tri que le produit promet,
 * et la découvrir suppose de ne pas avoir à cocher une case dont l'effet n'est
 * lisible qu'en comparant deux recherches. Le coût GPU reste réel (ADR-012),
 * mais il est borné par le service d'inférence, pas par ce réglage.
 *
 * Le piège de seuil décrit par ADR-012 §4 — le reranking change l'échelle du
 * score combiné et peut vider un résultat calé pour `rerank=false` — ne se
 * déclenche pas ici : `similarityThreshold` reste `null`, donc c'est le moteur
 * qui applique le sien, cohérent avec le mode qu'il exécute. Il redeviendrait
 * d'actualité si l'on figeait un seuil par défaut : les deux se règlent
 * ensemble, jamais l'un sans l'autre.
 */
export const DEFAULT_RETRIEVAL_SETTINGS: RetrievalSettingsValue = {
  topK: 10,
  rerank: true,
  similarityThreshold: null,
  documentIds: [],
};

export const MIN_TOP_K = 1;
export const MAX_TOP_K = 50;
