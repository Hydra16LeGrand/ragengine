/**
 * Séparé de `StagedFileRow.tsx` : une constante partagée à côté d'un composant
 * exporté casse le rafraîchissement à chaud (`react-refresh/only-export-components`).
 */

let sequence = 0;

/** Identifiant local : deux fichiers homonymes restent deux lignes distinctes. */
export function nextStagedFileId(): string {
  sequence += 1;
  return `staged-${String(sequence)}`;
}
