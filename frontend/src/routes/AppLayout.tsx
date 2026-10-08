import { useCallback, useEffect, useId, useRef, useState, type ReactNode, type Ref } from 'react';
import { Link, NavLink, Outlet } from 'react-router-dom';

import { Button } from '@/components/Button';
import { ThemeToggle } from '@/components/ThemeToggle';
import { useSession, type SessionStatus } from '@/features/auth';
import type { UserRead } from '@/api/schemas';
import { useI18n, useT, type Translate } from '@/i18n';
import { cx } from '@/lib/cx';
import { pluginNavEntries, pluginSlots } from '@/plugins';

import { HealthIndicator } from './HealthIndicator';
import styles from './AppLayout.module.css';

interface PrimaryNavLinksProps {
  t: Translate;
  isAuthenticated: boolean;
  isEphemeral: boolean;
  isAdmin: boolean;
  linkClassName: (isActive: boolean) => string;
  onNavigate?: () => void;
  firstLinkRef?: Ref<HTMLAnchorElement>;
}

/**
 * Les liens eux-mêmes, communs à la barre desktop et au tiroir mobile — une
 * seule liste à tenir à jour, deux mises en page différentes autour.
 * `onNavigate` referme le tiroir mobile au clic ; absent côté desktop, où il
 * n'y a rien à refermer.
 */
function PrimaryNavLinks({
  t,
  isAuthenticated,
  isEphemeral,
  isAdmin,
  linkClassName,
  onNavigate,
  firstLinkRef,
}: PrimaryNavLinksProps): ReactNode {
  const { locale } = useI18n();
  return (
    <>
      <NavLink
        ref={firstLinkRef}
        to="/knowledge-bases"
        className={({ isActive }) => linkClassName(isActive)}
        onClick={onNavigate}
      >
        {t('nav.knowledgeBases')}
      </NavLink>
      <NavLink
        to="/evaluations"
        className={({ isActive }) => linkClassName(isActive)}
        onClick={onNavigate}
      >
        {t('nav.evaluationsSummary')}
      </NavLink>
      <NavLink
        to="/developers"
        className={({ isActive }) => linkClassName(isActive)}
        onClick={onNavigate}
      >
        {t('nav.developers')}
      </NavLink>
      {/* Les paramètres (clés API, organisations) sont réservés aux
          comptes réels (RequireAccount) : pas la peine de proposer un lien
          qui mènerait à un écran bloquant. */}
      {isAuthenticated && (
        <NavLink
          to="/settings"
          className={({ isActive }) => linkClassName(isActive)}
          onClick={onNavigate}
        >
          {t('nav.settings')}
        </NavLink>
      )}
      {/* Entrées des greffons (ADR-069), après celles du cœur : chacune décide
          elle-même pour qui elle est visible. */}
      {pluginNavEntries()
        .filter((entry) => entry.isVisible({ isAuthenticated, isEphemeral, isAdmin }))
        .map((entry) => (
          <NavLink
            key={entry.to}
            to={entry.to}
            className={({ isActive }) => linkClassName(isActive)}
            onClick={onNavigate}
          >
            {entry.label(locale)}
          </NavLink>
        ))}
    </>
  );
}

interface AccountControlsProps {
  t: Translate;
  status: SessionStatus;
  user: UserRead | null;
  onLogout: () => void;
  loginClassName: string | undefined;
}

/**
 * Identité + connexion/déconnexion — communes à l'en-tête compact (desktop)
 * et au tiroir mobile, comme `PrimaryNavLinks`. Un intitulé complet
 * (« Se déconnecter ») à côté du logo et du sélecteur de thème ne tient pas
 * sous 48rem (constaté en conditions réelles sur téléphone, 2026-09-07) : ce
 * bloc bascule dans le tiroir à cette largeur plutôt que de déborder l'en-tête.
 */
function AccountControls({
  t,
  status,
  user,
  onLogout,
  loginClassName,
}: AccountControlsProps): ReactNode {
  if (status === 'anonymous') {
    return (
      <Link to="/login" className={loginClassName}>
        {t('nav.login')}
      </Link>
    );
  }

  if (status !== 'authenticated' && status !== 'ephemeral') return null;

  return (
    <div className={styles.account}>
      <span className={styles.identity}>
        <span className={styles.avatar} aria-hidden="true">
          {(user?.display_name ?? '?').trim().charAt(0).toUpperCase()}
        </span>
        <span className={styles.identityText}>
          <span className={styles.identityName}>{user?.display_name}</span>
          {pluginSlots('IdentityBadges').map((IdentityBadges, index) => (
            <IdentityBadges key={index} />
          ))}
        </span>
      </span>
      <Button variant="ghost" size="sm" onClick={onLogout}>
        {t('nav.logout')}
      </Button>
    </div>
  );
}

/** Icône hamburger/croix maison (24×24, trait courant) — même patron que `ThemeToggle`. */
function MenuIcon({ open }: { open: boolean }): ReactNode {
  return (
    <svg viewBox="0 0 24 24" strokeLinecap="round" aria-hidden="true" focusable="false">
      {open ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
    </svg>
  );
}

/** Coquille commune à tous les écrans : en-tête, contenu routé, pied de page. */
export function AppLayout(): ReactNode {
  const t = useT();
  const { status, user, isEphemeral, isAuthenticated, isAdmin, logout } = useSession();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuId = useId();
  const toggleRef = useRef<HTMLButtonElement | null>(null);
  const firstLinkRef = useRef<HTMLAnchorElement | null>(null);

  const closeMenu = useCallback(() => {
    setMenuOpen(false);
  }, []);

  // Fermeture au clavier (Échap) et retour du focus sur le bouton bascule —
  // sans ça, fermer le tiroir laisse le focus dans le vide (l'élément qui
  // l'avait disparaît du DOM au démontage conditionnel).
  useEffect(() => {
    if (!menuOpen) return undefined;
    firstLinkRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        closeMenu();
        toggleRef.current?.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [menuOpen, closeMenu]);

  const linkClassName = (isActive: boolean): string =>
    cx(styles.navLink, isActive && styles.navLinkActive);
  const mobileLinkClassName = (isActive: boolean): string =>
    cx(styles.mobileNavLink, isActive && styles.mobileNavLinkActive);

  return (
    <div className={styles.shell}>
      <a className={styles.skipLink} href="#main">
        {t('app.skipToContent')}
      </a>

      <header className={styles.header}>
        <div className={cx(styles.container, styles.headerInner)}>
          <Link to="/knowledge-bases" className={styles.brand}>
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
          </Link>

          <nav className={styles.nav} aria-label={t('nav.primaryLabel')}>
            <PrimaryNavLinks
              t={t}
              isAuthenticated={isAuthenticated}
              isEphemeral={isEphemeral}
              isAdmin={isAdmin}
              linkClassName={linkClassName}
            />
          </nav>

          <div className={styles.headerActions}>
            <ThemeToggle />

            {/* Sous 48rem, l'identité/connexion rejoint le tiroir (ci-dessous) :
                « Se déconnecter » en toutes lettres à côté du logo et du
                sélecteur de thème débordait l'en-tête sur téléphone. */}
            <span className={styles.headerAccount}>
              <AccountControls
                t={t}
                status={status}
                user={user}
                onLogout={() => {
                  void logout();
                }}
                loginClassName={styles.loginLink}
              />
            </span>

            <button
              ref={toggleRef}
              type="button"
              className={styles.menuToggle}
              aria-expanded={menuOpen}
              aria-controls={menuId}
              aria-label={t(menuOpen ? 'nav.menuClose' : 'nav.menuOpen')}
              onClick={() => {
                setMenuOpen((open) => !open);
              }}
            >
              <MenuIcon open={menuOpen} />
            </button>
          </div>
        </div>

        {menuOpen && (
          <div id={menuId} className={styles.mobileMenu}>
            <nav aria-label={t('nav.primaryLabel')} className={styles.mobileMenuNav}>
              <PrimaryNavLinks
                t={t}
                isAuthenticated={isAuthenticated}
                isEphemeral={isEphemeral}
                isAdmin={isAdmin}
                linkClassName={mobileLinkClassName}
                onNavigate={closeMenu}
                firstLinkRef={firstLinkRef}
              />
            </nav>

            {/* Identité/connexion : dupliquée depuis l'en-tête compact plutôt
                que déplacée, `.headerAccount` la cache déjà à cette largeur
                (`AppLayout.module.css`) — jamais les deux à l'écran ensemble. */}
            <div className={styles.mobileMenuAccount}>
              <AccountControls
                t={t}
                status={status}
                user={user}
                onLogout={() => {
                  closeMenu();
                  void logout();
                }}
                loginClassName={styles.mobileLoginLink}
              />
            </div>
          </div>
        )}
      </header>

      {pluginSlots('ShellBanner').map((ShellBanner, index) => (
        <ShellBanner key={index} />
      ))}

      <main id="main" className={styles.main}>
        <div className={styles.container}>
          <Outlet />
        </div>
      </main>

      <footer className={styles.footer}>
        <div className={cx(styles.container, styles.footerInner)}>
          <HealthIndicator />
          <span>
            © {new Date().getFullYear()} {t('app.name')}
          </span>
        </div>
      </footer>
    </div>
  );
}
