import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState, type SubmitEvent, type ReactNode } from 'react';

import { organizationsQueryOptions } from '@/api/organizations';
import { fieldErrorsOf, problemMessageOf } from '@/api/problem';
import { providersQueryOptions } from '@/api/providers';
import type { KnowledgeBaseRead, Visibility } from '@/api/schemas';
import { Button } from '@/components/Button';
import { Card, CardHeader } from '@/components/Card';
import { Checkbox } from '@/components/Checkbox';
import { Dropzone } from '@/components/Dropzone';
import { Field } from '@/components/Field';
import { Input } from '@/components/Input';
import { Select } from '@/components/Select';
import { Skeleton } from '@/components/Skeleton';
import { Textarea } from '@/components/Textarea';
import { StagedFileRow, type StagedFile } from '@/features/ingestion';
import { useT } from '@/i18n';
import { slugify } from '@/lib/format';

import styles from './KnowledgeBaseForm.module.css';

export interface KnowledgeBaseFormValues {
  name: string;
  slug: string;
  description: string;
  visibility: Visibility;
  /** Ignoré en édition (`KnowledgeBaseUpdate` n'a pas ce champ, immuable après création). */
  organization_id: string | null;
  embedding_model_id: string;
  reranker_model_id: string | null;
  observability_enabled: boolean;
  evaluation_enabled: boolean;
  provider_config: Record<string, unknown>;
}

export interface KnowledgeBaseFormProps {
  mode: 'create' | 'edit';
  initial?: KnowledgeBaseRead;
  submitting: boolean;
  error: unknown;
  submitLabel: string;
  onSubmit: (values: KnowledgeBaseFormValues) => void;
  onCancel: () => void;
  /**
   * Dépôt de documents avant même que la base existe (`mode: 'create'`
   * uniquement) — purement local, aucun appel réseau tant que ces fichiers
   * restent ici. `KnowledgeBaseCreatePage` porte l'état et les enchaîne après
   * la création ; ce formulaire ne fait qu'afficher la zone de dépôt et la
   * liste en attente. Omis (ou `mode: 'edit'`) : la section ne s'affiche pas.
   */
  stagedFiles?: StagedFile[];
  onStageFiles?: (files: File[]) => void;
  onUnstageFile?: (id: string) => void;
}

const NO_RERANKER = '';

/**
 * Formulaire de configuration d'une base.
 *
 * Périmètre strict : cet écran **configure la base** (identité, visibilité,
 * briques d'inférence). Il ne contient aucun champ de requête — la recherche
 * appartient au Playground (Phase 7), et les mélanger reviendrait à demander
 * une question à l'utilisateur au moment où il déclare un corpus.
 */
export function KnowledgeBaseForm({
  mode,
  initial,
  submitting,
  error,
  submitLabel,
  onSubmit,
  onCancel,
  stagedFiles,
  onStageFiles,
  onUnstageFile,
}: KnowledgeBaseFormProps): ReactNode {
  const t = useT();
  const { data: catalog, isPending: catalogPending } = useQuery(providersQueryOptions());
  // Sans objet en édition : l'organisation d'une base ne se réassigne pas
  // après création (`KnowledgeBaseUpdate` n'a pas ce champ) — inutile
  // d'interroger la liste, encore moins d'afficher un sélecteur muet.
  const { data: organizationPage, isPending: organizationsPending } = useQuery({
    ...organizationsQueryOptions(),
    enabled: mode === 'create',
  });

  const [name, setName] = useState(initial?.name ?? '');
  const [slug, setSlug] = useState(initial?.slug ?? '');
  // Tant que l'utilisateur n'a pas édité le slug lui-même, il suit le nom.
  const [slugTouched, setSlugTouched] = useState(mode === 'edit');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [visibility, setVisibility] = useState<Visibility>(initial?.visibility ?? 'private');
  const [embeddingModel, setEmbeddingModel] = useState(initial?.embedding_model_id ?? '');
  // `null` = l'utilisateur n'a pas encore tranché (on proposera le défaut du
  // déploiement) ; `''` = il a explicitement choisi « Aucun ». Les deux cas
  // sont distincts et ne doivent pas être confondus.
  const [rerankerModel, setRerankerModel] = useState<string | null>(
    mode === 'edit' ? (initial?.reranker_model_id ?? NO_RERANKER) : null,
  );
  const [organizationId, setOrganizationId] = useState<string | null>(null);
  const [observability, setObservability] = useState(initial?.observability_enabled ?? false);
  const [evaluation, setEvaluation] = useState(initial?.evaluation_enabled ?? false);
  const [providerConfig, setProviderConfig] = useState(() => {
    // `provider_config` est optionnel au schéma : absent ou vide, on n'écrit
    // pas « {} » dans la zone de saisie, qui doit rester visiblement vierge.
    const config = initial?.provider_config;
    if (config == null || Object.keys(config).length === 0) return '';
    return JSON.stringify(config, null, 2);
  });
  const [configError, setConfigError] = useState<string | null>(null);

  // Mémoïsés : sans cela, `?? []` fabrique un tableau neuf à chaque rendu et
  // toute dépendance qui l'observe se croit modifiée en permanence.
  const embeddings = useMemo(() => catalog?.embeddings ?? [], [catalog]);
  const rerankers = useMemo(() => catalog?.rerankers ?? [], [catalog]);
  const observabilityAvailable = catalog?.observability.available ?? false;

  // Défaut : la première brique réellement disponible du déploiement — on
  // n'impose pas un identifiant en dur qui pourrait ne pas être servi ici.
  const defaultEmbedding = useMemo(
    () => embeddings.find((provider) => provider.available)?.provider_id ?? '',
    [embeddings],
  );
  const effectiveEmbedding = embeddingModel === '' ? defaultEmbedding : embeddingModel;

  // Défaut de reranking : la brique locale servie par ce déploiement
  // (bge-reranker-v2-m3 dans la configuration de référence), plutôt que
  // « Aucun » — un corpus sans reranker perd en précision sans que
  // l'utilisateur ait choisi cette dégradation.
  const defaultReranker = useMemo(
    () =>
      rerankers.find((provider) => provider.available && provider.is_local)?.provider_id ??
      NO_RERANKER,
    [rerankers],
  );
  const effectiveReranker = rerankerModel ?? defaultReranker;

  const organizations = useMemo(() => organizationPage?.data ?? [], [organizationPage]);
  // Un compte fraîchement inscrit a toujours exactement une organisation
  // personnelle (`OrganizationService.create_personal`) : le cas courant ne
  // montre donc aucun sélecteur. Il n'apparaît que si le compte en a rejoint
  // ou créé d'autres — l'exception, pas la règle.
  const needsOrganizationChoice = mode === 'create' && organizations.length > 1;
  // La liste est triée du plus récent au plus ancien (convention serveur,
  // `created_at.desc()`) : la **plus ancienne** — celle créée à l'inscription
  // — est le choix par défaut le plus sûr, pas la plus récente qui peut être
  // une organisation créée en passant pour explorer la fonctionnalité.
  // Présélectionnée plutôt que de bloquer la soumission sur un choix vide :
  // un compte avec plusieurs organisations reste rare, forcer un clic dessus
  // à chaque création de base pour le cas courant serait de la friction
  // gratuite. Le champ reste visible et modifiable pour qui veut vraiment
  // choisir une autre organisation.
  const defaultOrganizationId = organizations.at(-1)?.id ?? null;
  const effectiveOrganizationId = organizationId ?? defaultOrganizationId;
  const hasNoOrganization =
    mode === 'create' && !organizationsPending && organizations.length === 0;

  const fieldErrors = fieldErrorsOf(error);
  const effectiveSlug = slugTouched ? slug : slugify(name);

  const errorBannerRef = useRef<HTMLDivElement>(null);

  // Le formulaire est long (identité, visibilité, briques, avancé) : sans
  // ceci, une erreur de soumission (ex. quota dépassé) reste hors champ tant
  // que l'utilisateur ne remonte pas manuellement en haut de la page.
  useEffect(() => {
    if (error === undefined || error === null) return;
    errorBannerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    errorBannerRef.current?.focus();
  }, [error]);

  const handleSubmit = (event: SubmitEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (submitting) return;
    if (hasNoOrganization) return;

    let parsedConfig: Record<string, unknown> = {};
    const trimmedConfig = providerConfig.trim();
    if (trimmedConfig.length > 0) {
      try {
        const parsed: unknown = JSON.parse(trimmedConfig);
        if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
          setConfigError(t('kb.form.configNotObject'));
          return;
        }
        parsedConfig = parsed as Record<string, unknown>;
      } catch {
        setConfigError(t('kb.form.configInvalid'));
        return;
      }
    }
    setConfigError(null);

    onSubmit({
      name: name.trim(),
      slug: effectiveSlug,
      description: description.trim(),
      visibility,
      organization_id: mode === 'create' ? effectiveOrganizationId : null,
      embedding_model_id: effectiveEmbedding,
      reranker_model_id: effectiveReranker === NO_RERANKER ? null : effectiveReranker,
      observability_enabled: observability,
      evaluation_enabled: evaluation,
      provider_config: parsedConfig,
    });
  };

  return (
    <form className={styles.form} onSubmit={handleSubmit} noValidate>
      {error !== undefined && error !== null && (
        <div ref={errorBannerRef} tabIndex={-1}>
          <Card tone="danger" role="alert">
            <p className={styles.errorTitle}>{t('kb.form.errorTitle')}</p>
            <p className={styles.errorDetail}>{problemMessageOf(error, t('error.unexpected'))}</p>
          </Card>
        </div>
      )}

      {/* --- Identité ---------------------------------------------------- */}
      <Card>
        <CardHeader
          eyebrow={t('kb.form.section.identity')}
          title={t('kb.form.identityTitle')}
          description={t('kb.form.identityDescription')}
        />
        <div className={styles.grid}>
          <Field label={t('kb.form.name')} error={fieldErrors.name} required>
            {(field) => (
              <Input
                {...field}
                name="name"
                required
                maxLength={200}
                value={name}
                placeholder={t('kb.form.namePlaceholder')}
                onChange={(event) => {
                  setName(event.target.value);
                }}
              />
            )}
          </Field>

          <Field label={t('kb.form.slug')} hint={t('kb.form.slugHint')} error={fieldErrors.slug}>
            {(field) => (
              <Input
                {...field}
                name="slug"
                mono
                maxLength={64}
                value={effectiveSlug}
                placeholder="ma-base"
                onChange={(event) => {
                  setSlugTouched(true);
                  setSlug(slugify(event.target.value));
                }}
              />
            )}
          </Field>

          <Field
            label={t('kb.form.description')}
            hint={t('kb.form.descriptionHint')}
            error={fieldErrors.description}
            optionalLabel={t('form.optional')}
            className={styles.full}
          >
            {(field) => (
              <Textarea
                {...field}
                name="description"
                maxLength={2000}
                value={description}
                placeholder={t('kb.form.descriptionPlaceholder')}
                onChange={(event) => {
                  setDescription(event.target.value);
                }}
              />
            )}
          </Field>

          {needsOrganizationChoice && (
            <Field
              label={t('kb.form.organization')}
              hint={t('kb.form.organizationHint')}
              error={fieldErrors.organization_id}
              required
              className={styles.full}
            >
              {(field) => (
                <Select
                  {...field}
                  name="organization_id"
                  required
                  value={effectiveOrganizationId ?? ''}
                  onChange={(event) => {
                    setOrganizationId(event.target.value);
                  }}
                >
                  {/* L'organisation présélectionnée s'affiche comme « Aucune », pas
                      sous son vrai nom : ce concept ne parle à personne hors d'un
                      compte multi-organisations, et le libellé générique évite
                      d'exposer un jargon que la grande majorité des utilisateurs
                      n'a jamais besoin de comprendre. Placée en tête de liste —
                      c'est le choix recommandé, pas un cas parmi d'autres. */}
                  {[...organizations]
                    .sort((a) => (a.id === defaultOrganizationId ? -1 : 0))
                    .map((organization) => (
                      <option key={organization.id} value={organization.id}>
                        {organization.id === defaultOrganizationId
                          ? t('kb.form.organizationDefault')
                          : organization.name}
                      </option>
                    ))}
                </Select>
              )}
            </Field>
          )}

          {hasNoOrganization && (
            <Card tone="danger" className={styles.full}>
              <p className={styles.errorDetail}>{t('kb.form.organizationMissing')}</p>
            </Card>
          )}

          <Field
            label={t('kb.form.visibility')}
            hint={
              visibility === 'public'
                ? t('kb.form.visibilityPublic')
                : t('kb.form.visibilityPrivate')
            }
            error={fieldErrors.visibility}
          >
            {(field) => (
              <Select
                {...field}
                name="visibility"
                value={visibility}
                onChange={(event) => {
                  setVisibility(event.target.value === 'public' ? 'public' : 'private');
                }}
              >
                <option value="private">{t('kb.visibility.private')}</option>
                <option value="public">{t('kb.visibility.public')}</option>
              </Select>
            )}
          </Field>
        </div>
      </Card>

      {/* --- Briques d'inférence ----------------------------------------- */}
      <Card>
        <CardHeader
          eyebrow={t('kb.form.section.providers')}
          title={t('kb.form.providersTitle')}
          description={t('kb.form.providersDescription')}
        />

        {catalogPending ? (
          <div className={styles.grid}>
            <Skeleton height="3rem" />
            <Skeleton height="3rem" />
          </div>
        ) : (
          <div className={styles.grid}>
            <Field
              label={t('kb.form.embedding')}
              hint={t('kb.form.embeddingHint')}
              error={fieldErrors.embedding_model_id}
              required
            >
              {(field) => (
                <Select
                  {...field}
                  name="embedding_model_id"
                  required
                  value={effectiveEmbedding}
                  disabled={mode === 'edit'}
                  onChange={(event) => {
                    setEmbeddingModel(event.target.value);
                  }}
                >
                  {embeddings.map((provider) => (
                    <option
                      key={provider.provider_id}
                      value={provider.provider_id}
                      disabled={!provider.available}
                    >
                      {provider.label}
                      {' · '}
                      {provider.dimensions}
                      {t('kb.form.dimensionsSuffix')}
                      {provider.is_local ? ` · ${t('kb.form.local')}` : ` · ${t('kb.form.remote')}`}
                      {provider.available ? '' : ` · ${t('kb.form.unavailable')}`}
                    </option>
                  ))}
                </Select>
              )}
            </Field>

            <Field
              label={t('kb.form.reranker')}
              hint={t('kb.form.rerankerHint')}
              error={fieldErrors.reranker_model_id}
              optionalLabel={t('form.optional')}
            >
              {(field) => (
                <Select
                  {...field}
                  name="reranker_model_id"
                  value={effectiveReranker}
                  onChange={(event) => {
                    setRerankerModel(event.target.value);
                  }}
                >
                  <option value={NO_RERANKER}>{t('kb.model.none')}</option>
                  {rerankers.map((provider) => (
                    <option
                      key={provider.provider_id}
                      value={provider.provider_id}
                      disabled={!provider.available}
                    >
                      {provider.label}
                      {provider.is_local ? ` · ${t('kb.form.local')}` : ` · ${t('kb.form.remote')}`}
                      {provider.available ? '' : ` · ${t('kb.form.unavailable')}`}
                    </option>
                  ))}
                </Select>
              )}
            </Field>

            {mode === 'edit' && <p className={styles.lockNote}>{t('kb.form.embeddingLocked')}</p>}
          </div>
        )}
      </Card>

      {/* --- Documents, déposés avant la création (facultatif) ------------- */}
      {mode === 'create' && (
        <Card>
          <CardHeader
            eyebrow={t('kb.form.section.documents')}
            title={t('kb.form.documentsTitle')}
            description={t('kb.form.documentsDescription')}
          />

          <Dropzone
            multiple
            className={styles.documentsDropzone}
            label={t('ingest.dropzone.label')}
            hint={t('ingest.dropzone.hint')}
            browseLabel={t('ingest.dropzone.browse')}
            disabled={submitting}
            onFiles={(files) => {
              onStageFiles?.(files);
            }}
          />

          {(stagedFiles ?? []).length > 0 && (
            <>
              <ul className={styles.stagedList} role="list">
                {(stagedFiles ?? []).map((item) => (
                  <StagedFileRow
                    key={item.id}
                    item={item}
                    // Aucun document existant à remplacer avant que la base
                    // n'existe — la détection de doublon n'a lieu que dans
                    // `UploadPanel`, une fois la base créée.
                    replaces={null}
                    onRemove={() => {
                      onUnstageFile?.(item.id);
                    }}
                  />
                ))}
              </ul>
              <p className={styles.documentsPendingHint}>{t('kb.form.documentsPendingHint')}</p>
            </>
          )}
        </Card>
      )}

      {/* --- Options avancées, repliées par défaut ------------------------ */}
      <Card>
        <details className={styles.advanced}>
          <summary className={styles.summary}>
            <span className={styles.summaryText}>
              <span className={styles.summaryTitle}>{t('kb.form.advancedTitle')}</span>
              <span className={styles.summaryHint}>{t('kb.form.advancedDescription')}</span>
            </span>
            <svg
              className={styles.chevron}
              viewBox="0 0 16 16"
              aria-hidden="true"
              focusable="false"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="m4 6.5 4 4 4-4" />
            </svg>
          </summary>

          <div className={styles.advancedBody}>
            <div className={styles.checks}>
              <Checkbox
                name="observability_enabled"
                label={t('kb.form.observability')}
                description={
                  observabilityAvailable
                    ? t('kb.form.observabilityHint')
                    : t('kb.form.observabilityUnavailable')
                }
                checked={observability}
                onChange={(event) => {
                  setObservability(event.target.checked);
                }}
              />
              <Checkbox
                name="evaluation_enabled"
                label={t('kb.form.evaluation')}
                description={t('kb.form.evaluationHint')}
                checked={evaluation}
                onChange={(event) => {
                  setEvaluation(event.target.checked);
                }}
              />
            </div>

            <Field
              label={t('kb.form.providerConfig')}
              hint={t('kb.form.providerConfigHint')}
              error={configError ?? fieldErrors.provider_config}
              optionalLabel={t('form.optional')}
            >
              {(field) => (
                <Textarea
                  {...field}
                  name="provider_config"
                  rows={6}
                  spellCheck={false}
                  className={styles.code}
                  value={providerConfig}
                  placeholder={'{\n  "chunk_size": 512\n}'}
                  onChange={(event) => {
                    setProviderConfig(event.target.value);
                  }}
                />
              )}
            </Field>
          </div>
        </details>
      </Card>

      <div className={styles.actions}>
        <Button type="button" variant="secondary" onClick={onCancel} disabled={submitting}>
          {t('form.cancel')}
        </Button>
        <Button type="submit" loading={submitting} disabled={hasNoOrganization}>
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}
