/**
 * Relais en mémoire, entre la création d'une base et l'écran de détail qui
 * suit immédiatement — pour les fichiers mis en attente **avant** que la base
 * existe (`KnowledgeBaseForm` en mode création).
 *
 * Ni `sessionStorage` ni l'état de navigation (`navigate(path, { state })`) :
 * un `File` ne se sérialise pas en JSON, et le faire transiter par
 * `history.pushState` le soumettrait à l'algorithme de clonage structuré du
 * navigateur — un pari inutile sur un trajet qui ne quitte jamais cet onglet.
 * Un simple module-level `Map` couvre exactement la durée de vie voulue (le
 * temps de cette session d'onglet, jamais au-delà) sans dépendre d'une API
 * navigateur tierce.
 *
 * Une seule lecture : `takePendingUploads` retire ce qu'il rend, pour qu'un
 * double montage (React StrictMode, retour arrière) ne renvoie pas deux fois
 * les mêmes fichiers.
 */

const pending = new Map<string, File[]>();

/** Dépose les fichiers à reprendre pour cette base, une fois créée. */
export function stashPendingUploads(knowledgeBaseId: string, files: File[]): void {
  if (files.length === 0) return;
  pending.set(knowledgeBaseId, files);
}

/** Retire et renvoie les fichiers en attente pour cette base — `[]` si aucun (ou déjà repris). */
export function takePendingUploads(knowledgeBaseId: string): File[] {
  const files = pending.get(knowledgeBaseId);
  if (files === undefined) return [];
  pending.delete(knowledgeBaseId);
  return files;
}
