/**
 * Amorce commune à tous les fichiers de test (`setupFiles`, cf. `vite.config.ts`).
 *
 * Le runner tourne sans globals (`globals: false`) : `describe`/`it`/`expect`
 * sont importés explicitement dans chaque fichier. L'auto-nettoyage de Testing
 * Library, lui, ne s'installe que s'il trouve un `afterEach` **global** — sans
 * ce fichier il ne se brancherait donc jamais, et le DOM d'un test fuiterait
 * dans le suivant (une requête `getBy*` y trouverait deux éléments au lieu
 * d'un, avec un message d'erreur qui n'accuse pas le vrai coupable).
 */
import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach } from 'vitest';

import { overridePlugins } from '@/plugins';

afterEach(() => {
  cleanup();
});

/**
 * Chaque test part du **cœur seul** (ADR-069) : le même test passe à l'identique
 * avec ou sans greffon installé. Un test de greffon qui a besoin de son
 * enregistrement (routes, navigation) le remet lui-même (`overridePlugins`).
 */
beforeEach(() => {
  overridePlugins([]);
});

/**
 * `<dialog>` natif : jsdom expose l'élément mais **pas** `showModal()`/`close()`.
 *
 * Tout composant monté à travers `components/Dialog` appelle `showModal()` dans
 * un effet ; sans ces deux méthodes, le montage lève et le test échoue sur une
 * pile React qui n'accuse pas la vraie cause. Le comble d'ouverture réel (fond
 * inerte, piège de focus, `Esc`) n'est pas simulé — ce n'est pas ce que ces
 * tests observent : ils vérifient le contenu rendu, qui, lui, est exact.
 */
if (typeof HTMLDialogElement !== 'undefined') {
  const proto = HTMLDialogElement.prototype;
  if (typeof proto.showModal !== 'function') {
    proto.showModal = function showModal(this: HTMLDialogElement): void {
      this.open = true;
    };
  }
  if (typeof proto.close !== 'function') {
    proto.close = function close(this: HTMLDialogElement): void {
      this.open = false;
    };
  }
}

/**
 * jsdom n'implémente pas `scrollIntoView` : tout composant qui défile vers un
 * message reçu (chat Playground, conversation publique) lève dès qu'une conversation
 * gagne une entrée, sur une pile qui n'accuse pas la vraie cause.
 */
if (
  typeof HTMLElement !== 'undefined' &&
  typeof HTMLElement.prototype.scrollIntoView !== 'function'
) {
  HTMLElement.prototype.scrollIntoView = function scrollIntoView(): void {
    // no-op : jsdom n'a pas de mise en page, il n'y a rien à défiler.
  };
}
