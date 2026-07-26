/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";

/**
 * Valida una cadena de procesos de taller. FUNCIÓN PURA: no toca el DOM ni
 * servicios; recibe un snapshot plano y devuelve el resultado.
 *
 * @param {Object} chain
 * @param {{id: number, name: string}|null} chain.originProduct  producto de origen
 * @param {{id: number, name: string}|null} chain.soldProduct    producto vendido
 * @param {Array} chain.steps  pasos en orden. Cada paso:
 *   {
 *     key: string,                      // identificador de UI
 *     processId: number|null,
 *     processName: string,
 *     receiveProduct: {id, name}|null,  // lo que recibe (derivado)
 *     deliverProduct: {id, name}|null,  // lo que entrega (derivado)
 *   }
 * @returns {{status: "valid"|"warning"|"error", issues: Array<{level, stepKey, message}>}}
 */
export function validateWorkshopChain(chain) {
    const issues = [];
    const steps = (chain && chain.steps) || [];
    const origin = chain ? chain.originProduct : null;
    const sold = chain ? chain.soldProduct : null;

    if (!steps.length) {
        issues.push({
            level: "error",
            stepKey: null,
            message: _t("La cadena debe tener al menos el paso principal."),
        });
    }
    if (!origin) {
        issues.push({
            level: "error",
            stepKey: steps.length ? steps[0].key : null,
            message: _t("La línea no tiene producto de origen configurado."),
        });
    }
    if (!sold) {
        issues.push({
            level: "error",
            stepKey: null,
            message: _t("La línea no tiene producto vendido."),
        });
    }

    const seenPairs = new Map();

    steps.forEach((step, index) => {
        const position = index + 1;
        const isLast = index === steps.length - 1;

        // Paso incompleto: sin proceso.
        if (!step.processId) {
            issues.push({
                level: "error",
                stepKey: step.key,
                message: _t("El paso %s no tiene proceso definido.").replace("%s", position),
            });
        }

        // Recepción: el primer paso debe recibir el producto de origen.
        if (index === 0) {
            if (origin && step.receiveProduct && step.receiveProduct.id !== origin.id) {
                issues.push({
                    level: "error",
                    stepKey: step.key,
                    message: _t(
                        "El paso 1 debe recibir el producto de origen (%s)."
                    ).replace("%s", origin.name),
                });
            }
        } else {
            const prev = steps[index - 1];
            if (!step.receiveProduct) {
                issues.push({
                    level: "error",
                    stepKey: step.key,
                    message: _t(
                        "El paso %s no recibe ningún producto: define qué entrega el paso anterior."
                    ).replace("%s", position),
                });
            } else if (
                prev.deliverProduct &&
                step.receiveProduct.id !== prev.deliverProduct.id
            ) {
                // Entrega/recepción desincronizadas (datos cargados corruptos).
                issues.push({
                    level: "error",
                    stepKey: step.key,
                    message: _t(
                        "El paso %(pos)s recibe %(got)s pero el paso anterior entrega %(expected)s."
                    )
                        .replace("%(pos)s", position)
                        .replace("%(got)s", step.receiveProduct.name)
                        .replace("%(expected)s", prev.deliverProduct.name),
                });
            }
        }

        // Entrega: todo paso debe entregar algo; el último, el producto vendido.
        if (!step.deliverProduct) {
            issues.push({
                level: "error",
                stepKey: step.key,
                message: _t(
                    "El paso %s no define qué producto entrega."
                ).replace("%s", position),
            });
        } else if (isLast && sold && step.deliverProduct.id !== sold.id) {
            issues.push({
                level: "error",
                stepKey: step.key,
                message: _t(
                    "El último paso debe entregar el producto vendido (%s)."
                ).replace("%s", sold.name),
            });
        }

        // Posible duplicado: mismo proceso recibiendo el mismo producto.
        if (step.processId && step.receiveProduct) {
            const pairKey = `${step.processId}/${step.receiveProduct.id}`;
            if (seenPairs.has(pairKey)) {
                issues.push({
                    level: "warning",
                    stepKey: step.key,
                    message: _t(
                        "El paso %(pos)s parece duplicado del paso %(other)s (mismo proceso y mismo producto recibido)."
                    )
                        .replace("%(pos)s", position)
                        .replace("%(other)s", seenPairs.get(pairKey)),
                });
            } else {
                seenPairs.set(pairKey, position);
            }
        }
    });

    const status = issues.some((i) => i.level === "error")
        ? "error"
        : issues.some((i) => i.level === "warning")
          ? "warning"
          : "valid";

    return { status, issues };
}
