/**
 * Requêtes DOM partagées par les tests d'interface.
 *
 * `Checkbox` place le libellé **et** son texte d'aide dans le même `<label>` :
 * le nom accessible de la case est donc la concaténation des deux, et une
 * requête par nom exact échouerait sur un texte d'aide qu'on ne veut surtout
 * pas recopier dans les assertions (ADR-032 : on n'épingle pas la copie). On
 * interroge donc par **préfixe** du nom accessible. `Field` (`optionalLabel`)
 * a exactement le même effet sur tout contrôle facultatif (« Facultatif »
 * concaténé au libellé) : même repli, généralisé à n'importe quel rôle.
 */
import { screen } from '@testing-library/react';

/** La case dont le libellé visible est `label`, typée pour lire `.checked`. */
export function checkboxLabelled(label: string): HTMLInputElement {
  return screen.getByRole<HTMLInputElement>('checkbox', {
    name: (accessibleName) => accessibleName.startsWith(label),
  });
}

/**
 * Contrôle de rôle `role` (`combobox`, `textbox`…) dont le libellé visible
 * commence par `label` — pour un champ `Field` marqué `optionalLabel`, dont
 * le nom accessible réel est `${label} ${optionalLabel}`. Rendu générique par
 * le rôle, pas par le type concret de l'élément : l'appelant caste (`as
 * HTMLSelectElement`…) s'il a besoin d'une propriété spécifique.
 */
export function fieldLabelled(
  role: Parameters<typeof screen.getByRole>[0],
  label: string,
): HTMLElement {
  return screen.getByRole(role, {
    name: (accessibleName) => accessibleName.startsWith(label),
  });
}
