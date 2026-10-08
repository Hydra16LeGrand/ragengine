/**
 * Contrat de l'écran d'aiguillage, rapport déjà disponible.
 *
 * Le timing d'arrivée du rapport — la couture qui portait le défaut d'ADR-031 —
 * se teste depuis `JobTracker.test.tsx`, seul endroit où il est observable.
 * Ici on épingle ce que ce composant promet une fois le rapport en main : la
 * proposition est pré-cochée, un rapport dégradé ne propose rien, et la
 * décision transmise est **ce qui est affiché**, pas la proposition d'origine.
 */
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { fr } from '@/i18n/locales/fr';
import { checkboxLabelled } from '@/test/dom';
import { precheckReport } from '@/test/fixtures';
import { renderWithProviders } from '@/test/render';

import { PrecheckReview } from './PrecheckReview';

describe('PrecheckReview', () => {
  it('pré-coche la proposition d’un PDF à tableaux', () => {
    renderWithProviders(
      <PrecheckReview
        report={precheckReport({ tableCount: 3 })}
        filename="rapport.pdf"
        loading={false}
        submitting={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );

    expect(checkboxLabelled(fr['precheck.decision.ocr']).checked).toBe(true);
    expect(checkboxLabelled(fr['precheck.decision.tables']).checked).toBe(true);
    expect(checkboxLabelled(fr['precheck.decision.parentChild']).checked).toBe(false);
  });

  it('ne pré-coche pas l’analyse de mise en page sur un PDF à colonnes, et dit pourquoi', () => {
    // ADR-042 — la couture qui compte : une fois le rapport arrivé, la case
    // proposée par défaut sur tout PDF depuis ADR-031 doit être **décochée**
    // sur ce document-là, et l'écran doit porter la raison. Une case qui change
    // d'état d'un document à l'autre sans explication serait illisible.
    renderWithProviders(
      <PrecheckReview
        report={precheckReport({ twoColumnPageCount: 2, tableCount: 0 })}
        filename="rapport.pdf"
        loading={false}
        submitting={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );

    expect(checkboxLabelled(fr['precheck.decision.ocr']).checked).toBe(false);
    expect(screen.getByText(fr['precheck.decision.ocrColumnsWarning'])).toBeDefined();
    expect(
      screen.getByText(fr['precheck.metric.columnsValue'].replace('{count}', '2')),
    ).toBeDefined();
  });

  it('garde l’analyse de mise en page cochée quand colonnes et tableaux coexistent', () => {
    // ADR-043 : les deux gains ne s'opposent plus, le backend route ce cas
    // vers un moteur de mise en page qui les tient tous les deux. L'écran
    // garde donc la case cochée (proposition d'ADR-031) et remplace le texte
    // d'arbitrage par ce qu'il advient réellement du document.
    renderWithProviders(
      <PrecheckReview
        report={precheckReport({ twoColumnPageCount: 4, tableCount: 16 })}
        filename="rapport.pdf"
        loading={false}
        submitting={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );

    expect(checkboxLabelled(fr['precheck.decision.ocr']).checked).toBe(true);
    expect(screen.getByText(fr['precheck.decision.ocrColumnsWithTables'])).toBeDefined();
  });

  it('ne dit rien des colonnes quand le document n’en a pas', () => {
    renderWithProviders(
      <PrecheckReview
        report={precheckReport({ twoColumnPageCount: 0 })}
        filename="rapport.pdf"
        loading={false}
        submitting={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.queryByText(fr['precheck.decision.ocrColumnsWarning'])).toBeNull();
    expect(screen.queryByText(fr['precheck.decision.ocrColumnsWithTables'])).toBeNull();
    expect(screen.getByText(fr['precheck.metric.columnsNone'])).toBeDefined();
  });

  it('ne pré-coche rien sur un rapport dégradé et dit pourquoi', () => {
    renderWithProviders(
      <PrecheckReview
        report={precheckReport({
          supported: false,
          tableCount: 3,
          error: { code: 'corrupted_file', message: 'Fichier illisible.' },
        })}
        filename="rapport.pdf"
        loading={false}
        submitting={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText(fr['precheck.degradedTitle'])).toBeDefined();
    expect(checkboxLabelled(fr['precheck.decision.ocr']).checked).toBe(false);
    expect(checkboxLabelled(fr['precheck.decision.tables']).checked).toBe(false);
  });

  it('transmet ce que l’utilisateur a laissé à l’écran, pas la proposition d’origine', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    renderWithProviders(
      <PrecheckReview
        report={precheckReport({ tableCount: 3 })}
        filename="rapport.pdf"
        loading={false}
        submitting={false}
        error={null}
        onSubmit={onSubmit}
      />,
    );

    // L'utilisateur contredit la proposition — tout l'objet de cet écran.
    await user.click(checkboxLabelled(fr['precheck.decision.ocr']));
    await user.click(checkboxLabelled(fr['precheck.decision.parentChild']));
    await user.click(screen.getByRole('button', { name: fr['precheck.decision.submit'] }));

    expect(onSubmit).toHaveBeenCalledWith({
      optimize_for_tables: true,
      heavy_ocr: false,
      use_parent_child: true,
      chunk_method_override: null,
    });
  });

  it('dit la présence de figures sans jamais afficher leur nombre', () => {
    // Le compte n'est pas fiable pour l'utilisateur (une page scannée entière
    // compte pour « une figure » côté precheck) : seule la présence compte.
    renderWithProviders(
      <PrecheckReview
        report={precheckReport({ figureCount: 7 })}
        filename="rapport.pdf"
        loading={false}
        submitting={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );

    // Le libellé ne porte plus le chiffre — jamais « 7 » quelque part dans le texte.
    expect(screen.getByText(fr['precheck.metric.figuresDetected'])).toBeDefined();
  });

  it('dit l’absence de figures quand le precheck n’en a repéré aucune', () => {
    renderWithProviders(
      <PrecheckReview
        report={precheckReport({ figureCount: 0 })}
        filename="rapport.pdf"
        loading={false}
        submitting={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText(fr['precheck.metric.figuresNone'])).toBeDefined();
  });

  it('rappelle la portée de l’échantillon sur les tableaux et les figures d’un document long', () => {
    // Le precheck plafonne son analyse : « 3 tableaux » sur 194 pages veut dire
    // « 3 sur les 24 analysées ». Les deux comptages doivent porter la portée,
    // sinon l'utilisateur lit un total qui n'en est pas un — c'est exactement la
    // remontée faite sur `real-rfc9110.pdf`.
    renderWithProviders(
      <PrecheckReview
        report={precheckReport({
          pageCount: 194,
          analyzedPageCount: 24,
          tableCount: 3,
          figureCount: 5,
        })}
        filename="rapport.pdf"
        loading={false}
        submitting={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );

    expect(
      screen.getByText(fr['precheck.metric.tablesSampled'].replace('{count}', '24')),
    ).toBeDefined();
    expect(
      screen.getByText(fr['precheck.metric.figuresSampled'].replace('{count}', '24')),
    ).toBeDefined();
    // Le chiffre lui-même reste dit — la portée qualifie, elle ne masque pas.
    expect(
      screen.getByText(fr['precheck.metric.tablesValue'].replace('{count}', '3')),
    ).toBeDefined();
  });

  it('n’ajoute rien quand l’analyse a couvert tout le document', () => {
    renderWithProviders(
      <PrecheckReview
        report={precheckReport({ pageCount: 12, analyzedPageCount: 12, tableCount: 3 })}
        filename="rapport.pdf"
        loading={false}
        submitting={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText(fr['precheck.metric.tables'])).toBeDefined();
    expect(screen.getByText(fr['precheck.metric.figures'])).toBeDefined();
    expect(
      screen.queryByText(fr['precheck.metric.tablesSampled'].replace('{count}', '12')),
    ).toBeNull();
  });

  it('ne présente pas « 0 caractère » comme un défaut sur un document scanné', () => {
    // La couche texte d'un scan est vide par construction : afficher un chiffre
    // nul à côté de « Document scanné détecté » se lit comme une panne.
    renderWithProviders(
      <PrecheckReview
        report={precheckReport({
          isLikelyScanned: true,
          scannedPageRatio: 1,
          textCharacterCount: 0,
        })}
        filename="rapport.pdf"
        loading={false}
        submitting={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText(fr['precheck.metric.scanned'])).toBeDefined();
    expect(screen.getByText(fr['precheck.metric.textScanned'])).toBeDefined();
    expect(screen.getByText(fr['precheck.metric.textScannedDetail'])).toBeDefined();
    expect(screen.queryByText(fr['precheck.metric.textValue'].replace('{count}', '0'))).toBeNull();
  });

  it('garde le compte de caractères sur un scan partiel qui a bien du texte', () => {
    // `is_likely_scanned` peut être vrai avec une couche texte réelle (scan
    // partiel) : le chiffre est alors une information, pas un faux zéro.
    renderWithProviders(
      <PrecheckReview
        report={precheckReport({
          isLikelyScanned: true,
          scannedPageRatio: 0.5,
          textCharacterCount: 24_000,
        })}
        filename="rapport.pdf"
        loading={false}
        submitting={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText(fr['precheck.metric.text'])).toBeDefined();
    expect(screen.queryByText(fr['precheck.metric.textScanned'])).toBeNull();
  });

  it('ne présente pas non plus « 0 caractère » sur un document non scanné', () => {
    // Le zéro reste un constat légitime hors scan (PDF vide) : on n'y applique
    // pas la reformulation, sous peine d'annoncer une reconnaissance optique
    // qui n'aura pas lieu.
    renderWithProviders(
      <PrecheckReview
        report={precheckReport({ isLikelyScanned: false, textCharacterCount: 0 })}
        filename="rapport.pdf"
        loading={false}
        submitting={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText(fr['precheck.metric.textValue'].replace('{count}', '0'))).toBeDefined();
    expect(screen.queryByText(fr['precheck.metric.textScanned'])).toBeNull();
  });

  it('rend le bouton d’annulation à côté de la validation quand on le lui donne', async () => {
    const user = userEvent.setup();
    const onRequestCancel = vi.fn();

    renderWithProviders(
      <PrecheckReview
        report={precheckReport({ tableCount: 3 })}
        filename="rapport.pdf"
        loading={false}
        submitting={false}
        error={null}
        onSubmit={vi.fn()}
        onRequestCancel={onRequestCancel}
      />,
    );

    await user.click(screen.getByRole('button', { name: fr['job.cancel.action'] }));
    expect(onRequestCancel).toHaveBeenCalledOnce();
  });

  it('ne rend aucun bouton d’annulation quand l’appelant n’en fournit pas', () => {
    // `JobTracker` ne le fournit que pour un job encore annulable : l'absence
    // du callback doit se traduire par l'absence du bouton, pas par un bouton
    // inerte.
    renderWithProviders(
      <PrecheckReview
        report={precheckReport({ tableCount: 3 })}
        filename="rapport.pdf"
        loading={false}
        submitting={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.queryByRole('button', { name: fr['job.cancel.action'] })).toBeNull();
  });

  it('n’affiche jamais la sélection fine, quel que soit le document', () => {
    // Désactivée pour cette version (2026-09-06) : `laws` sur un PDF réel a
    // produit un fragment dépassant les limites du reranker en aval
    // (`CUDA out of memory`) — `advancedChunkMethodsFor` retourne toujours
    // `[]` désormais, y compris sur un PDF qui l'aurait proposée avant.
    renderWithProviders(
      <PrecheckReview
        report={precheckReport({ tableCount: 3 })}
        filename="rapport.pdf"
        loading={false}
        submitting={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText(fr['precheck.advanced.unavailable'])).toBeDefined();
    expect(screen.queryByText(fr['precheck.advanced.title'])).toBeNull();
  });

  it('ouvre le détail de l’analyse par défaut, avec ou sans signal notable', () => {
    // Toujours visible sans clic supplémentaire — l'utilisateur reste libre de
    // le replier lui-même (élément <details> natif), mais le défaut ne dépend
    // plus de la présence d'un scan/tableau/colonne.
    renderWithProviders(
      <PrecheckReview
        report={precheckReport({ tableCount: 0, twoColumnPageCount: 0, isLikelyScanned: false })}
        filename="rapport.pdf"
        loading={false}
        submitting={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );

    const details = screen.getByText(fr['precheck.report.detailsToggle']).closest('details');
    expect(details?.open).toBe(true);
  });

  it('montre la forme à venir tant que le rapport n’est pas lisible', () => {
    renderWithProviders(
      <PrecheckReview
        report={null}
        filename="rapport.pdf"
        loading
        submitting={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText(fr['precheck.analyzingTitle'])).toBeDefined();
    expect(screen.queryByRole('checkbox')).toBeNull();
  });
});
