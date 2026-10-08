/**
 * Branches de `suggestedDecision` — la proposition d'aiguillage d'ADR-031.
 *
 * ⚠️ Ces tests ne couvrent que la **fonction**, qui était déjà correcte quand le
 * mécanisme de suggestion ne marchait pas : le défaut vivait dans la façon dont
 * l'interface consommait sa valeur face à l'arrivée asynchrone du rapport
 * (`JobTracker.test.tsx`). Les garder seuls recréerait exactement l'angle mort
 * qu'ADR-032 cherche à supprimer.
 */
import { describe, expect, it } from 'vitest';

import { precheckReport } from '@/test/fixtures';

import {
  advancedChunkMethodsFor,
  parsePrecheckReport,
  readsBetterWithoutLayoutAnalysis,
  suggestedDecision,
} from './precheck';

describe('suggestedDecision', () => {
  it('propose l’analyse de mise en page sur un PDF à couche texte', () => {
    // Le cœur d'ADR-031 : ce n'est plus `is_likely_scanned` qui décide, c'est le
    // format. Un PDF non scanné, qui ne proposait rien avant, propose désormais.
    const report = precheckReport({ format: 'pdf', isLikelyScanned: false });

    expect(suggestedDecision(report)).toEqual({ optimizeForTables: false, heavyOcr: true });
  });

  it('propose aussi le découpage tabulaire quand l’analyse a vu des tableaux', () => {
    const report = precheckReport({ format: 'pdf', tableCount: 3 });

    expect(suggestedDecision(report)).toEqual({ optimizeForTables: true, heavyOcr: true });
  });

  it('ne propose pas l’analyse de mise en page sur un tableur', () => {
    // Un tableur part en `chunk_method=table` sans passer par l'analyse de mise
    // en page : la proposer ne déciderait de rien (ADR-031, §Décision).
    const report = precheckReport({ format: 'spreadsheet', tableCount: 4 });

    expect(suggestedDecision(report)).toEqual({ optimizeForTables: true, heavyOcr: false });
  });

  it('ne propose rien sur une image, que la façade classe hors de l’union connue', () => {
    // Rapport tel que `_unanalyzed` le construit réellement
    // (`backend/app/ingestion/precheck/analyzer.py`) : supporté, `is_likely_scanned`
    // vrai par propriété du format, compteurs neutres parce que rien n'a été lu.
    // `format: "image"` est absent de `PrecheckFormat` côté client, donc
    // normalisé en `unsupported` — dans les deux cas, pas `pdf`, donc aucune
    // proposition : les deux options sont des no-op documentés sur une image.
    const report = parsePrecheckReport({
      supported: true,
      format: 'image',
      size_bytes: 42_000,
      page_count: null,
      analyzed_page_count: 0,
      is_likely_scanned: true,
      scanned_page_ratio: null,
      error: null,
    });

    expect(report?.format).toBe('unsupported');
    expect(suggestedDecision(report)).toEqual({ optimizeForTables: false, heavyOcr: false });
  });

  it('ne propose rien sur un rapport dégradé, même s’il annonce un PDF', () => {
    const report = precheckReport({
      supported: false,
      format: 'pdf',
      tableCount: 5,
      error: { code: 'extraction_failed', message: 'Extraction impossible.' },
    });

    expect(suggestedDecision(report)).toEqual({ optimizeForTables: false, heavyOcr: false });
  });

  it('ne propose rien en l’absence de rapport', () => {
    expect(suggestedDecision(null)).toEqual({ optimizeForTables: false, heavyOcr: false });
  });

  it('ne propose plus l’analyse de mise en page sur un PDF à colonnes sans tableau', () => {
    // ADR-042 : c'est exactement le document où la proposition d'ADR-031 se
    // retourne contre l'utilisateur — l'analyse de mise en page y lit les
    // colonnes ligne par ligne (taux de basculement mesuré 0,283 contre 0,057
    // sans elle, pour une lecture correcte à 0,020).
    const report = precheckReport({ format: 'pdf', twoColumnPageCount: 1, tableCount: 0 });

    expect(suggestedDecision(report)).toEqual({ optimizeForTables: false, heavyOcr: false });
  });

  it('la propose quand même quand le document à colonnes porte aussi des tableaux', () => {
    // ADR-043 a refermé l'arbitrage qu'ADR-042 laissait ouvert : ce document
    // reçoit désormais un troisième moteur de mise en page qui respecte les
    // colonnes *et* reconnaît les tableaux (`chunking_profile.py`). Ce choix se
    // fait côté backend, à partir des mêmes deux compteurs ; l'heuristique
    // d'interface, elle, ne bouge pas — elle propose toujours l'analyse.
    const report = precheckReport({ format: 'pdf', twoColumnPageCount: 4, tableCount: 16 });

    expect(suggestedDecision(report)).toEqual({ optimizeForTables: true, heavyOcr: true });
  });
});

describe('readsBetterWithoutLayoutAnalysis', () => {
  it('ne se déclenche que sur un PDF à colonnes et sans tableau', () => {
    expect(
      readsBetterWithoutLayoutAnalysis(precheckReport({ format: 'pdf', twoColumnPageCount: 2 })),
    ).toBe(true);
    expect(
      readsBetterWithoutLayoutAnalysis(
        precheckReport({ format: 'pdf', twoColumnPageCount: 2, tableCount: 1 }),
      ),
    ).toBe(false);
    expect(
      readsBetterWithoutLayoutAnalysis(precheckReport({ format: 'pdf', twoColumnPageCount: 0 })),
    ).toBe(false);
  });

  it('reste faux hors PDF et sans rapport', () => {
    // Un tableur n'a pas de mise en page, et le backend y rapporte toujours 0 :
    // la garde de format évite qu'une valeur inattendue de l'API n'y déclenche
    // un texte sans objet.
    expect(
      readsBetterWithoutLayoutAnalysis(
        precheckReport({ format: 'spreadsheet', twoColumnPageCount: 3 }),
      ),
    ).toBe(false);
    expect(readsBetterWithoutLayoutAnalysis(null)).toBe(false);
  });
});

describe('advancedChunkMethodsFor', () => {
  it('ne propose jamais de sélection fine, quel que soit le document', () => {
    // Désactivé pour cette version (2026-09-06) : `laws` sur un PDF réel a
    // produit un fragment de 41 264 caractères, jamais mesuré aux limites du
    // reranker en aval — `CUDA out of memory` sur un lot de reranking le
    // contenant. Le backend rejette aussi `chunk_method_override` (422).
    expect(advancedChunkMethodsFor(precheckReport({ format: 'pdf' }), 'rgpd.pdf')).toEqual([]);
    expect(
      advancedChunkMethodsFor(
        precheckReport({
          supported: false,
          format: 'unsupported',
          pageCount: null,
          error: { code: 'unsupported_format', message: 'Format non analysé.' },
        }),
        'guide.md',
      ),
    ).toEqual([]);
    expect(advancedChunkMethodsFor(null, 'd.md')).toEqual([]);
  });
});
