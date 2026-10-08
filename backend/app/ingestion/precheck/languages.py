"""Détection des langues d'un document — étape CPU légère du precheck (ADR-003).

**Bibliothèque : `langdetect`** (port du détecteur par n-grammes de Google,
Apache-2.0, ~1 Mo de profils embarqués). Choisie parce qu'elle ne télécharge
aucun modèle, ne dépend d'aucun binaire natif et coûte moins d'une milliseconde
par segment : ADR-003 impose explicitement que la détection reste une étape CPU
légère, jamais une tâche GPU.

**Multilingue par découpage.** `langdetect` attribue *une* langue à un texte
donné : appelée sur un document entier, elle ne renverrait jamais que la langue
dominante. Le texte est donc découpé en segments d'environ
`SEGMENT_TARGET_CHARS` caractères (coupés sur des fins de ligne, une ligne vide
fermant toujours un segment), chaque segment est classé séparément, et les
langues sont pondérées par le volume de texte qu'elles couvrent. Un document
« un paragraphe en français puis un paragraphe en anglais » ressort donc bien
avec `["fr", "en"]`.

**Deux garde-fous, jamais un seul.** Un segment n'est écarté que s'il cumule
les deux symptômes du texte linguistiquement dégénéré : le classifieur hésite
(`detect_langs` rend la meilleure hypothèse sous `MIN_SEGMENT_CONFIDENCE`) *et*
le vocabulaire tourne en rond (`MIN_SEGMENT_LEXICAL_DIVERSITY`). Le `et` est le
cœur de la règle, et il est adossé à des mesures sur le corpus de test :

- un tableau de 35 lignes « REF-001 Article fictif 1 11 10.90 » produit des
  segments à `en:0.86` (« Article » est aussi un mot anglais) pour une diversité
  lexicale de 0,11 — le document est pourtant purement français ;
- un vrai paragraphe bilingue français/anglais produit lui aussi une confiance
  basse (`en:0.71`, les deux profils se disputent le segment), mais avec une
  diversité de 0,89. La confiance seule le supprimerait : ce serait perdre la
  seconde langue d'un document réellement bilingue, c'est-à-dire exactement ce
  que ce module doit détecter ;
- inversement, les lignes d'un tableau français de commandes sont répétitives
  (diversité 0,39) mais classées `fr:1.00` : la diversité seule les
  supprimerait sans raison. Chaque garde-fou couvre l'angle mort de l'autre.

**Limites assumées**, à connaître avant de se fier au résultat :

- granularité d'environ 300 caractères : une alternance de langue *à
  l'intérieur* d'un segment est attribuée à la langue dominante du segment ;
- une langue présente en dessous de `MIN_LANGUAGE_SHARE` du texte est écartée —
  une citation isolée n'est pas une langue du document ;
- les segments pauvres en lettres (tableaux de chiffres, références) sont
  ignorés : `langdetect` y est notoirement instable ;
- un texte à la fois répétitif *et* mal classé n'est rattaché à aucune langue :
  aucune correction n'est tentée, le segment cesse simplement de peser ;
- `langdetect` reste non déterministe par conception (échantillonnage interne) ;
  `DetectorFactory.seed` est donc figé à l'import pour que deux prechecks du
  même document produisent le même rapport.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Final

from langdetect import DetectorFactory, LangDetectException, detect_langs

# Figé au chargement du module : sans graine, `langdetect` peut classer
# différemment deux appels identiques, et le rapport de precheck deviendrait
# irreproductible d'une exécution à l'autre.
DetectorFactory.seed = 0

_logger = logging.getLogger(__name__)

SEGMENT_TARGET_CHARS: Final = 300
"""Taille visée d'un segment : au-delà de ~200 caractères, `langdetect` est très fiable."""

MIN_SEGMENT_ALPHA_CHARS: Final = 40
"""En deçà, un segment est trop pauvre en lettres pour être classé sérieusement."""

MIN_TOTAL_ALPHA_CHARS: Final = 20
"""Volume minimal de lettres dans tout le document pour tenter la moindre détection."""

MIN_LANGUAGE_SHARE: Final = 0.15
"""Part minimale du texte pour qu'une langue soit retenue (hors langue dominante)."""

MAX_ANALYZED_SEGMENTS: Final = 60
"""Plafond de segments classés : borne le coût sur un document volumineux."""

MAX_ANALYZED_CHARS: Final = 100_000
"""Plafond de texte examiné, pour la même raison."""

MIN_SEGMENT_CONFIDENCE: Final = 0.95
"""Probabilité minimale de la meilleure hypothèse pour qu'un segment soit *suspect*.

Sur un paragraphe monolingue ordinaire, `langdetect` rend 0,999 et plus. En
dessous de 0,95, plusieurs profils se disputent le segment — ce qui arrive
autant sur du texte dégénéré que sur un vrai passage bilingue : ce seuil ne
disqualifie donc rien à lui seul, il ouvre seulement l'examen de la diversité.
"""

MIN_SEGMENT_LEXICAL_DIVERSITY: Final = 0.35
"""Sous ce ratio de mots distincts, un segment mal classé n'est plus pris en compte.

Mesuré : 0,11 à 0,14 sur un tableau de références répétées, 0,39 et plus sur des
lignes de tableau françaises pourtant classées avec certitude, 0,58 et plus sur
tout texte rédigé. Le seuil est posé dans l'intervalle vide entre les deux.
"""

_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
"""Mot = au moins deux lettres consécutives. Codes et montants sont hors sujet ici."""


def _alpha_count(text: str) -> int:
    """Compte les lettres, seuls caractères informatifs pour un détecteur par n-grammes."""
    return sum(1 for character in text if character.isalpha())


def lexical_diversity(text: str) -> float:
    """Part de mots distincts dans le segment, entre 0 et 1 (1 si aucun mot).

    Un texte rédigé renouvelle son vocabulaire ; une ligne de tableau répétée
    trente fois non. La valeur 1 rendue sur un segment sans mot est le choix
    conservateur : ce garde-fou ne doit jamais écarter un segment de son propre
    chef, seulement confirmer une classification déjà douteuse.
    """
    words = [match.lower() for match in _WORD.findall(text)]
    if not words:
        return 1.0
    return len(set(words)) / len(words)


def split_segments(text: str) -> list[str]:
    """Découpe le texte en segments d'environ `SEGMENT_TARGET_CHARS` caractères.

    Une ligne vide ferme toujours le segment courant : c'est le seul indice de
    changement de bloc que laisse l'extraction PDF, et un changement de bloc est
    le lieu le plus probable d'un changement de langue.
    """
    segments: list[str] = []
    buffer: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal size
        if buffer:
            segments.append(" ".join(buffer))
            buffer.clear()
            size = 0

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        buffer.append(stripped)
        size += len(stripped)
        if size >= SEGMENT_TARGET_CHARS:
            flush()
    flush()
    return segments


def _normalize(code: str) -> str | None:
    """Ramène un code `langdetect` à un code ISO 639-1 (`zh-cn` → `zh`), ou l'écarte."""
    normalized = code.split("-", maxsplit=1)[0].lower()
    return normalized if len(normalized) == 2 and normalized.isalpha() else None


def detect_languages(text: str) -> list[str]:
    """Renvoie les codes ISO 639-1 du texte, par couverture décroissante.

    Ne lève jamais : un texte vide, trop court ou non textuel renvoie une liste
    vide — l'absence de langue détectée est une information valide du rapport,
    pas une erreur.
    """
    sample = text[:MAX_ANALYZED_CHARS]
    segments = [
        segment
        for segment in split_segments(sample)
        if _alpha_count(segment) >= MIN_SEGMENT_ALPHA_CHARS
    ]
    if not segments:
        # Document court (une ligne de titre, une cellule) : on tente le texte
        # entier plutôt que de renvoyer « aucune langue » par simple découpage.
        if _alpha_count(sample) < MIN_TOTAL_ALPHA_CHARS:
            return []
        segments = [sample]

    weights: dict[str, int] = defaultdict(int)
    for segment in segments[:MAX_ANALYZED_SEGMENTS]:
        try:
            # Hypothèses par probabilité décroissante. La liste peut être vide :
            # `langdetect` n'y garde que les langues au-dessus de 0,1, et un
            # texte parfaitement ambigu n'en laisse aucune.
            #
            # Un segment tout en capitales est classé en minuscules : mesuré sur
            # un titre d'acte juridique réel (« ACTE UNIFORME PORTANT... »),
            # `langdetect` le classe allemand à confiance 0,9999 tel quel,
            # français à confiance 0,9999 une fois mis en minuscules — un
            # artefact du modèle par n-grammes, pas une propriété du texte. Les
            # tout-capitales sont courantes dans les titres et intitulés
            # d'articles des documents juridiques/administratifs, un genre
            # central de ce produit. Restreint aux segments *entièrement* en
            # capitales (`str.isupper()`) : un segment à la casse normale, même
            # bruité (`REF-001 Article fictif...`), garde son texte tel quel —
            # `test_repetitive_table_does_not_invent_a_second_language` montre
            # qu'y mettre systématiquement tout en minuscules déplace au
            # contraire sa confiance au-dessus du seuil ci-dessous et
            # désactive le garde-fou de diversité lexicale qui le rattrapait.
            candidates = detect_langs(segment.lower() if segment.isupper() else segment)
        except LangDetectException:
            # Segment non classable (symboles, chiffres) : il ne pèse sur aucune
            # langue, mais il ne compromet pas le reste de la détection.
            continue
        if not candidates:
            continue
        best = candidates[0]
        if best.prob < MIN_SEGMENT_CONFIDENCE:
            diversity = lexical_diversity(segment)
            if diversity < MIN_SEGMENT_LEXICAL_DIVERSITY:
                # Classification douteuse sur un vocabulaire qui se répète : le
                # segment est écarté du calcul plutôt que d'inventer une langue.
                _logger.debug(
                    "Segment écarté (%s à %.2f, diversité %.2f) : texte trop répétitif",
                    best.lang,
                    best.prob,
                    diversity,
                )
                continue
        code = _normalize(best.lang)
        if code is not None:
            weights[code] += _alpha_count(segment)

    total = sum(weights.values())
    if total == 0:
        _logger.debug("Aucune langue détectable dans l'échantillon de %d caractères", len(sample))
        return []

    # Tri par poids décroissant puis par code, pour un rapport stable d'une
    # exécution à l'autre même en cas d'égalité parfaite.
    ranked = sorted(weights.items(), key=lambda item: (-item[1], item[0]))
    dominant = ranked[0][0]
    return [
        code for code, weight in ranked if code == dominant or weight / total >= MIN_LANGUAGE_SHARE
    ]
