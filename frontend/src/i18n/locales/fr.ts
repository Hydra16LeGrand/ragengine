/**
 * Dictionnaire FR — SOURCE DE VÉRITÉ des libellés d'interface.
 *
 * CONTRIBUTING.md §7 : aucune chaîne d'UI en dur dispersée dans les composants.
 * Toute nouvelle chaîne visible par l'utilisateur s'ajoute ici en premier ;
 * les autres locales doivent alors satisfaire le type `Dictionary` (une clé
 * manquante devient une erreur de compilation).
 *
 * Convention de clés : `domaine.sous-domaine.element`, en camelCase.
 * Interpolation : `{nomDuParametre}`.
 */
export const fr = {
  'app.name': '{appName}',
  'app.tagline': 'Plateforme RAG d’entreprise souveraine, modulaire et clé en main.',
  'app.skipToContent': 'Aller au contenu principal',

  'nav.home': 'Accueil',
  'nav.primaryLabel': 'Navigation principale',
  'nav.menuOpen': 'Ouvrir le menu',
  'nav.menuClose': 'Fermer le menu',
  'nav.knowledgeBases': 'Bases de connaissances',
  'nav.evaluationsSummary': 'Évaluations',
  'nav.developers': 'Développeurs',
  'nav.settings': 'Paramètres',
  'nav.login': 'Se connecter',
  'nav.logout': 'Se déconnecter',

  'theme.label': 'Thème',
  'theme.light': 'Clair',
  'theme.dark': 'Sombre',
  'theme.system': 'Système',

  /* --- Formulaires, éléments transverses ---------------------------------- */
  'form.optional': 'Facultatif',
  'form.cancel': 'Annuler',
  'form.close': 'Fermer',
  'toast.dismiss': 'Masquer la notification',

  /* --- Authentification --------------------------------------------------- */
  'auth.modeLabel': 'Connexion ou inscription',
  'auth.mode.login': 'Connexion',
  'auth.mode.register': 'Créer un compte',
  'auth.login.title': 'Content de vous revoir',
  'auth.login.subtitle': 'Connectez-vous pour retrouver vos bases de connaissances.',
  'auth.register.title': 'Créer un compte',
  'auth.register.subtitle':
    'Un compte vous permet de créer des bases, d’y verser des documents et d’obtenir des clés API.',
  'auth.field.displayName': 'Nom affiché',
  'auth.field.displayNamePlaceholder': 'Camille Dupont',
  'auth.field.email': 'Adresse e-mail',
  'auth.field.emailPlaceholder': 'vous@organisation.fr',
  'auth.field.password': 'Mot de passe',
  'auth.field.passwordHint': 'Au moins 8 caractères.',
  'auth.field.confirmPassword': 'Confirmer le mot de passe',
  'auth.field.confirmPasswordMismatch': 'Les deux mots de passe ne correspondent pas.',
  'auth.submit.login': 'Se connecter',
  'auth.submit.register': 'Créer mon compte',

  /* --- Bases de connaissances : liste ------------------------------------- */
  'kb.eyebrow': 'Bases de connaissances',
  'kb.list.title': 'Vos bases de connaissances',
  'kb.list.titleAnonymous': 'Bases de connaissances publiques',
  'kb.list.description':
    'Chaque base réunit un corpus, ses modèles d’embedding et de reranking, et ses règles d’accès.',
  'kb.list.descriptionAnonymous':
    'Les corpus que ce déploiement expose publiquement, consultables sans compte.',
  'kb.list.create': 'Nouvelle base',
  'kb.list.shared': 'Partagé',
  'kb.list.errorTitle': 'Impossible de charger les bases',
  'kb.list.anonymousTitle': 'Vous consultez {appName} sans être connecté',
  'kb.list.anonymousDescription':
    'Seules les bases publiques sont visibles, en lecture seule. Connectez-vous pour créer vos propres bases et gérer leurs accès.',

  'kb.column.base': 'Base',
  'kb.column.visibility': 'Visibilité',
  'kb.column.models': 'Briques',
  'kb.column.documents': 'Documents',
  'kb.column.updated': 'Mise à jour',

  'kb.visibility.private': 'Privée',
  'kb.visibility.public': 'Publique',

  'kb.role.owner': 'Propriétaire',
  'kb.role.editor': 'Éditeur',
  'kb.role.viewer': 'Lecteur',

  'kb.model.embedding': 'Emb',
  'kb.model.reranker': 'Rrk',
  'kb.model.embeddingFull': 'Modèle d’embedding',
  'kb.model.rerankerFull': 'Reranker',
  'kb.model.none': 'Aucun',

  'kb.empty.title': 'Aucune base pour l’instant',
  'kb.empty.description':
    'Une base de connaissances est le corpus interrogé par vos recherches. Créez la première pour commencer à y verser des documents.',
  'kb.empty.step1': 'Créer une base — votre corpus, ses briques d’IA.',
  'kb.empty.step2': 'Déposer des documents — PDF, Word, Excel, images…',
  'kb.empty.step3': 'Interroger — recherche ou chat, réponses citées.',
  'kb.empty.anonymousTitle': 'Aucune base publique à afficher',
  'kb.empty.anonymousDescription':
    'Ce déploiement n’expose aucune base publique pour le moment. Connectez-vous pour accéder aux vôtres.',

  'kb.backToList': 'Toutes les bases',
  'kb.nav.previous': 'Base précédente',
  'kb.nav.next': 'Base suivante',
  'kb.nav.previousTo': 'Précédente : {name}',
  'kb.nav.nextTo': 'Suivante : {name}',
  'kb.nav.boundaryReached':
    'Limite de la page chargée — repassez par la liste pour aller plus loin.',

  /* --- Création / édition -------------------------------------------------- */
  'kb.create.title': 'Nouvelle base de connaissances',
  'kb.create.description':
    'Déclarez le corpus et les briques d’inférence qui le serviront. Les documents seront versés ensuite.',
  'kb.create.submit': 'Créer la base',
  'kb.create.successTitle': 'Base créée',
  'kb.create.successDescription': '« {name} » est prête à recevoir des documents.',

  'kb.edit.submit': 'Enregistrer les modifications',
  'kb.edit.successTitle': 'Modifications enregistrées',
  'kb.edit.successDescription': '« {name} » a été mise à jour.',

  'kb.form.errorTitle': 'La base n’a pas pu être enregistrée',
  'kb.form.section.identity': 'Identité',
  'kb.form.identityTitle': 'Nom et accès',
  'kb.form.identityDescription':
    'Ce qui identifie la base dans l’interface, dans l’API et pour vos utilisateurs.',
  'kb.form.name': 'Nom',
  'kb.form.namePlaceholder': 'Documentation juridique',
  'kb.form.slug': 'Identifiant court',
  'kb.form.slugHint':
    'Déduit du nom, modifiable. Minuscules, chiffres et tirets — c’est lui qui apparaît dans les URL.',
  'kb.form.description': 'Description',
  'kb.form.descriptionHint': 'Ce que contient le corpus, pour ceux qui le découvriront.',
  'kb.form.descriptionPlaceholder':
    'Contrats-cadres, avenants et jurisprudence applicable aux marchés publics.',
  'kb.form.organization': 'Organisation propriétaire',
  'kb.form.organizationDefault': 'Aucune',
  'kb.form.organizationHint':
    'L’espace de travail qui administre cette base (membres, facturation) — sans lien avec son ' +
    'contenu. « Aucune » suffit dans la grande majorité des cas — changez uniquement si cette ' +
    'base doit appartenir à une autre de vos organisations.',
  'kb.form.organizationMissing':
    'Vous n’appartenez à aucune organisation : créez-en une avant de pouvoir créer une base.',
  'kb.form.visibility': 'Visibilité',
  'kb.form.visibilityPrivate': 'Seuls vous et les membres que vous désignez y accèdent.',
  'kb.form.visibilityPublic': 'Toute personne, même non connectée, peut consulter cette base.',

  'kb.form.section.providers': 'Briques d’inférence',
  'kb.form.providersTitle': 'Modèles servant cette base',
  'kb.form.providersDescription':
    'Choisis parmi les briques réellement servies par ce déploiement. Les briques indisponibles ne sont pas sélectionnables.',
  'kb.form.embedding': 'Modèle d’embedding',
  'kb.form.embeddingHint': 'Il vectorise les passages à l’indexation comme à la recherche.',
  'kb.form.embeddingLocked':
    'Le modèle d’embedding est figé après la création : en changer invaliderait tous les vecteurs déjà indexés.',
  'kb.form.reranker': 'Reranker',
  'kb.form.rerankerHint':
    'Réordonne les passages retenus. Facultatif — il s’active requête par requête.',
  'kb.form.dimensionsSuffix': ' dim.',
  'kb.form.local': 'local',
  'kb.form.remote': 'distant',
  'kb.form.unavailable': 'indisponible',

  'kb.form.section.documents': 'Documents',
  'kb.form.documentsTitle': 'Déposer des documents (facultatif)',
  'kb.form.documentsDescription':
    'Choisissez ou glissez vos fichiers dès maintenant : ils restent en attente ici, puis partent juste après la création de la base — inutile de revenir sur cet écran ensuite.',
  'kb.form.documentsPendingHint':
    'Envoyés automatiquement une fois la base créée. Vous pourrez suivre et reprendre chacun depuis sa fiche.',

  'kb.form.advancedTitle': 'Options avancées',
  'kb.form.advancedDescription':
    'Observabilité, évaluation et réglages fins des briques. Les valeurs par défaut conviennent à la plupart des cas.',
  'kb.form.observability': 'Observabilité',
  'kb.form.observabilityHint': 'Trace les recherches de cette base vers Langfuse.',
  'kb.form.observabilityUnavailable':
    'Aucun fournisseur d’observabilité n’est configuré sur ce déploiement : l’option restera sans effet.',
  'kb.form.evaluation': 'Évaluation',
  'kb.form.evaluationHint': 'Autorise la génération de jeux d’évaluation à partir de ce corpus.',
  'kb.form.providerConfig': 'Configuration des briques (JSON)',
  'kb.form.providerConfigHint':
    'Objet JSON transmis tel quel aux briques. Laissez vide pour conserver les réglages par défaut.',
  'kb.form.configInvalid': 'JSON invalide : vérifiez la syntaxe.',
  'kb.form.configNotObject': 'La configuration doit être un objet JSON.',

  /* --- Détail -------------------------------------------------------------- */
  'kb.detail.section.configuration': 'Configuration',
  'kb.detail.configurationTitle': 'Composition de la base',
  'kb.detail.configurationDescription':
    'Les briques et repères techniques de ce corpus, tels que la façade les expose.',
  'kb.detail.section.access': 'Accès',
  'kb.detail.section.documents': 'Corpus',
  'kb.detail.section.quality': 'Qualité',
  'kb.detail.edit': 'Modifier',
  'kb.detail.delete': 'Supprimer',
  'kb.detail.created': 'Créée le',
  'kb.detail.updated': 'Modifiée le',
  'kb.detail.engineDataset': 'Jeu de données moteur',
  'kb.detail.engineDatasetPending': 'Créé au premier versement',
  'kb.detail.notFoundTitle': 'Base introuvable',
  'kb.detail.notFoundDescription':
    'Cette base n’existe pas, ou elle n’est pas visible avec vos accès actuels.',
  'kb.detail.errorTitle': 'Impossible d’afficher cette base',

  'kb.delete.title': 'Supprimer cette base ?',
  'kb.delete.description': '« {name} » et sa configuration seront définitivement supprimées.',
  'kb.delete.warning':
    'Cette action est irréversible : {count} document(s) et leur indexation seront perdus.',
  'kb.delete.confirm': 'Supprimer définitivement',
  'kb.delete.successTitle': 'Base supprimée',
  'kb.delete.errorTitle': 'La suppression a échoué',

  /* --- Membres -------------------------------------------------------------- */
  'kb.members.title': 'Membres et rôles',
  'kb.members.description':
    'Le propriétaire décide qui consulte, qui alimente et qui administre cette base.',
  'kb.members.you': 'Vous',
  'kb.members.lookupLabel': 'Identifiant de l’utilisateur',
  'kb.members.lookupHint': 'UUID du compte à ajouter.',
  'kb.members.lookupAction': 'Rechercher',
  'kb.members.roleLabel': 'Rôle',
  'kb.members.notMember': 'Cet utilisateur n’est pas encore membre de la base.',
  'kb.members.alreadyListed': 'Cet utilisateur figure déjà dans l’annuaire ci-dessus.',
  'kb.members.grant': 'Accorder l’accès',
  'kb.members.remove': 'Retirer',
  'kb.members.grantedTitle': 'Rôle enregistré',
  'kb.members.grantedDescription': '{name} a désormais le rôle « {role} » sur cette base.',
  'kb.members.removedTitle': 'Membre retiré',
  'kb.members.removeConfirmTitle': 'Retirer ce membre ?',
  'kb.members.removeConfirmDescription':
    '{name} perdra tout accès à cette base. Vous pourrez le lui rendre en l’ajoutant à nouveau.',
  'kb.members.removeConfirm': 'Retirer le membre',
  'kb.members.errorTitle': 'Action impossible',

  /* --- Documents ------------------------------------------------------------ */
  'kb.documents.title': 'Documents indexés',
  'kb.documents.description':
    'Les pièces versées à ce corpus et leur état d’indexation, telles que la façade les connaît.',
  'kb.documents.emptyTitle': 'Aucun document dans cette base',
  'kb.documents.emptyDescription':
    'Le corpus est vide. Déposez vos premiers fichiers ci-dessus : chacun sera analysé avant que vous ne décidiez comment l’indexer.',
  'kb.documents.emptyAction': 'Déposer un document',
  'kb.documents.pages': '{count} page(s)',
  'kb.documents.chunkCount': '{count} fragment(s)',
  'kb.documents.open': 'Ouvrir',
  'kb.documents.qualityWarning': 'Qualité à vérifier',
  'kb.documents.qualityWarningHint':
    'Le document est indexé, mais l’extraction a produit un résultat suspect — ouvrez « Voir la cause » pour le détail.',

  // Consultation de la cause d'un échec ou d'un avertissement, après coup :
  // jusqu'ici elle n'existait que dans le suivi de la session de versement en
  // cours (cf. l'analyse interne des tests manuels §3).
  'kb.documents.cause.show': 'Voir la cause',
  'kb.documents.cause.hide': 'Masquer la cause',
  'kb.documents.cause.region': 'Cause du résultat d’ingestion de « {name} »',
  'kb.documents.cause.none':
    'Le moteur n’a enregistré ni cause d’échec ni avertissement pour ce document.',
  'kb.documents.delete': 'Supprimer',
  'kb.documents.deleteConfirmTitle': 'Supprimer ce document ?',
  'kb.documents.deleteConfirmDescription':
    '« {name} » sera retiré du moteur d’ingestion, de l’index et du stockage. Cette action est irréversible.',
  'kb.documents.deleteConfirm': 'Supprimer définitivement',
  'kb.documents.deletedTitle': 'Document supprimé',
  'kb.documents.deleteErrorTitle': 'Suppression impossible',
  'kb.documents.loadMore': 'Charger plus de documents',

  // Thème : organisation par sujet au sein d'une base (saisi par l'utilisateur,
  // jamais déduit — contrairement aux langues détectées au precheck).
  'kb.documents.theme.none': 'Aucun thème',
  'kb.documents.theme.edit': 'Modifier le thème',
  'kb.documents.theme.placeholder': 'Ex. juridique, finance…',
  'kb.documents.theme.save': 'Enregistrer',
  'kb.documents.theme.cancel': 'Annuler',
  'kb.documents.theme.saveErrorTitle': 'Thème non enregistré',
  'kb.documents.theme.filterLabel': 'Filtrer par thème',
  'kb.documents.theme.filterAll': 'Tous les thèmes',

  'kb.documents.chunks.action': 'Voir les fragments',
  'kb.documents.chunks.title': 'Fragments indexés',
  'kb.documents.chunks.summary': '{count} fragment(s) · méthode « {method} »',
  'kb.documents.chunks.methodUnknown': 'non déterminée',
  // Libellés compréhensibles pour les noms internes RAGFlow (`chunk_method`) —
  // « naive » notamment ne signifie pas « sans mise en page » mais désigne le
  // parseur généraliste, celui qui respecte `layout_recognize` et porte le
  // parent/enfant, par opposition aux méthodes spécialisées (QA, tableau…).
  'kb.documents.chunks.method.naive': 'Général (mise en page adaptative)',
  'kb.documents.chunks.method.table': 'Tableau',
  'kb.documents.chunks.method.picture': 'Image',
  'kb.documents.chunks.method.qa': 'Questions/réponses',
  'kb.documents.chunks.method.resume': 'CV',
  'kb.documents.chunks.method.manual': 'Manuel (sections numérotées)',
  'kb.documents.chunks.method.book': 'Livre (chapitres)',
  'kb.documents.chunks.method.laws': 'Texte juridique (articles)',
  'kb.documents.chunks.method.presentation': 'Présentation (diapositives)',
  'kb.documents.chunks.method.paper': 'Article scientifique',
  'kb.documents.chunks.method.email': 'E-mail',
  'kb.documents.chunks.method.one': 'Document entier (bloc unique)',
  'kb.documents.chunks.method.tag': 'Étiquettes',
  'kb.documents.chunks.errorTitle': 'Fragments indisponibles',
  'kb.documents.chunks.emptyTitle': 'Aucun fragment',
  'kb.documents.chunks.emptyDescription':
    'L’indexation a abouti sans produire le moindre fragment — le garde-fou qualité l’a déjà signalé sur ce document.',
  'kb.documents.chunks.number': 'Fragment {number}',
  'kb.documents.chunks.pageSingle': 'Page {page}',
  'kb.documents.chunks.pageRange': 'Pages {start}–{end}',
  'kb.documents.chunks.keywords': 'Mots-clés',
  'kb.documents.chunks.questions': 'Questions générées',
  'kb.documents.chunks.disabled': 'Désactivé',
  'kb.documents.chunks.parent': 'Passage restitué',
  'kb.documents.chunks.parentHint':
    'Ce passage n’est pas interrogé directement : c’est lui que la recherche renvoie dès qu’un de ses fragments de repérage correspond.',
  'kb.documents.chunks.child': 'Repérage',
  'kb.documents.chunks.childHint':
    'Fragment court servant à localiser l’information. La recherche ne le renvoie pas tel quel : elle remonte au passage qui le contient.',
  'kb.documents.chunks.parentChildNote':
    'Ce document a été indexé avec la granularité affinée. La liste contient donc deux natures de fragments : de courts fragments de « repérage », qui servent à localiser l’information, et les « passages restitués » qui les contiennent — ce sont ces derniers, plus longs, que la recherche renvoie réellement. Voir de très courts fragments ici est donc attendu, et non un découpage raté.',
  'kb.documents.chunks.parentsOnly': 'Afficher uniquement les passages restitués',
  'kb.documents.chunks.parentsOnlyHint':
    'Masque les fragments de repérage sur cette page — la pagination reste celle de l’ensemble complet.',
  'kb.documents.chunks.noParentsFoundTitle': 'Aucun passage restitué',
  'kb.documents.chunks.noParentsFoundDescription':
    'Ce document ne contient aucun fragment parent. Décochez le filtre pour voir les fragments de repérage.',
  'kb.documents.chunks.pagination': 'Page {page} / {totalPages}',
  'kb.documents.chunks.previous': 'Précédent',
  'kb.documents.chunks.next': 'Suivant',

  'document.status.pending': 'En attente',
  'document.status.processing': 'Traitement',
  'document.status.awaitingReview': 'Validation requise',
  'document.status.indexed': 'Indexé',
  'document.status.failed': 'Échec',
  'document.status.cancelled': 'Annulé',

  /* --- Évaluations ---------------------------------------------------------- */
  'kb.evaluations.title': 'Évaluations du corpus',
  'kb.evaluations.description':
    'Un jeu de questions est généré depuis vos propres documents, puis chaque réponse est notée par un modèle juge. De quoi mesurer ce que la base répond vraiment.',
  'kb.evaluations.launch': 'Lancer une évaluation',

  'kb.evaluations.disabledTitle': 'L’évaluation est désactivée sur cette base',
  'kb.evaluations.disabledDescription':
    'Un run génère des questions et les fait juger par un modèle externe : la dépense n’est jamais engagée sans y avoir consenti. Activez « Évaluation » dans les options avancées de la base pour l’autoriser.',

  'kb.evaluations.emptyTitle': 'Aucune évaluation lancée',
  'kb.evaluations.emptyDescription':
    'Lancez un premier run pour savoir si le corpus répond juste, et si les bons documents remontent.',
  'kb.evaluations.emptyDescriptionReadOnly':
    'Les résultats apparaîtront ici. Lancer un run demande le rôle éditeur : il consomme du budget de génération.',

  'kb.evaluations.judgeScore': 'Note du juge',
  'kb.evaluations.recall': 'Rappel',
  'kb.evaluations.scoreUnavailable': 'non mesuré',
  'kb.evaluations.items': '{count} question(s)',
  'kb.evaluations.itemsEmpty': 'Ce run n’a produit aucune question.',

  'kb.evaluations.item.index': 'Question {index}',
  'kb.evaluations.item.reference': 'Réponse de référence',
  'kb.evaluations.item.generated': 'Réponse de la base',
  'kb.evaluations.item.rationale': 'Justification du juge',
  'kb.evaluations.item.rank': 'Rang {rank}',
  'kb.evaluations.item.rankMissing': 'Hors du top-k',
  'kb.evaluations.item.rankTitle':
    'Position du document source dans les passages retrouvés pour cette question.',
  'kb.evaluations.item.judge': 'Juge {score}',

  'kb.evaluations.dialogTitle': 'Lancer une évaluation',
  'kb.evaluations.dialogDescription':
    'Une question est générée par document échantillonné, la base y répond, puis un juge note l’écart avec la réponse de référence.',
  'kb.evaluations.sampleSize': 'Documents échantillonnés',
  'kb.evaluations.sampleSizeHint':
    'Jusqu’à 20 : run immédiat, une question générée par document. Au-delà : exécuté en arrière-plan (voir son avancement dans l’historique ci-dessous).',
  'kb.evaluations.sampleSizeHintAsync':
    'Au-delà de 20 documents, le run s’exécute en arrière-plan : la modale se ferme aussitôt, suivez son avancement dans l’historique ci-dessous.',
  'kb.evaluations.model': 'Modèle générateur et juge',
  // Remplace l'un ou l'autre selon que le fournisseur sélectionné dispose
  // d'une clé personnelle déclarée pour le compte qui lance le run
  // (`settings.llmKeys`) : sans elle, ce choix est silencieusement remplacé
  // par un modèle du déploiement (addendum 2026-09-21 à ADR-054,
  // `resolve_llm_funding`, réutilisée telle quelle par `EvaluationService`).
  'kb.evaluations.modelAppliesHint':
    'Clé personnelle {vendor} configurée : ce choix s’applique. Le même modèle génère les questions et note les réponses.',
  'kb.evaluations.modelOverriddenHint':
    'Sans clé personnelle {vendor}, un modèle du déploiement est utilisé à la place de ce choix. Le même modèle génère les questions et note les réponses.',
  'kb.evaluations.noProvider':
    'Ce déploiement ne dispose d’aucun fournisseur de génération configuré. L’évaluation en a besoin : sans clé d’API, aucun run n’est possible.',
  'kb.evaluations.costWarning':
    'Chaque question déclenche trois appels au modèle (génération, réponse, notation) et consomme du budget réel. Commencez petit.',
  'kb.evaluations.submit': 'Lancer le run',

  'kb.evaluations.runningTitle': 'Évaluation en cours…',
  'kb.evaluations.runningHint':
    '{count} document(s) échantillonné(s) : génération des questions, réponses, puis notation. L’opération est synchrone et peut prendre plusieurs dizaines de secondes — restez sur cet écran.',
  'kb.evaluations.submittingTitle': 'Envoi du run…',
  'kb.evaluations.submittingHint':
    '{count} document(s) échantillonné(s) : le run s’exécute en arrière-plan, cet envoi ne prend qu’un instant.',
  'kb.evaluations.successTitle': 'Évaluation terminée',
  'kb.evaluations.successDescription': '{count} question(s) générée(s) et notée(s).',
  'kb.evaluations.acceptedTitle': 'Run accepté',
  'kb.evaluations.acceptedDescription':
    '{count} document(s) échantillonné(s), exécution en arrière-plan. Suivez son avancement dans l’historique ci-dessous.',

  'kb.evaluations.status.pending': 'En cours',
  'kb.evaluations.status.failed': 'Échec',

  'kb.evaluations.itemsPendingTitle': 'Génération en cours…',
  'kb.evaluations.itemsPendingHint':
    'Ce run s’exécute en arrière-plan ; ses questions apparaîtront ici toutes ensemble, à sa résolution.',
  'kb.evaluations.itemsFailed':
    'Ce run a échoué avant de produire la moindre question. Relancez-le depuis « Lancer une évaluation ».',

  'kb.evaluations.runErrorTitle': 'L’évaluation n’a pas pu être lancée',
  'kb.evaluations.hint403':
    'Lancer un run demande le rôle éditeur sur cette base : demandez-le à son propriétaire.',
  'kb.evaluations.hint409':
    'Aucun document indexé n’est disponible. Versez et indexez au moins un document avant d’évaluer.',
  'kb.evaluations.hint422':
    'Vérifiez que l’évaluation est activée sur la base et que le modèle choisi est bien configuré côté déploiement — ou réduisez le nombre de documents échantillonnés si le plafond asynchrone de ce déploiement est dépassé.',

  /* --- Évaluations : vue globale cross-KB (Phase 12, Étape 5) --------------- */
  'evaluationsSummary.eyebrow': 'Vue d’ensemble',
  'evaluationsSummary.title': 'Évaluations',
  'evaluationsSummary.description':
    'Recall et note de juge agrégés sur toutes les bases auxquelles vous avez accès — pas seulement celle que vous consultez.',

  'evaluationsSummary.overallTitle': 'Dans l’ensemble',
  'evaluationsSummary.overallDescription':
    'Calculé sur l’ensemble des questions de toutes les bases mesurées, jamais une moyenne des bases prises séparément.',
  'evaluationsSummary.statBases': 'Bases mesurées',
  'evaluationsSummary.statRuns': 'Runs',
  'evaluationsSummary.statItems': 'Questions',
  'evaluationsSummary.recallAtK': 'Rappel@{k}',
  'evaluationsSummary.mrr': 'MRR',
  'evaluationsSummary.judgeScore': 'Note du juge',
  'evaluationsSummary.scoreUnavailable': 'non mesuré',

  'evaluationsSummary.tableTitle': 'Détail par base',
  'evaluationsSummary.tableDescription':
    'Une ligne par base mesurée — cliquez pour ouvrir son historique complet.',
  'evaluationsSummary.rowRuns': '{count} run(s)',
  'evaluationsSummary.rowItems': '{count} question(s)',

  'evaluationsSummary.emptyTitle': 'Aucune évaluation mesurée pour l’instant',
  'evaluationsSummary.emptyDescription':
    'Lancez une évaluation depuis une base pour voir sa mesure apparaître ici, et dans la vue d’ensemble.',

  /* --- Ingestion : versement ------------------------------------------------ */
  'ingest.section': 'Versement',
  'ingest.title': 'Verser des documents',
  'ingest.description':
    'Chaque fichier est analysé avant indexation. Vous validez ensuite la façon de le découper, document par document.',
  'ingest.dropzone.label': 'Déposez vos documents ici',
  'ingest.dropzone.hint':
    'PDF, Word, Excel, PowerPoint, CSV/texte, e-mails et images (OCR). Plusieurs fichiers à la fois : chacun suit son propre parcours.',
  'ingest.dropzone.browse': 'Parcourir vos fichiers',

  /* --- Ingestion : mise en attente avant confirmation ------------------------ */
  'ingest.staging.title': 'Fichiers en attente',
  'ingest.staging.launch': 'Lancer l’ingestion',
  'ingest.staging.replaces': 'Remplacera « {name} »',

  'ingest.queue.title': 'File de versement',
  'ingest.queue.count': '{count} fichier(s)',
  'ingest.queue.clear': 'Retirer les terminés',
  'ingest.item.remove': 'Retirer de la file',
  'ingest.item.retry': 'Réessayer',
  'ingest.item.reviewAction': 'Régler l’indexation',
  'ingest.item.replacesNote': 'Remplace « {name} »',

  'ingest.state.pending': 'En attente',
  'ingest.state.deleting': 'Suppression de l’ancienne version',
  'ingest.state.deleteFailed': 'Suppression échouée',
  'ingest.state.uploading': 'Envoi en cours',
  'ingest.state.uploadFailed': 'Envoi échoué',
  'ingest.state.analyzing': 'Analyse préalable',
  'ingest.state.awaitingReview': 'Votre validation est requise',
  'ingest.state.processing': 'Indexation en cours',
  'ingest.state.completed': 'Indexé',
  'ingest.state.failed': 'Échec',
  'ingest.state.cancelled': 'Annulé',

  'ingest.uploadErrorTitle': 'Ce fichier n’a pas pu être envoyé',
  'ingest.deleteFailedTitle': 'L’ancienne version n’a pas pu être supprimée',
  'ingest.trackingErrorTitle': 'Suivi indisponible',
  'ingest.trackingErrorDescription':
    'Le document a bien été reçu, mais l’état de son traitement n’a pas pu être relu.',
  'ingest.noJobTitle': 'Document reçu, sans job de suivi',
  'ingest.noJobDescription':
    'La façade a accepté le fichier sans rattacher de job d’ingestion. Rechargez la page pour retrouver son état.',
  'ingest.completedTitle': 'Document indexé',
  'ingest.completedDescription': '« {name} » est interrogeable depuis le Playground.',
  'ingest.failedTitle': 'L’ingestion a échoué',
  'ingest.error.retryable': 'Une nouvelle tentative a une chance d’aboutir.',
  'ingest.attempts': 'Tentative {count}',

  /* --- Ingestion : remplacement d'un document existant ----------------------- */
  'ingest.replace.confirmTitle': 'Remplacer des documents existants ?',
  'ingest.replace.confirmDescription':
    'Ces fichiers portent le même nom qu’un document déjà indexé dans cette base. L’ancienne version sera supprimée avant l’indexation de la nouvelle.',
  'ingest.replace.item': '« {incoming} » remplacera « {existing} »',
  'ingest.replace.confirm': 'Confirmer le remplacement',

  /* --- Ingestion : fichier déjà présent, reconnu à l'empreinte (ADR-014) ----- */
  'ingest.item.duplicateBadge': 'Déjà présent',
  'ingest.item.duplicateNote':
    'Contenu identique à « {name} », déjà versé dans cette base : aucun nouveau document n’a été créé et c’est l’existant qui est suivi ci-dessous. Le nom du fichier déposé n’apparaîtra donc pas dans la liste.',

  /* --- Ingestion : accepter les suggestions de precheck en lot --------------- */
  'ingest.bulkAccept.action': 'Appliquer les suggestions par défaut à tous',
  'ingest.bulkAccept.description':
    '{count} documents attendent votre validation, avec un réglage déjà proposé chacun — analyse de mise en page comprise sur les PDF, donc une indexation plus lente.',
  'ingest.bulkAccept.progress': 'Acceptation en cours… {settled}/{total}',
  'ingest.bulkAccept.successTitle': 'Suggestions acceptées',
  'ingest.bulkAccept.successDescription': '{count} document(s) ont démarré leur indexation.',
  'ingest.bulkAccept.partialFailureTitle': 'Acceptation partiellement échouée',
  'ingest.bulkAccept.partialFailureDescription':
    '{failed} document(s) sur {total} n’ont pas pu être acceptés automatiquement. Utilisez la validation individuelle pour ceux-ci.',

  // Pendant symétrique du précédent : soumet une décision **vide** pour tous
  // les documents en attente. « Refuser » porte sur les suggestions, pas sur
  // l'ingestion — les documents partent bien à l'indexation, sans les options.
  // D'où le texte d'aide, qui empêche de lire ce bouton comme une annulation.
  'ingest.bulkRefuse.action': 'Refuser les suggestions pour tous',
  'ingest.bulkRefuse.hint':
    'Lance l’indexation des documents en attente sans aucune des options proposées : plus rapide, mais les fragments perdent l’ordre du document. Cela n’annule pas l’ingestion — pour renoncer à un document, utilisez « Annuler l’ingestion » sur sa ligne.',
  'ingest.bulkRefuse.successTitle': 'Suggestions refusées',

  /* --- Ingestion : garde-fou qualité d'extraction (ADR-024) ------------------ */
  'ingest.qualityWarningTitle': 'Qualité à vérifier',
  'ingest.qualityWarningIntro':
    'L’indexation a réussi, mais l’extraction a produit un résultat suspect :',
  'ingest.qualityWarning.code.zero_chunks': 'Aucun fragment produit',
  'ingest.qualityWarning.code.degenerate_content': 'Fragments majoritairement vides',
  'ingest.qualityWarning.code.low_source_coverage': 'Perte de texte source',
  'ingest.qualityWarning.metric.chunk_count': 'Fragments produits',
  'ingest.qualityWarning.metric.degenerate_chunk_pct': 'Part de fragments dégénérés',
  'ingest.qualityWarning.metric.significant_char_total': 'Caractères significatifs au total',
  'ingest.qualityWarning.metric.source_coverage': 'Texte source retrouvé dans les fragments',
  'ingest.qualityWarning.advice.zero_chunks':
    'Le document est référencé mais rien n’en sera retrouvé : la page est vide, illisible, ou son texte n’a pas pu être extrait. Ouvrez le fichier pour vérifier, puis réindexez-le en cochant l’analyse de mise en page si son contenu est une image.',
  'ingest.qualityWarning.advice.degenerate_content':
    'La majorité des fragments ne contient presque aucun texte exploitable. Une réindexation avec l’analyse de mise en page donne souvent un meilleur résultat sur les documents numérisés.',
  'ingest.qualityWarning.advice.low_source_coverage':
    'Une part importante du texte du fichier ne se retrouve pas dans les fragments indexés : ces passages seront introuvables à la recherche. Vérifiez les fragments avant de vous appuyer sur ce document.',

  /* --- Ingestion : causes d'échec traduites en constats actionnables --------- */
  'ingest.cause.rawToggle': 'Message technique du moteur',
  'ingest.cause.quotaExhausted.title': 'Quota du service de description d’images épuisé',
  'ingest.cause.quotaExhausted.advice':
    'Le fournisseur d’IA qui décrit les images du document a refusé de nouvelles requêtes. Réessayez plus tard — le quota se recharge —, versez les documents un par un plutôt qu’en lot, ou configurez un fournisseur payant dans les réglages de la base.',
  'ingest.cause.unsupportedFormat.title': 'Format non pris en charge par le moteur',
  'ingest.cause.unsupportedFormat.advice':
    'Le moteur d’ingestion ne sait pas encore lire le format {format}. Convertissez le document dans un format pris en charge (PDF, DOCX, TXT, Markdown…) avant de le verser à nouveau.',
  'ingest.cause.unsupportedFormat.formatFallback': 'de ce fichier',
  'ingest.cause.unreadableImage.title': 'Fichier image illisible',
  'ingest.cause.unreadableImage.advice':
    'Le moteur n’a pas reconnu ce fichier comme une image exploitable : il est probablement tronqué, corrompu, ou dans une variante de format qu’il ne sait pas décoder. Ouvrez-le pour vérifier, puis versez-en une version ré-enregistrée.',

  /* --- Ingestion : machine à états du job ------------------------------------ */
  'job.progress.label': 'Progression de l’ingestion',
  'job.step.precheck': 'Analyse',
  'job.step.review': 'Validation',
  'job.step.chunking': 'Découpage',
  'job.step.embedding': 'Vectorisation',
  'job.step.indexing': 'Indexation',

  'job.state.queued': 'En file d’attente',
  'job.state.prechecking': 'Lecture du document',
  'job.state.awaiting_review': 'En attente de vous',
  'job.state.chunking': 'Découpage en passages',
  'job.state.embedding': 'Calcul des vecteurs',
  'job.state.indexing': 'Écriture dans l’index',
  'job.state.completed': 'Terminé',
  'job.state.failed': 'Interrompu',
  'job.state.cancelled': 'Annulé',

  'job.cancel.action': 'Annuler l’ingestion',
  'job.cancel.title': 'Annuler cette ingestion ?',
  'job.cancel.description': '« {name} » ne sera pas indexé. Cette action est définitive.',
  'job.cancel.confirm': 'Confirmer l’annulation',
  'job.cancel.errorTitle': 'L’annulation a échoué',

  /* --- Precheck : rapport de complexité --------------------------------------- */
  'precheck.analyzingTitle': 'Analyse du document',
  'precheck.analyzingDescription':
    '{appName} repère les tableaux, les pages numérisées et les langues avant de vous laisser décider.',
  'precheck.reportTitle': 'Complexité détectée',
  'precheck.reportDescription':
    'Ce que l’analyse préalable a relevé. Ce sont des indices, pas un verdict : vous pouvez les contredire.',
  'precheck.report.detailsToggle': 'Voir le détail de l’analyse',

  'precheck.format.pdf': 'PDF',
  'precheck.format.spreadsheet': 'Tableur',
  'precheck.format.presentation': 'Diaporama',
  'precheck.format.unsupported': 'Format non analysé',

  'precheck.metric.pages': 'Pages',
  'precheck.metric.pagesValue': '{count} page(s)',
  'precheck.metric.pagesAnalyzed': 'dont {count} analysée(s)',
  'precheck.metric.pagesUnknown': 'Non paginé',
  'precheck.metric.tables': 'Tableaux',
  // Portée du comptage quand l'analyse n'a pas couvert tout le document
  // (`MAX_ANALYZED_PAGES` côté backend) : « 3 tableaux » sur un document de
  // 194 pages veut dire « 3 sur les 24 premières », pas « 3 en tout ».
  'precheck.metric.tablesSampled': 'Tableaux — sur les {count} page(s) analysée(s)',
  'precheck.metric.tablesValue': '{count} tableau(x) repéré(s)',
  'precheck.metric.tablesNone': 'Aucun tableau repéré',
  'precheck.metric.columns': 'Mise en page',
  'precheck.metric.columnsValue': 'Colonnes côte à côte sur {count} page(s)',
  'precheck.metric.columnsNone': 'Texte sur une seule colonne',
  'precheck.metric.figures': 'Figures',
  'precheck.metric.figuresSampled': 'Figures — sur les {count} page(s) analysée(s)',
  // Présence/absence, jamais un chiffre : sur un PDF majoritairement scanné,
  // chaque page entière compte comme « une figure » (`figure_count`,
  // `backend/app/ingestion/precheck/schemas.py`) — techniquement correct au
  // sens du precheck, mais trompeur au sens intuitif d'« une image ».
  'precheck.metric.figuresDetected': 'Images détectées',
  'precheck.metric.figuresNone': 'Aucune image détectée',
  'precheck.metric.text': 'Texte extractible',
  'precheck.metric.textValue': '{count} caractères lus',
  // Sur un scan, la couche texte est légitimement vide : « 0 caractères lus »
  // à côté de « Document scanné détecté » se lit comme une panne alors que
  // c'est le cas nominal — on dit donc ce qui va se passer, pas un chiffre nul.
  'precheck.metric.textScanned': 'Texte à reconstituer par reconnaissance optique',
  'precheck.metric.textScannedDetail': 'Attendu sur un document scanné : la couche texte est vide.',
  'precheck.metric.scanned': 'Document scanné détecté',
  'precheck.metric.scannedRatio': '{percent} des pages analysées sont sans couche texte',
  'precheck.metric.scannedNone': 'Couche texte présente',
  'precheck.metric.scannedNoneDetail': 'Le texte est lisible sans reconnaissance optique.',
  'precheck.metric.languages': 'Langues détectées',
  'precheck.metric.languagesEmpty': 'Trop peu de texte pour trancher',

  'precheck.degradedTitle': 'Analyse préalable incomplète',
  'precheck.degradedDescription':
    'Les indices ci-dessous manquent : le moteur d’ingestion prendra le relais avec ses réglages hérités.',
  'precheck.error.unsupported_format':
    '{appName} ne sait pas analyser ce format en amont du moteur d’ingestion.',
  'precheck.error.corrupted_file':
    'Le contenu ne correspond pas au format annoncé, ou le fichier est vide.',
  'precheck.error.extraction_failed':
    'Le format est reconnu mais l’extraction a échoué : fichier tronqué ou protégé.',
  'precheck.error.unknown': 'L’analyse préalable n’a pas abouti.',

  /* --- Precheck : aiguillage -------------------------------------------------- */
  'precheck.decision.title': 'Comment indexer ce document ?',
  'precheck.decision.description':
    'Trois réglages possibles. Les deux premiers sont pré-cochés — l’un d’après l’analyse, l’autre parce que c’est le meilleur choix par défaut ; le dernier reste toujours à votre initiative.',
  'precheck.decision.tables': 'Optimiser pour les tableaux financiers',
  'precheck.decision.tablesHint':
    'Découpe le document ligne par ligne, adapté aux données en tableau. Une valeur courte comme « 03-04 » peut alors être lue comme une date.',
  'precheck.decision.ocr': 'Analyser la mise en page du document',
  'precheck.decision.ocrHint':
    'Respecte l’ordre réel des pages et ne coupe plus les phrases en deux — pour une recherche plus précise. Un peu plus lent à indexer.',
  'precheck.decision.parentChild': 'Affiner la granularité de recherche',
  'precheck.decision.parentChildHint':
    'Repère des passages très précis tout en gardant leur contexte complet dans les résultats. Génère davantage de fragments techniques en coulisses, sans changer ce que vous lisez.',
  'precheck.decision.ocrColumnsWarning':
    'Ce document a des colonnes côte à côte. L’analyse de mise en page les lit ligne par ligne en passant d’une colonne à l’autre, ce qui entremêle les phrases des deux colonnes ; sans elle, chaque colonne est lue d’un bloc. C’est pourquoi elle n’est pas proposée ici.',
  'precheck.decision.ocrColumnsWithTables':
    'Ce document a des colonnes côte à côte **et** des tableaux. Il reçoit pour cela une analyse de mise en page qui tient les deux : chaque colonne est lue d’un bloc, les tableaux restent reconnus, et chaque fragment garde sa page d’origine. Vous n’avez rien à arbitrer ici — le temps d’indexation reste celui de l’analyse habituelle.',
  'precheck.advanced.title': 'Paramètres avancés',
  'precheck.advanced.description':
    'Pour qui sait déjà comment ce document doit être découpé. Rien à ouvrir sinon : les réglages ci-dessus suffisent.',
  // Explique une absence plutôt que de la laisser muette : sans ce texte, le
  // panneau « Paramètres avancés » disparaît sans un mot, et rien ne permet
  // à l'utilisateur de savoir si c'est attendu ou un problème.
  'precheck.advanced.unavailable':
    'Aucun réglage fin de découpage n’est proposé pour ce document : son type ou sa mise en page ne s’y prête pas.',
  'precheck.advanced.chunkMethod': 'Méthode de découpage',
  'precheck.advanced.chunkMethodHint':
    'Par défaut, la méthode est choisie d’après l’analyse du document. Seules les méthodes mesurées comme utiles sur ce type de document sont proposées ici ; les autres ne le sont nulle part.',
  'precheck.advanced.method.automatic': 'Automatique (recommandé)',
  'precheck.advanced.method.naive': 'Paragraphes — le découpage habituel',
  'precheck.advanced.method.laws':
    'Articles numérotés — pour un texte réglementaire ou contractuel',
  'precheck.advanced.method.paper': 'Sections d’article — pour une publication scientifique',
  'precheck.advanced.method.manual': 'Sections de manuel — pour une notice ou une procédure',
  'precheck.advanced.method.qa': 'Questions / réponses — un fragment par paire',
  'precheck.decision.suggested': 'Suggéré par l’analyse',
  'precheck.decision.byDefault': 'Proposé par défaut',
  'precheck.decision.appliedChunkMethod': 'Découpage forcé',
  'precheck.decision.submit': 'Lancer l’indexation',
  'precheck.decision.errorTitle': 'La décision n’a pas pu être transmise',
  'precheck.decision.appliedTitle': 'Indexation lancée',
  'precheck.decision.appliedDescription': 'Le découpage de « {name} » a démarré.',
  'precheck.decision.applied': 'Réglages retenus',
  'precheck.decision.appliedTables': 'Découpage tabulaire',
  'precheck.decision.appliedOcr': 'Analyse de mise en page',
  'precheck.decision.appliedParentChild': 'Granularité affinée',
  'precheck.decision.appliedDefault': 'Réglages par défaut du moteur',

  /* --- Playground ------------------------------------------------------------- */
  'playground.eyebrow': 'Playground',
  'playground.open': 'Ouvrir le Playground',
  'playground.title': 'Interroger « {name} »',
  'playground.description':
    'Éprouvez le corpus : recherchez des passages bruts, ou faites répondre un modèle en citant ses sources.',
  'playground.backToBase': 'Retour à la base',
  'playground.noIndexed.title': 'Rien à interroger pour l’instant',
  'playground.noIndexed.description':
    'Aucun document indexé dans cette base. Déposez-en depuis sa fiche pour pouvoir la questionner ici.',
  'playground.mode.label': 'Mode d’interrogation',
  'playground.mode.search': 'Recherche',
  'playground.mode.chat': 'Chat',

  'playground.settings.title': 'Réglages du retrieval',
  'playground.settings.description':
    'Partagés par les deux modes : le chat s’appuie exactement sur la même recherche hybride que le mode Recherche.',
  'playground.settings.topK': 'Passages retenus',
  'playground.settings.topKHint': 'Nombre de fragments rendus par le moteur, entre 1 et 50.',
  'playground.settings.rerank': 'Reranking',
  'playground.settings.rerankHint':
    'Réordonne les passages avec le reranker de la base. Plus juste, plus lent.',
  'playground.settings.rerankUnavailable':
    'Cette base ne déclare aucun reranker : la façade refuserait la requête.',
  'playground.settings.threshold': 'Seuil de similarité',
  'playground.settings.thresholdHint':
    'Vide = seuil du moteur. Attention : activer le reranking change l’échelle du score, un seuil calé sans lui peut vider les résultats.',
  'playground.settings.thresholdPlaceholder': 'automatique',
  'playground.settings.documents': 'Restreindre le champ',
  'playground.settings.documentsAll': 'Tout le corpus',
  'playground.settings.documentsSelected': '{count} document(s)',
  'playground.settings.documentsEmpty': 'Aucun document indexé pour l’instant.',
  'playground.settings.documentsHint':
    'Seuls les documents indexés peuvent être interrogés. Sans sélection, tout le corpus est balayé.',
  'playground.settings.documentsTruncated':
    'Cette base compte plus de documents indexés que cet écran ne peut en lister ; seuls les {count} premiers sont proposés ici.',

  'playground.search.queryLabel': 'Votre requête',
  'playground.search.queryPlaceholder':
    'Quelles sont les conditions de résiliation anticipée du contrat-cadre ?',
  'playground.search.submit': 'Rechercher',
  'playground.search.resultsTitle': 'Passages retenus',
  'playground.search.resultsCount': '{count} passage(s)',
  'playground.search.emptyTitle': 'Posez votre première question',
  'playground.search.emptyDescription':
    'Les passages remontés apparaîtront ici, avec le détail des scores qui les ont fait sortir.',
  'playground.search.noResultsTitle': 'Aucun passage retenu',
  'playground.search.noResultsDescription':
    'Le moteur n’a rien trouvé au-dessus du seuil. Reformulez, augmentez le nombre de passages ou abaissez le seuil.',
  'playground.search.errorTitle': 'La recherche a échoué',

  'playground.result.rank': 'Passage {rank}',
  'playground.result.expand': 'Voir le passage entier',
  'playground.result.collapse': 'Réduire le passage',
  'playground.result.page': 'p. {page}',
  'playground.result.pageRange': 'p. {start}-{end}',
  'playground.result.pageLabel': 'Page {page} du document',
  'playground.result.pageRangeLabel': 'Pages {start} à {end} du document',
  'playground.score.combined': 'Combiné',
  'playground.score.vector': 'Vectoriel',
  'playground.score.term': 'Termes',
  'playground.score.legend':
    'Le score combiné arbitre entre proximité de sens (vectoriel) et présence des mots de la requête (termes).',

  'playground.chat.model': 'Modèle de génération',
  // Remplace l'un ou l'autre selon que le fournisseur sélectionné dispose
  // d'une clé personnelle déclarée (`settings.llmKeys`) : sans elle, ce choix
  // est silencieusement remplacé par un modèle du déploiement (addendum
  // 2026-09-21 à ADR-054, `resolve_llm_funding`).
  'playground.chat.modelAppliesHint': 'Clé personnelle {vendor} configurée : ce choix s’applique.',
  'playground.chat.modelOverriddenHint':
    'Sans clé personnelle {vendor}, un modèle du déploiement est utilisé à la place de ce choix.',
  'playground.chat.noProviderTitle': 'Aucun fournisseur de génération disponible',
  'playground.chat.noProviderDescription':
    'Ce déploiement ne dispose d’aucune clé d’API pour Anthropic ou OpenAI. La génération de réponses est externalisée par conception : sans clé configurée, le chat reste inactif.',
  'playground.chat.noProviderHint':
    'Le mode Recherche fonctionne, lui, entièrement en local : il n’a besoin d’aucun fournisseur externe.',
  'playground.chat.switchToSearch': 'Passer en mode Recherche',
  'playground.chat.emptyTitle': 'Demandez ce que vous cherchez',
  'playground.chat.emptyDescription':
    'La réponse s’appuiera uniquement sur les passages retrouvés dans ce corpus, chacun cité par son numéro.',
  'playground.chat.inputLabel': 'Votre question',
  'playground.chat.placeholder': 'Posez votre question sur ce corpus…',
  'playground.chat.send': 'Envoyer',
  'playground.chat.stop': 'Arrêter',
  'playground.chat.reset': 'Nouvelle conversation',
  'playground.chat.you': 'Vous',
  'playground.chat.assistant': 'Réponse',
  'playground.chat.retrieving': 'Recherche des passages…',
  'playground.chat.generating': 'Rédaction en cours…',
  'playground.chat.sources': 'Sources citées',
  'playground.chat.sourcesCount': '{count} passage(s) fournis au modèle',
  'playground.chat.sourceLabel': 'Aller à la source {index}',
  'playground.chat.errorTitle': 'Génération interrompue',
  'playground.chat.refusedTitle': 'Requête refusée',
  'playground.chat.emptyAnswer': 'Le modèle n’a produit aucun texte.',

  /* --- Paramètres : coquille commune -------------------------------------- */
  'settings.eyebrow': 'Paramètres',
  'settings.pageTitle': 'Paramètres du compte',
  'settings.tabsLabel': 'Sections des paramètres',
  'settings.tabs.apiKeys': 'Clés API',
  'settings.tabs.organizations': 'Organisations',
  'settings.tabs.llmKeys': 'Clés LLM personnelles',

  /* --- Paramètres : clés API -------------------------------------------------- */
  'settings.apiKeys.title': 'Vos clés API',
  'settings.apiKeys.description':
    'Chaque clé authentifie une intégration en votre nom. Le jeton en clair n’est visible qu’une seule fois, à la création.',
  'settings.apiKeys.create': 'Nouvelle clé',
  'settings.apiKeys.emptyTitle': 'Aucune clé API',
  'settings.apiKeys.emptyDescription':
    'Créez une clé pour authentifier vos scripts avec l’en-tête « Authorization: Bearer <clé> ».',

  'settings.apiKeys.created': 'Créée le {date}',
  'settings.apiKeys.lastUsed': 'Dernière utilisation le {date}',
  'settings.apiKeys.neverUsed': 'Jamais utilisée',
  'settings.apiKeys.expires': 'Expire le {date}',
  'settings.apiKeys.status.active': 'Active',
  'settings.apiKeys.status.revoked': 'Révoquée',

  'settings.apiKeys.revoke.action': 'Révoquer',
  'settings.apiKeys.revoke.title': 'Révoquer cette clé ?',
  'settings.apiKeys.revoke.description': '« {name} » ne pourra plus authentifier aucune requête.',
  'settings.apiKeys.revoke.warning':
    'Cette action est irréversible. Toute intégration utilisant cette clé perdra l’accès immédiatement.',
  'settings.apiKeys.revoke.confirm': 'Révoquer définitivement',
  'settings.apiKeys.revoke.successTitle': 'Clé révoquée',
  'settings.apiKeys.revoke.errorTitle': 'La révocation a échoué',

  'settings.apiKeys.create.title': 'Nouvelle clé API',
  'settings.apiKeys.create.description':
    'Le jeton complet ne sera affiché qu’une seule fois, juste après la création.',
  'settings.apiKeys.create.nameLabel': 'Nom',
  'settings.apiKeys.create.namePlaceholder': 'Intégration CI, script de synchronisation…',
  'settings.apiKeys.create.scopesLabel': 'Portées',
  'settings.apiKeys.create.scopeFull': 'Accès complet',
  'settings.apiKeys.create.scopeCustom': 'Portées personnalisées',
  'settings.apiKeys.create.scopeFullHint':
    'La clé peut appeler l’intégralité de l’API, comme votre compte.',
  'settings.apiKeys.create.scopeCustomHint':
    'Sélectionnez uniquement les portées dont cette intégration a besoin.',
  'settings.apiKeys.create.scopeCustomEmpty':
    'Sélectionnez au moins une portée, ou choisissez « Accès complet ».',
  'settings.apiKeys.create.submit': 'Créer la clé',
  'settings.apiKeys.create.errorTitle': 'La clé n’a pas pu être créée',
  'settings.apiKeys.create.revealTitle': 'Clé créée',
  'settings.apiKeys.create.revealDescription':
    'Copiez ce jeton maintenant : il ne sera plus jamais affiché.',
  'settings.apiKeys.create.revealWarning':
    'Une fois cette fenêtre fermée, ce jeton disparaît définitivement de l’interface — seule la révocation de la clé restera possible, jamais sa relecture.',
  'settings.apiKeys.create.copy': 'Copier',
  'settings.apiKeys.create.copied': 'Copié',
  'settings.apiKeys.create.copyErrorTitle':
    'Copie automatique impossible : sélectionnez et copiez le jeton manuellement.',
  'settings.apiKeys.create.done': 'J’ai copié la clé, terminer',

  'settings.apiKeys.scope.knowledgeBasesRead': 'Bases de connaissances — lecture',
  'settings.apiKeys.scope.knowledgeBasesWrite': 'Bases de connaissances — écriture',
  'settings.apiKeys.scope.documentsRead': 'Documents — lecture',
  'settings.apiKeys.scope.documentsWrite': 'Documents — écriture',
  'settings.apiKeys.scope.jobsRead': 'Jobs d’ingestion — lecture',
  'settings.apiKeys.scope.jobsWrite': 'Jobs d’ingestion — écriture',
  'settings.apiKeys.scope.retrievalSearch': 'Retrieval — recherche',
  'settings.apiKeys.scope.retrievalChat': 'Retrieval — chat',
  'settings.apiKeys.scope.evaluationsRead': 'Évaluations — lecture',
  'settings.apiKeys.scope.evaluationsWrite': 'Évaluations — écriture',
  'settings.apiKeys.scope.apiKeysRead': 'Clés API — lecture',
  'settings.apiKeys.scope.apiKeysWrite': 'Clés API — écriture',
  'settings.apiKeys.scope.organizationsRead': 'Organisations — lecture',
  'settings.apiKeys.scope.organizationsWrite': 'Organisations — écriture',
  'settings.apiKeys.scope.llmKeysRead': 'Clés LLM personnelles — lecture',
  'settings.apiKeys.scope.llmKeysWrite': 'Clés LLM personnelles — écriture',

  /* --- Paramètres : clés LLM personnelles ------------------------------------ */
  'settings.llmKeys.title': 'Vos clés LLM personnelles',
  'settings.llmKeys.description':
    'Utilisée en priorité sur la clé du service pour ce fournisseur, sur le chat de retrieval. La clé elle-même n’est jamais réaffichée après son enregistrement.',

  'settings.llmKeys.vendor.anthropic': 'Anthropic',
  'settings.llmKeys.vendor.openai': 'OpenAI',
  'settings.llmKeys.vendor.groq': 'Groq',

  'settings.llmKeys.status.active': 'Clé personnelle active',
  'settings.llmKeys.status.none': 'Aucune clé personnelle',
  'settings.llmKeys.updated': 'Enregistrée le {date}',

  'settings.llmKeys.add': 'Ajouter une clé',
  'settings.llmKeys.replace': 'Remplacer',
  'settings.llmKeys.keyLabel': 'Clé API',
  'settings.llmKeys.keyPlaceholder': 'Collez la clé du fournisseur',
  'settings.llmKeys.save': 'Enregistrer',

  'settings.llmKeys.set.successTitle': 'Clé enregistrée',
  'settings.llmKeys.set.errorTitle': 'L’enregistrement a échoué',

  'settings.llmKeys.remove.action': 'Retirer',
  'settings.llmKeys.remove.title': 'Retirer cette clé personnelle ?',
  'settings.llmKeys.remove.description':
    'Les appels au chat pour « {vendor} » reviendront à la clé du service, si elle est configurée.',
  'settings.llmKeys.remove.warning': 'Cette action est irréversible.',
  'settings.llmKeys.remove.confirm': 'Retirer définitivement',
  'settings.llmKeys.remove.successTitle': 'Clé retirée',
  'settings.llmKeys.remove.errorTitle': 'Le retrait a échoué',

  /* --- Paramètres : organisations ------------------------------------------- */
  'settings.organizations.title': 'Vos organisations',
  'settings.organizations.description':
    'Une organisation regroupe des comptes pour partager des bases de connaissances en équipe.',
  'settings.organizations.create': 'Nouvelle organisation',
  'settings.organizations.emptyTitle': 'Aucune organisation',
  'settings.organizations.emptyDescription':
    'Créez une organisation pour partager des bases de connaissances avec d’autres comptes.',
  'settings.organizations.role.owner': 'Propriétaire',
  'settings.organizations.role.member': 'Membre',
  'settings.organizations.backToList': 'Retour aux organisations',
  'settings.organizations.notFoundTitle': 'Organisation introuvable',
  'settings.organizations.notFoundDescription':
    'Cette organisation n’existe pas, ou vous n’en êtes pas membre.',
  'settings.organizations.errorTitle': 'Impossible de charger cette organisation',

  'settings.organizations.create.title': 'Nouvelle organisation',
  'settings.organizations.create.description':
    'Un identifiant unique (le « slug ») sera dérivé automatiquement du nom.',
  'settings.organizations.create.nameLabel': 'Nom',
  'settings.organizations.create.namePlaceholder': 'Équipe Data, Acme Corp…',
  'settings.organizations.create.submit': 'Créer l’organisation',
  'settings.organizations.create.successTitle': 'Organisation créée',
  'settings.organizations.create.successDescription': '« {name} » est prête.',
  'settings.organizations.create.errorTitle': 'L’organisation n’a pas pu être créée',

  'settings.organizations.detail.edit': 'Renommer',
  'settings.organizations.detail.delete': 'Supprimer',

  'settings.organizations.edit.submit': 'Enregistrer',
  'settings.organizations.edit.successTitle': 'Organisation renommée',
  'settings.organizations.edit.successDescription': 'Elle s’appelle désormais « {name} ».',
  'settings.organizations.edit.errorTitle': 'Le renommage a échoué',

  'settings.organizations.delete.title': 'Supprimer cette organisation ?',
  'settings.organizations.delete.description':
    '« {name} » et son annuaire de membres seront définitivement supprimés.',
  'settings.organizations.delete.warning':
    'Refusé tant que l’organisation possède encore des bases de connaissances — retirez-les ou transférez-les d’abord.',
  'settings.organizations.delete.confirm': 'Supprimer définitivement',
  'settings.organizations.delete.successTitle': 'Organisation supprimée',
  'settings.organizations.delete.errorTitle': 'La suppression a échoué',

  'settings.organizations.section.members': 'Accès',
  'settings.organizations.members.title': 'Membres',
  'settings.organizations.members.description':
    'Visible par tout membre. Ajouter, changer un rôle ou retirer un membre est réservé au propriétaire.',
  'settings.organizations.members.roleLabel': 'Rôle',
  'settings.organizations.members.addLabel': 'Identifiant de l’utilisateur',
  'settings.organizations.members.addHint': 'UUID du compte à ajouter.',
  'settings.organizations.members.add': 'Ajouter',
  'settings.organizations.members.notFound': 'Aucun compte ne correspond à cet identifiant.',
  'settings.organizations.members.grantedTitle': 'Rôle enregistré',
  'settings.organizations.members.grantedDescription': 'Le membre a désormais le rôle « {role} ».',
  'settings.organizations.members.removedTitle': 'Membre retiré',
  'settings.organizations.members.errorTitle': 'Action impossible',
  'settings.organizations.members.remove': 'Retirer',
  'settings.organizations.members.removeConfirmTitle': 'Retirer ce membre ?',
  'settings.organizations.members.removeConfirmDescription':
    'Il perdra immédiatement l’accès à l’organisation et à ses bases de connaissances.',
  'settings.organizations.members.removeConfirm': 'Retirer le membre',

  /* --- Santé de l'API -------------------------------------------------------- */
  'health.checking': 'Vérification en cours…',
  'health.online': 'API disponible',
  'health.offline': 'API injoignable',
  'health.version': 'Version {version}',

  'error.unexpected': 'Une erreur inattendue est survenue.',
  'error.network': 'Impossible de joindre le serveur.',
  'error.textTooLong': 'Ce texte est trop long : {max} caractères au maximum.',
  'error.conversationTooLong':
    'La conversation est devenue trop longue pour être envoyée. Recommencez-en une nouvelle.',
  'error.inputLimitReached': 'Limite de {max} caractères atteinte.',

  'notFound.title': 'Page introuvable',
  'notFound.description': 'L’adresse demandée ne correspond à aucun écran de {appName}.',
  'notFound.back': 'Retour aux bases de connaissances',

  'developers.eyebrow': 'Développeurs',
  'developers.title': 'Intégrer {appName} par l’API',
  'developers.description':
    'Tout ce qu’il faut pour ingérer des documents et interroger vos bases de connaissances depuis votre propre code. La référence exhaustive et toujours à jour de chaque endpoint vit dans Redoc — ce guide couvre l’essentiel pour démarrer.',
  'developers.referenceCta': 'Ouvrir la référence complète (Redoc)',

  'developers.auth.title': 'Authentification',
  'developers.auth.description':
    'Deux façons de vous identifier : un cookie de session (utilisé par l’interface web) ou une clé API dans l’en-tête Authorization, réservée aux comptes réels.',
  'developers.auth.apiKey': 'Authorization: Bearer rag_<votre_clé>',
  'developers.auth.apiKeyNote':
    'La clé n’est affichée qu’une seule fois à sa création — conservez-la immédiatement.',

  'developers.schemaA.title': 'Ingestion d’un document',
  'developers.schemaA.description':
    'Cinq appels, dans l’ordre : créer une base, envoyer le fichier, suivre le job jusqu’à ce qu’il attende votre décision, choisir comment le découper, puis suivre le job jusqu’à la fin.',
  'developers.schemaA.step1': 'Créer une base de connaissances',
  'developers.schemaA.step2':
    'Envoyer le document (multipart) — répond aussitôt, le traitement continue en tâche de fond',
  'developers.schemaA.step3': 'Suivre le job jusqu’à ce qu’il attende votre validation',
  'developers.schemaA.step4': 'Valider comment découper le document',
  'developers.schemaA.step5': 'Suivre le job jusqu’à « terminé »',

  'developers.schemaB.title': 'Interroger une base',
  'developers.schemaB.description':
    'Deux endpoints : une recherche classique qui renvoie les passages trouvés, ou une conversation qui génère une réponse à partir de ces mêmes passages, diffusée au fil de l’eau.',
  'developers.schemaB.search': 'Recherche — réponse JSON immédiate',
  'developers.schemaB.chat': 'Conversation — réponse générée, diffusée en flux',
  'developers.schemaB.filters':
    'Les résultats peuvent être filtrés par langue, type de fichier ou date d’ingestion.',

  'developers.errors.title': 'Erreurs',
  'developers.errors.description':
    'Toutes les erreurs suivent le même format (RFC 9457) : un type, un titre, un statut HTTP et un détail lisible. Une ressource qui existe mais qui vous est invisible répond « introuvable », jamais « refusé » — pour ne pas confirmer son existence.',

  'developers.providers.title': 'Fournisseurs disponibles',
  'developers.providers.description':
    'Avant de configurer une base, consultez le catalogue des modèles d’embedding, de reranking et de génération réellement disponibles dans cet environnement — accessible sans compte.',
} as const satisfies Record<string, string>;

/** Ensemble des clés de traduction disponibles. */
export type TranslationKey = keyof typeof fr;

/** Contrat que toute locale doit remplir intégralement. */
export type Dictionary = Record<TranslationKey, string>;
