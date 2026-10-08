import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import { ToastViewport } from './ToastViewport';

export type ToastTone = 'success' | 'danger' | 'info';

export interface Toast {
  id: number;
  tone: ToastTone;
  title: string;
  description?: string;
}

export type ToastInput = Omit<Toast, 'id'>;

export interface ToastContextValue {
  /** Empile une notification et renvoie son identifiant. */
  notify: (toast: ToastInput) => number;
  dismiss: (id: number) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const DEFAULT_DURATION_MS = 6000;
const MAX_VISIBLE = 4;

/**
 * Gestionnaire de notifications.
 *
 * Les toasts sont éphémères et n'appartiennent à aucun écran : ils vivent
 * donc au-dessus du routeur, dans un contexte, et sont rendus une seule fois
 * dans un « viewport » commun (région ARIA `status`).
 */
export function ToastProvider({ children }: { children: ReactNode }): ReactNode {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number): void => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
    const timer = timers.current.get(id);
    if (timer !== undefined) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const notify = useCallback(
    (input: ToastInput): number => {
      const id = nextId.current++;
      // On borne la pile : au-delà, les plus anciennes sortent d'elles-mêmes.
      setToasts((current) => [...current, { ...input, id }].slice(-MAX_VISIBLE));
      timers.current.set(
        id,
        setTimeout(() => {
          dismiss(id);
        }, DEFAULT_DURATION_MS),
      );
      return id;
    },
    [dismiss],
  );

  const value = useMemo<ToastContextValue>(() => ({ notify, dismiss }), [notify, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

/** Émission d'une notification depuis n'importe quel écran. */
export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (context === null) {
    throw new Error('useToast() doit être utilisé à l’intérieur d’un <ToastProvider>.');
  }
  return context;
}
