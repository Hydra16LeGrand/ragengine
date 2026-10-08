/**
 * Ce que le gabarit de modale garantit à tous les dialogues de l'application.
 *
 * Un point est ici et pas ailleurs parce qu'il est **structurel** : le
 * confinement du défilement. `showModal()` rend le fond inerte au clavier et
 * aux lecteurs d'écran, mais pas à la molette — remontée d'usage sur le popup
 * des fragments : « quand il n'y a rien à scroller, c'est la page de fond qui
 * scrolle ». Le correctif est une règle CSS sur la zone défilante du gabarit,
 * donc valable pour les neuf dialogues du dépôt d'un seul geste.
 *
 * jsdom n'applique aucune feuille de style et Vitest neutralise les CSS
 * modules : `getComputedStyle` ne peut rien affirmer ici. Le test se pose donc
 * sur les deux moitiés qui, ensemble, font la garantie — la règle existe dans
 * la feuille, et c'est bien la classe qu'elle vise qui porte le contenu
 * défilant dans le DOM rendu. Retirer l'une ou l'autre casse le test.
 */
import { screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import { renderWithProviders } from '@/test/render';

import { Dialog } from './Dialog';
import styles from './Dialog.module.css';

// Lecture directe de la feuille : un `import ... ?raw` ne rendrait pas la
// source ici, Vitest neutralisant tout ce qui ressemble à un CSS module. Le
// chemin part du dossier du test, pas du dossier courant du lanceur.
const stylesheet = readFileSync(join(import.meta.dirname, 'Dialog.module.css'), 'utf8');

/** Corps de la règle `.<nom>` telle qu'écrite dans la feuille. */
function ruleBody(selector: string): string {
  // Ancré en début de ligne : `.panel` seul, jamais `.dialog[open] .panel`.
  const match = new RegExp(`^\\.${selector}\\s*\\{([^}]*)\\}`, 'm').exec(stylesheet);
  expect(match, `règle .${selector} absente de Dialog.module.css`).not.toBeNull();
  return match?.[1] ?? '';
}

describe('Dialog', () => {
  it('confine le défilement de la modale au lieu de le laisser filer vers la page', () => {
    expect(ruleBody('body')).toContain('overscroll-behavior: contain');
    // Le corps n'est pas la seule zone survolable : l'en-tête et le pied
    // laisseraient passer la molette si le panneau ne la retenait pas aussi.
    expect(ruleBody('panel')).toContain('overscroll-behavior: contain');
  });

  it('rend le contenu dans la zone défilante que cette règle vise', () => {
    const { container } = renderWithProviders(
      <Dialog open onClose={vi.fn()} title="Fragments indexés" closeLabel="Fermer">
        <p>Contenu du dialogue</p>
      </Dialog>,
    );

    expect(screen.getByText('Contenu du dialogue')).toBeDefined();
    // `styles.body` est typé `string | undefined` (index non vérifié) : on
    // interroge le DOM avec la valeur réelle, quel que soit le nom de classe
    // que le pipeline CSS modules a produit.
    const bodyClass = String(styles.body);
    const body = container.querySelector(`.${bodyClass}`);
    expect(body).not.toBeNull();
    expect(body?.textContent).toContain('Contenu du dialogue');
  });
});
