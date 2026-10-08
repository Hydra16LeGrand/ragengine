# `knowledge-bases/`

Dashboard des bases de connaissances : liste, création, configuration (modèle d'embedding,
reranker, profil de chunking) et suivi du contenu indexé de chaque KB.

S'appuiera sur `/api/v1/knowledge-bases` et `/api/v1/providers`.

**Un document en échec dit pourquoi** (ADR-041). `DocumentsPanel` n'affichait qu'un badge
`failed` : la cause, pourtant enregistrée, n'était lisible que dans le suivi de la session de
versement en cours. Chaque document `failed` ou porteur d'un avertissement qualité gagne donc un
dépli « Voir la cause », qui relit `GET /api/v1/jobs/{current_job_id}` **au clic** — pas au rendu
de la liste : celle-ci continue de se rendre sans jointure sur les jobs, et un corpus de
59 documents ne déclenche aucune requête tant que personne n'ouvre un détail. Le contenu du dépli
est rendu par `features/ingestion/JobOutcome`, le même bloc que pendant l'ingestion.
