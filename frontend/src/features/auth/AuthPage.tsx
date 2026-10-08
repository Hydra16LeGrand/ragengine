import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, type SubmitEvent, type ReactNode } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';

import { authKeys, loginMutationOptions, registerMutationOptions } from '@/api/auth';
import { fieldErrorsOf, problemMessageOf } from '@/api/problem';
import { Button } from '@/components/Button';
import { Field } from '@/components/Field';
import { Input } from '@/components/Input';
import { useT } from '@/i18n';
import { cx } from '@/lib/cx';
import { pluginSlots } from '@/plugins';

import { useSession } from './SessionProvider';
import styles from './AuthPage.module.css';

type Mode = 'login' | 'register';

const MIN_PASSWORD_LENGTH = 8;

/**
 * Écran combiné connexion / inscription.
 *
 * Un seul écran et une bascule, pas deux routes : le visiteur qui se trompe de
 * mode ne perd ni sa saisie ni sa place dans le parcours, et la redirection
 * `?next=` reste valable dans les deux cas.
 */
export function AuthPage(): ReactNode {
  const t = useT();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const { canWrite, isLoading } = useSession();

  const [mode, setMode] = useState<Mode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [confirmPasswordTouched, setConfirmPasswordTouched] = useState(false);
  const [displayName, setDisplayName] = useState('');

  const nextPath = new URLSearchParams(location.search).get('next') ?? '/knowledge-bases';

  const login = useMutation(loginMutationOptions());
  const register = useMutation(registerMutationOptions());

  const pending = login.isPending || register.isPending;
  const error: unknown = register.error ?? login.error;
  const fieldErrors = fieldErrorsOf(error);
  const passwordsMismatch = mode === 'register' && password !== confirmPassword;
  const confirmPasswordError =
    confirmPasswordTouched && passwordsMismatch
      ? t('auth.field.confirmPasswordMismatch')
      : fieldErrors.confirm_password;

  // Déjà connecté : cet écran n'a plus d'objet.
  if (!isLoading && canWrite) return <Navigate to={nextPath} replace />;

  const switchMode = (next: Mode): void => {
    setMode(next);
    login.reset();
    register.reset();
    setConfirmPassword('');
    setConfirmPasswordTouched(false);
  };

  const onSubmit = (event: SubmitEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (pending) return;

    if (mode === 'register') {
      setConfirmPasswordTouched(true);
      if (passwordsMismatch) return;
    }

    const finish = async (): Promise<void> => {
      if (mode === 'register') {
        await register.mutateAsync({ email, password, display_name: displayName });
      }
      // Dans les deux modes on termine par une session : l'inscription seule
      // ne connecte pas, et redemander ses identifiants juste après les avoir
      // choisis serait une friction gratuite.
      await login.mutateAsync({ email, password });
      await queryClient.invalidateQueries({ queryKey: authKeys.me });
      void navigate(nextPath, { replace: true });
    };

    // Les erreurs sont déjà portées par l'état des mutations (bannière + champs).
    void finish().catch(() => undefined);
  };

  return (
    <div className={styles.page}>
      <div className={styles.panel}>
        <div className={styles.brand}>
          <svg
            className={styles.brandMark}
            viewBox="0 0 24 24"
            strokeLinecap="round"
            aria-hidden="true"
            focusable="false"
          >
            <rect x="3" y="3" width="18" height="18" rx="5" />
            <path d="M8 9.5h8M8 13h5.5M8 16.5h3" />
          </svg>
          <span className={styles.brandName}>{t('app.name')}</span>
        </div>

        <div className={styles.intro}>
          <h1 className={styles.title}>
            {mode === 'login' ? t('auth.login.title') : t('auth.register.title')}
          </h1>
          <p className={styles.subtitle}>
            {mode === 'login' ? t('auth.login.subtitle') : t('auth.register.subtitle')}
          </p>
        </div>

        <div className={styles.switch} role="tablist" aria-label={t('auth.modeLabel')}>
          {(['login', 'register'] as const).map((value) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={mode === value}
              className={cx(styles.switchOption, mode === value && styles.switchOptionActive)}
              onClick={() => {
                switchMode(value);
              }}
            >
              {value === 'login' ? t('auth.mode.login') : t('auth.mode.register')}
            </button>
          ))}
        </div>

        <form className={styles.form} onSubmit={onSubmit} noValidate>
          {error !== undefined && error !== null && (
            <p className={styles.banner} role="alert">
              {problemMessageOf(error, t('error.unexpected'))}
            </p>
          )}

          {mode === 'register' && (
            <Field label={t('auth.field.displayName')} error={fieldErrors.display_name} required>
              {(field) => (
                <Input
                  {...field}
                  name="display_name"
                  autoComplete="name"
                  required
                  value={displayName}
                  placeholder={t('auth.field.displayNamePlaceholder')}
                  onChange={(event) => {
                    setDisplayName(event.target.value);
                  }}
                />
              )}
            </Field>
          )}

          <Field label={t('auth.field.email')} error={fieldErrors.email} required>
            {(field) => (
              <Input
                {...field}
                type="email"
                name="email"
                autoComplete="email"
                required
                value={email}
                placeholder={t('auth.field.emailPlaceholder')}
                onChange={(event) => {
                  setEmail(event.target.value);
                }}
              />
            )}
          </Field>

          <Field
            label={t('auth.field.password')}
            error={fieldErrors.password}
            hint={mode === 'register' ? t('auth.field.passwordHint') : undefined}
            required
          >
            {(field) => (
              <Input
                {...field}
                type="password"
                name="password"
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                required
                minLength={mode === 'register' ? MIN_PASSWORD_LENGTH : undefined}
                value={password}
                onChange={(event) => {
                  setPassword(event.target.value);
                }}
              />
            )}
          </Field>

          {mode === 'register' && (
            <Field label={t('auth.field.confirmPassword')} error={confirmPasswordError} required>
              {(field) => (
                <Input
                  {...field}
                  type="password"
                  name="confirm_password"
                  autoComplete="new-password"
                  required
                  value={confirmPassword}
                  onChange={(event) => {
                    setConfirmPassword(event.target.value);
                  }}
                  onBlur={() => {
                    setConfirmPasswordTouched(true);
                  }}
                />
              )}
            </Field>
          )}

          <Button type="submit" loading={pending} className={styles.submit}>
            {mode === 'login' ? t('auth.submit.login') : t('auth.submit.register')}
          </Button>
        </form>

        {pluginSlots('AuthPageExtra').map((AuthPageExtra, index) => (
          <AuthPageExtra key={index} nextPath={nextPath} />
        ))}
      </div>
    </div>
  );
}
