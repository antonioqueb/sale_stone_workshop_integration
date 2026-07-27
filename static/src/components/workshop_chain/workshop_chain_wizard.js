/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { validateWorkshopChain } from "./workshop_chain_validation";

const LOCKED_OT_STATES = ["in_workshop", "done"];
const OT_BADGE_CLASSES = {
    draft: "text-bg-secondary",
    in_workshop: "text-bg-warning",
    done: "text-bg-success",
    cancel: "text-bg-danger",
};

let newStepCounter = 0;

/* -------------------------------------------------------------------------
 * Banda superior de validación (valid / warning / error)
 * ---------------------------------------------------------------------- */
export class WorkshopChainValidationBanner extends Component {
    static template = "sale_stone_workshop_integration.WorkshopChainValidationBanner";
    static props = { "*": true };

    get bannerData() {
        const { status, issues } = this.props.validation;
        if (status === "error") {
            return {
                cssClass: "o_sw_banner_error",
                icon: "fa-times-circle",
                title: _t("Cadena inválida"),
                description:
                    issues
                        .filter((i) => i.level === "error")
                        .map((i) => i.message)
                        .join(" · ") || _t("Corrige los pasos marcados."),
            };
        }
        if (status === "warning") {
            return {
                cssClass: "o_sw_banner_warning",
                icon: "fa-exclamation-triangle",
                title: _t("Revisa la cadena"),
                description: issues
                    .filter((i) => i.level === "warning")
                    .map((i) => i.message)
                    .join(" · "),
            };
        }
        return {
            cssClass: "o_sw_banner_valid",
            icon: "fa-check-circle",
            title: _t("Cadena válida"),
            description: this.props.summary,
        };
    }
}

/* -------------------------------------------------------------------------
 * Nodos del diagrama
 * ---------------------------------------------------------------------- */
export class ProductNode extends Component {
    static template = "sale_stone_workshop_integration.ProductNode";
    static props = { "*": true };
}

export class ProcessNode extends Component {
    static template = "sale_stone_workshop_integration.ProcessNode";
    static props = { "*": true };
}

/* -------------------------------------------------------------------------
 * Diagrama horizontal: origen → proceso 1 → intermedio → … → vendido
 * ---------------------------------------------------------------------- */
export class WorkshopChainDiagram extends Component {
    static template = "sale_stone_workshop_integration.WorkshopChainDiagram";
    static components = { ProductNode, ProcessNode };
    static props = { "*": true };

    /**
     * Secuencia alternada de nodos. El producto vendido SIEMPRE cierra el
     * flujo al final; no hay flecha después del último nodo (la flecha se
     * pinta ANTES de cada nodo excepto el primero).
     */
    get nodes() {
        const rows = this.props.rows;
        const line = this.props.line;
        const selectedKey = this.props.selectedKey;
        const nodes = [];

        nodes.push({
            nodeType: "product",
            kind: line.origin_product ? "origin" : "missing",
            tag: _t("Origen"),
            product: line.origin_product,
            highlighted: rows.length && rows[0].key === selectedKey,
        });

        rows.forEach((row, index) => {
            nodes.push({
                nodeType: "process",
                row,
                selected: row.key === selectedKey,
            });

            const isLast = index === rows.length - 1;
            nodes.push({
                nodeType: "product",
                kind: isLast
                    ? line.sold_product
                        ? "final"
                        : "missing"
                    : row.deliver
                      ? "intermediate"
                      : "missing",
                tag: isLast ? _t("Vendido") : _t("Intermedio"),
                product: isLast ? line.sold_product : row.deliver,
                highlighted:
                    row.key === selectedKey ||
                    (!isLast && rows[index + 1] && rows[index + 1].key === selectedKey),
            });
        });

        return nodes;
    }
}

/* -------------------------------------------------------------------------
 * Fila de la tabla de pasos
 * ---------------------------------------------------------------------- */
export class WorkshopChainStepRow extends Component {
    static template = "sale_stone_workshop_integration.WorkshopChainStepRow";
    static props = { "*": true };

    get row() {
        return this.props.row;
    }

    get otBadgeClass() {
        return OT_BADGE_CLASSES[this.row.ot && this.row.ot.state] || "text-bg-secondary";
    }

    get rowIssueText() {
        return this.row.issues.map((i) => i.message).join("\n");
    }

    onDragStart(ev) {
        if (!this.row.reorderable) {
            ev.preventDefault();
            return;
        }
        ev.dataTransfer.effectAllowed = "move";
        ev.dataTransfer.setData("text/plain", this.row.key);
        this.props.onDragStart(this.row.key);
    }

    onDragOver(ev) {
        if (this.props.draggingKey && this.row.reorderable) {
            ev.preventDefault();
        }
    }

    onDrop(ev) {
        ev.preventDefault();
        this.props.onDrop(this.row.key);
    }
}

/* -------------------------------------------------------------------------
 * Tabla unificada de pasos (principal + adicionales)
 * ---------------------------------------------------------------------- */
export class WorkshopChainStepsTable extends Component {
    static template = "sale_stone_workshop_integration.WorkshopChainStepsTable";
    static components = { WorkshopChainStepRow };
    static props = { "*": true };
}

/* -------------------------------------------------------------------------
 * Panel lateral de edición (380 px, dentro del mismo wizard)
 * ---------------------------------------------------------------------- */
export class WorkshopChainStepEditor extends Component {
    static template = "sale_stone_workshop_integration.WorkshopChainStepEditor";
    static props = { "*": true };

    setup() {
        this.notification = useService("notification");
        this.state = useState({
            productTerm: "",
            productOptions: [],
            searching: false,
        });
        this._searchTimeout = null;
    }

    get row() {
        return this.props.row;
    }

    onProcessChange(ev) {
        const processId = parseInt(ev.target.value, 10) || null;
        const process = this.props.processes.find((p) => p.id === processId);
        this.props.onChangeProcess(this.row.key, process || null);
    }

    onProductTermInput(ev) {
        const term = ev.target.value;
        this.state.productTerm = term;
        clearTimeout(this._searchTimeout);
        this._searchTimeout = setTimeout(() => this.searchProducts(term), 350);
    }

    async searchProducts(term) {
        this.state.searching = true;
        try {
            const results = await this.props.searchProducts(term);
            this.state.productOptions = results.map(([id, name]) => ({ id, name }));
        } catch (error) {
            // Sin esto el buscador moría en silencio y parecía que "no
            // dejaba" seleccionar el producto intermedio.
            this.state.productOptions = [];
            this.notification.add(
                _t("No se pudo buscar productos: %s").replace(
                    "%s",
                    (error && error.message) || error
                ),
                { type: "danger" }
            );
        } finally {
            this.state.searching = false;
        }
    }

    selectDeliverProduct(option) {
        this.props.onChangeDeliver(this.row.key, { id: option.id, name: option.name });
        this.state.productTerm = "";
        this.state.productOptions = [];
    }

    clearDeliverProduct() {
        this.props.onChangeDeliver(this.row.key, null);
    }
}

/* -------------------------------------------------------------------------
 * Footer sticky
 * ---------------------------------------------------------------------- */
export class WorkshopChainFooter extends Component {
    static template = "sale_stone_workshop_integration.WorkshopChainFooter";
    static props = { "*": true };

    get statusLabel() {
        const status = this.props.validation.status;
        if (status === "error") {
            return _t("Con errores");
        }
        if (status === "warning") {
            return _t("Con advertencias");
        }
        return _t("Válida");
    }
}

/* -------------------------------------------------------------------------
 * Wizard raíz: workspace de ancho completo
 * ---------------------------------------------------------------------- */
export class WorkshopChainWizard extends Component {
    static template = "sale_stone_workshop_integration.WorkshopChainWizard";
    static components = {
        WorkshopChainValidationBanner,
        WorkshopChainDiagram,
        WorkshopChainStepsTable,
        WorkshopChainStepEditor,
        WorkshopChainFooter,
    };
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.lineId =
            (this.props.action && this.props.action.params
                ? this.props.action.params.sale_line_id
                : null) || null;

        this.state = useState({
            loading: true,
            line: null,
            processes: [],
            // Cada paso: { key, plId, type, process:{id,name}|null,
            //             deliver:{id,name}|null  (solo pasos no finales),
            //             ot, locked, lockReason }
            steps: [],
            deletedIds: [],
            selectedKey: null,
            editorKey: null,
            draggingKey: null,
            dirty: false,
            saving: false,
        });

        onWillStart(() => this.loadData());
    }

    async loadData() {
        const data = await this.orm.call(
            "sale.order.line",
            "get_workshop_chain_workspace_data",
            [[this.lineId]]
        );
        this.state.line = data.line;
        this.state.processes = data.processes;

        const rawSteps = data.steps || [];
        this.state.steps = rawSteps.map((raw, index) => ({
            key: raw.pl_id ? `pl_${raw.pl_id}` : `main_${index}`,
            plId: raw.pl_id || 0,
            type: raw.type,
            process: raw.process || null,
            // "Entrega" del paso i = "Recibe" del paso i+1 en BD.
            // El último paso entrega el producto vendido (implícito).
            deliver:
                index + 1 < rawSteps.length ? rawSteps[index + 1].receive || null : null,
            ot: raw.ot || null,
            locked: !!raw.locked,
            lockReason: raw.lock_reason || "",
        }));
        this.state.loading = false;
    }

    /* ------------------------------------------------------------------
     * Modelo derivado
     * ------------------------------------------------------------------ */

    deliverOf(index) {
        const steps = this.state.steps;
        if (index === steps.length - 1) {
            return this.state.line.sold_product || null;
        }
        return steps[index].deliver || null;
    }

    receiveOf(index) {
        if (index === 0) {
            return this.state.line.origin_product || null;
        }
        return this.deliverOf(index - 1);
    }

    get rows() {
        const steps = this.state.steps;
        const lastLockedIndex = steps.reduce(
            (acc, step, index) => (step.locked ? index : acc),
            -1
        );

        const rows = steps.map((step, index) => {
            const isLast = index === steps.length - 1;
            const nextStep = steps[index + 1];
            const deliverLockedByNext = !!(nextStep && nextStep.locked);

            return {
                key: step.key,
                index,
                position: index + 1,
                type: step.type,
                plId: step.plId,
                processId: step.process ? step.process.id : null,
                processName: step.process ? step.process.name : "",
                receive: this.receiveOf(index),
                deliver: this.deliverOf(index),
                isLast,
                ot: step.ot,
                locked: step.locked,
                lockReason: step.lockReason,
                // Entrega editable: no es el último paso y el paso siguiente
                // (cuyo "Recibe" cambiaría) no tiene OT bloqueada.
                canEditDeliver: !isLast && !deliverLockedByNext,
                deliverLockReason: deliverLockedByNext ? nextStep.lockReason : "",
                canEdit: !step.locked,
                canDelete: step.type === "extra" && !step.locked,
                // Reordenable: paso adicional, sin OT bloqueada y posicionado
                // después del último paso bloqueado (mover algo antes de un
                // paso bloqueado cambiaría el material de una OT intocable).
                reorderable:
                    step.type === "extra" && !step.locked && index > lastLockedIndex,
                issues: [],
            };
        });

        // Anotar issues de validación por fila.
        const validation = this.validation;
        for (const issue of validation.issues) {
            const row = rows.find((r) => r.key === issue.stepKey);
            if (row) {
                row.issues.push(issue);
            }
        }
        return rows;
    }

    get validation() {
        const line = this.state.line;
        return validateWorkshopChain({
            originProduct: line.origin_product || null,
            soldProduct: line.sold_product || null,
            steps: this.state.steps.map((step, index) => ({
                key: step.key,
                processId: step.process ? step.process.id : null,
                processName: step.process ? step.process.name : "",
                receiveProduct: this.receiveOf(index),
                deliverProduct: this.deliverOf(index),
            })),
        });
    }

    get validationSummary() {
        const line = this.state.line;
        return _t("%(origin)s se transforma en %(sold)s en %(count)s paso(s) de taller.")
            .replace("%(origin)s", line.origin_product ? line.origin_product.name : "—")
            .replace("%(sold)s", line.sold_product ? line.sold_product.name : "—")
            .replace("%(count)s", this.state.steps.length);
    }

    get editorRow() {
        return this.rows.find((r) => r.key === this.state.editorKey) || null;
    }

    get otCount() {
        return this.state.steps.filter((s) => s.ot).length;
    }

    /* ------------------------------------------------------------------
     * Interacción
     * ------------------------------------------------------------------ */

    selectStep(key) {
        this.state.selectedKey = this.state.selectedKey === key ? null : key;
    }

    openEditor(key) {
        this.state.selectedKey = key;
        this.state.editorKey = key;
    }

    closeEditor() {
        this.state.editorKey = null;
    }

    addStep() {
        newStepCounter += 1;
        const key = `new_${newStepCounter}`;
        // El nuevo paso pasa a ser el ÚLTIMO: entrega el producto vendido.
        // El paso que antes era último ahora necesita definir su entrega
        // (producto intermedio); la validación lo marcará hasta definirlo.
        this.state.steps.push({
            key,
            plId: 0,
            type: "extra",
            process: null,
            deliver: null,
            ot: null,
            locked: false,
            lockReason: "",
        });
        this.state.dirty = true;
        this.openEditor(key);
    }

    deleteStep(key) {
        const index = this.state.steps.findIndex((s) => s.key === key);
        if (index < 0) {
            return;
        }
        const step = this.state.steps[index];
        if (step.locked || step.type !== "extra") {
            return;
        }
        if (step.plId) {
            this.state.deletedIds.push(step.plId);
        }
        this.state.steps.splice(index, 1);
        this.state.dirty = true;
        if (this.state.editorKey === key) {
            this.state.editorKey = null;
        }
        if (this.state.selectedKey === key) {
            this.state.selectedKey = null;
        }
    }

    changeProcess(key, process) {
        const step = this.state.steps.find((s) => s.key === key);
        if (step && !step.locked) {
            step.process = process;
            this.state.dirty = true;
        }
    }

    changeDeliver(key, product) {
        const index = this.state.steps.findIndex((s) => s.key === key);
        if (index < 0 || index === this.state.steps.length - 1) {
            return;
        }
        const nextStep = this.state.steps[index + 1];
        if (nextStep && nextStep.locked) {
            return;
        }
        this.state.steps[index].deliver = product;
        this.state.dirty = true;
    }

    onRowDragStart(key) {
        this.state.draggingKey = key;
    }

    onRowDrop(targetKey) {
        const fromKey = this.state.draggingKey;
        this.state.draggingKey = null;
        if (!fromKey || fromKey === targetKey) {
            return;
        }
        const steps = this.state.steps;
        const fromIndex = steps.findIndex((s) => s.key === fromKey);
        const toIndex = steps.findIndex((s) => s.key === targetKey);
        if (fromIndex < 0 || toIndex < 0) {
            return;
        }
        const fromRow = this.rows[fromIndex];
        const toRow = this.rows[toIndex];
        if (!fromRow.reorderable || !toRow.reorderable) {
            return;
        }
        const [moved] = steps.splice(fromIndex, 1);
        steps.splice(toIndex, 0, moved);
        this.state.dirty = true;
    }

    async searchProducts(term) {
        return await this.orm.call("product.product", "name_search", [], {
            name: term || "",
            // Odoo 19: name_search recibe "domain"; el kwarg "args" ya no existe.
            domain: [["tracking", "!=", "none"]],
            limit: 8,
        });
    }

    /* ------------------------------------------------------------------
     * Guardar / cancelar
     * ------------------------------------------------------------------ */

    get canSave() {
        return (
            !this.state.saving && this.state.dirty && this.validation.status !== "error"
        );
    }

    async saveChain() {
        if (!this.canSave) {
            return;
        }
        this.state.saving = true;
        try {
            const stepsPayload = this.state.steps.map((step, index) => ({
                id: step.plId || 0,
                type: step.type,
                process_id: step.process ? step.process.id : false,
                // "Recibe" del paso i (i>0) = "Entrega" del paso i-1.
                input_product_id:
                    index === 0
                        ? false
                        : (this.deliverOf(index - 1) || {}).id || false,
            }));

            await this.orm.call(
                "sale.order.line",
                "save_workshop_chain_from_workspace",
                [[this.lineId], { steps: stepsPayload, deleted_ids: this.state.deletedIds }]
            );

            this.notification.add(_t("Cadena de procesos guardada."), {
                type: "success",
            });
            await this.closeWorkspace(true);
        } finally {
            this.state.saving = false;
        }
    }

    async closeWorkspace(reload = false) {
        await this.actionService.doAction({ type: "ir.actions.act_window_close" });
        if (reload) {
            try {
                await this.actionService.doAction({
                    type: "ir.actions.client",
                    tag: "soft_reload",
                });
            } catch {
                // soft_reload no disponible: el usuario refresca manualmente.
            }
        }
    }

    discard() {
        this.closeWorkspace(false);
    }
}

registry
    .category("actions")
    .add("sale_stone_workshop_chain_workspace", WorkshopChainWizard);
